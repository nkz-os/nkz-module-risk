"""Aviso de helada por noche: une la decisión pura (frost_spell) con Orion.

Una evaluación sin dato (`no_data`) no publica ni marca: el aviso sigue, y mientras su
última noche esté por delante tampoco caduca. Pasada esa noche, TTL normal. Solo una
evaluación con dato completo puede resolver.
El estado de la racha vive en `evaluationData.spell` de la propia Alert, así que
un publish fallido no lo avanza.

Resolver libera la deduplicación en notifications: una helada que vuelve tras el escalón
`none` vibra otra vez con el mismo id. Se acepta: peca de avisar.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Optional, Set

from app.alerts.contract import frost_alert_id
from app.alerts.plain import frost_body_key, round_tmin
from app.worker import alert_lifecycle as lifecycle
from app.worker import sources, weather_source
from app.worker.alert_publisher import publish_alert
from app.worker.frost_spell import (
    STAGE_NONE, STAGE_FAR, STAGE_NEAR, STAGE_SEVERE, URGENCY_VIBRATE,
    FrostConfig, FrostDecision, Night, SpellState,
    config_from_model_config, decide_frost, frost_urgency, should_reopen_dismissed,
)

logger = logging.getLogger(__name__)

ALERT_TYPE = "frost"
PUBLISHED = "published"
RESOLVED = "resolved"
SKIPPED = "skipped"
NO_DATA = "no_data"
NONE = "none"
UNCHANGED = "unchanged"

REASON_BREAKER_OPEN = "breaker_open"

# Fallos seguidos de weather-api (transporte o 5xx) que abren el freno de un tenant.
BREAKER_FAILURES = 3
# Sin cambios, una Alert activa se reescribe como mucho cada estas horas...
REPUBLISH_HOURS = 6
# ...salvo que toque vibrar con la noche a esta antelación o menos: reintento en cada pasada.
RETRY_MAX_LEAD = 1

_STAGES = {STAGE_NONE, STAGE_FAR, STAGE_NEAR, STAGE_SEVERE}


class FrostBreaker:
    """Freno de una pasada de un tenant: tras N caídas seguidas no se espera a weather-api.

    Una respuesta sin datos no cuenta: el servicio contestó, y puede ser cosa de esa
    parcela (sin geometría, por ejemplo). Apagar al resto por eso sería perder avisos.
    """

    def __init__(self) -> None:
        self.consecutive = 0
        self.skipped = 0

    @property
    def is_open(self) -> bool:
        return self.consecutive >= BREAKER_FAILURES

    def failure(self) -> None:
        self.consecutive += 1

    def success(self) -> None:
        self.consecutive = 0


def _read_spell(info: Optional[Dict[str, Any]], alert_id: str) -> Optional[SpellState]:
    """Estado de la racha guardado en la Alert; ausente o ilegible = None (WARNING)."""
    if info is None:
        return None
    data = info.get("evaluation_data")
    raw = data.get("spell") if isinstance(data, dict) else None
    try:
        if not isinstance(raw, dict):
            raise ValueError("no spell")
        if not isinstance(raw["vibrated"], bool) or not isinstance(raw["severe_vibrated"], bool):
            raise ValueError("flags")
        if raw["stage"] not in _STAGES:
            raise ValueError("stage")
        return SpellState(
            start=date.fromisoformat(raw["start"]),
            last_night=date.fromisoformat(raw["last_night"]),
            vibrated=raw["vibrated"],
            severe_vibrated=raw["severe_vibrated"],
            stage=raw["stage"],
        )
    except (KeyError, TypeError, ValueError) as e:
        logger.warning("frost alert %s has no readable spell state (%s)", alert_id, e)
        return None


def _frost_alerts_of(existing: Dict[str, Dict[str, Any]], entity_id: str) -> Dict[str, Dict[str, Any]]:
    return {
        aid: info for aid, info in existing.items()
        if info.get("alert_type") == ALERT_TYPE and info.get("ref_entity") == entity_id
    }


def _latest(alerts: Dict[str, Dict[str, Any]]) -> Optional[str]:
    """Id de la Alert con `observed_at` más reciente (sin fecha, la más antigua)."""
    floor = datetime.min.replace(tzinfo=timezone.utc)
    best: Optional[str] = None
    best_at = floor
    for aid, info in alerts.items():
        at = lifecycle._parse(info.get("observed_at")) or floor
        if best is None or at > best_at:
            best, best_at = aid, at
    return best


def _is_muted(conn, tenant_id: str, entity_id: str) -> bool:
    """Interruptor de vibración por parcela. Si falla la lectura, no se silencia.

    ASSUMPTION: `parcel_id` de risk.parcel_alert_settings es el URN de la parcela.
    """
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT vibrate FROM risk.parcel_alert_settings "
                "WHERE tenant_id = %s AND parcel_id = %s AND alert_type = %s",
                (tenant_id, entity_id, ALERT_TYPE),
            )
            row = cur.fetchone()
    except Exception as e:
        logger.error(
            "frost settings read failed tenant=%s parcel_id=%s: %s (vibration stays on)",
            tenant_id, entity_id, e,
        )
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    return row is not None and not row["vibrate"]


def _night_dict(n: Night, today: date) -> Dict[str, Any]:
    return {"day": n.day.isoformat(), "tmin_c": n.tmin_c, "lead": (n.day - today).days}


def _publish_key(decision: FrostDecision, urgency: str, local_hour: int) -> Dict[str, Any]:
    """Lo que, si cambia, obliga a reescribir la Alert.

    ASSUMPTION: `text_form` se añade a la clave acordada: sin él, una Alert silenciada
    seguiría diciendo «esta noche» tras la medianoche hasta 6 h. Son 1-2 escrituras más al día.
    """
    text = decision.text_night
    return {
        "stage": decision.stage,
        "severity": decision.severity,
        "urgency": urgency,
        "text_night": text.day.isoformat(),
        "text_tmin_c": round_tmin(text.tmin_c),
        "text_form": frost_body_key(decision.text_lead, local_hour),
    }


def _should_publish(
    current: Optional[Dict[str, Any]], key: Dict[str, Any], decision: FrostDecision, now: datetime,
) -> bool:
    # Una Alert cerrada o inexistente no tiene publicación viva que conservar.
    if current is None or current["status"] != "active":
        return True
    stored = current.get("evaluation_data")
    if not isinstance(stored, dict) or stored.get("publish_key") != key:
        return True
    last = lifecycle._parse(current.get("observed_at"))
    if last is None or now - last >= timedelta(hours=REPUBLISH_HOURS):
        return True
    return key["urgency"] == URGENCY_VIBRATE and decision.text_lead <= RETRY_MAX_LEAD


def _evaluation_data(
    decision: FrostDecision, today: date, issued_at: Optional[str], local_hour: int,
    publish_key: Dict[str, Any],
) -> Dict[str, Any]:
    nxt, cold, spell = decision.next_night, decision.coldest, decision.spell
    text = decision.text_night
    return {
        "text_night": text.day.isoformat(),
        "text_tmin_c": text.tmin_c,
        "text_lead": decision.text_lead,
        "local_hour": local_hour,
        "publish_key": publish_key,
        "stage": decision.stage,
        "next_night": nxt.day.isoformat(),
        "next_tmin_c": nxt.tmin_c,
        "next_lead": (nxt.day - today).days,
        "coldest_night": cold.day.isoformat(),
        "coldest_tmin_c": cold.tmin_c,
        "nights": [_night_dict(n, today) for n in decision.at_risk],
        "model": "coldest_of_two",
        "local_today": today.isoformat(),
        "forecast_issued_at": issued_at,
        "spell": {
            "start": spell.start.isoformat(),
            "last_night": spell.last_night.isoformat(),
            "vibrated": spell.vibrated,
            "severe_vibrated": spell.severe_vibrated,
            "stage": spell.stage,
        },
    }


def _keep_if_night_ahead(
    mine: Dict[str, Dict[str, Any]], today: date, touched: Set[str],
) -> None:
    """Sin dato, el último aviso activo no caduca mientras su racha siga por delante."""
    active = {aid: info for aid, info in mine.items() if info["status"] == "active"}
    latest = _latest(active)
    if latest is None:
        return
    spell = _read_spell(active[latest], latest)
    if spell is not None and spell.last_night >= today:
        touched.add(latest)


def _resolve_others(
    orion, tenant_id: str, entity_id: str, mine: Dict[str, Dict[str, Any]], aid: str,
    touched: Set[str], now: datetime,
) -> None:
    """Una sola Alert activa por parcela: la de otra racha o la sin fecha se cierran.

    Siempre DESPUÉS de publicar la nueva. Si cerrar falla, se registra y se reintenta en
    la siguiente pasada; no deshace el aviso nuevo.
    """
    for other, info in mine.items():
        if other == aid or info["status"] != "active":
            continue
        try:
            lifecycle.mark(orion, other, "resolved", now)
        except Exception as e:
            logger.error(
                "frost resolve of old alert failed tenant=%s parcel_id=%s alert=%s: %s",
                tenant_id, entity_id, other, e,
            )
            continue
        info["status"] = "resolved"
        touched.add(other)


def process_frost_parcel(
    orion, conn, settings, tenant_id: str, risk: Dict[str, Any], entity: Dict[str, Any],
    existing: Dict[str, Dict[str, Any]], touched: Set[str], now: datetime,
    breaker: Optional[FrostBreaker] = None,
) -> str:
    """Evalúa y publica/resuelve el aviso de helada de una parcela.

    Devuelve published | unchanged | resolved | skipped | no_data | none. Lanza si falla
    una escritura en Orion: el estado no avanza y la siguiente pasada reintenta.
    """
    entity_id = entity.get("id", "")
    mine = _frost_alerts_of(existing, entity_id)
    # La fecha UTC nunca va por delante de la local en Europa: si no hay fecha local, el
    # error está del lado de conservar el aviso unas horas más.
    utc_today = now.date()

    def no_data(reason: str, today: date = utc_today) -> str:
        logger.error("frost no_data tenant=%s parcel_id=%s reason=%s", tenant_id, entity_id, reason)
        _keep_if_night_ahead(mine, today, touched)
        return NO_DATA

    weather_parcel = weather_source.resolve_parcel_id(entity)
    if not weather_parcel:
        return no_data("no_parcel")

    cfg: Optional[FrostConfig] = config_from_model_config(risk.get("model_config") or {})
    if cfg is None:
        return no_data("unconfigured_catalog")

    breaker = breaker if breaker is not None else FrostBreaker()
    if breaker.is_open:
        # Sin registro por parcela: el total sale en una línea al acabar la pasada.
        breaker.skipped += 1
        _keep_if_night_ahead(mine, utc_today, touched)
        return NO_DATA

    try:
        fetched = sources.fetch_frost_nights(
            settings.weather_api_url, tenant_id, weather_parcel, cfg.horizon_nights
        )
    except sources.WeatherOutage as e:
        breaker.failure()
        logger.warning("frost weather outage tenant=%s parcel_id=%s: %s", tenant_id, entity_id, e)
        return no_data("weather_unavailable")
    breaker.success()
    if fetched is None:
        return no_data("weather_unavailable")

    prev_id = _latest(mine)
    prev = _read_spell(mine[prev_id], prev_id) if prev_id else None
    muted = _is_muted(conn, tenant_id, entity_id)

    decision = decide_frost(fetched.nights, prev, cfg, fetched.today, muted, fetched.local_hour)
    if decision.no_data:
        return no_data(decision.no_data_reason or "incomplete_nights", fetched.today)

    if decision.stage == STAGE_NONE:
        # Dato completo y sin riesgo: se cierra el aviso; el estado de la racha
        # queda en la Alert resuelta por si vuelve el frío dentro del hueco.
        resolved = False
        for aid, info in mine.items():
            if info["status"] in ("active", "dismissed"):
                lifecycle.mark(orion, aid, "resolved", now)
                info["status"] = "resolved"
                touched.add(aid)
                resolved = True
        return RESOLVED if resolved else NONE

    aid = frost_alert_id(tenant_id, entity_id, decision.spell.start)

    current = existing.get(aid)
    if current is not None and current["status"] == "dismissed":
        shown = _read_spell(current, aid)
        if not should_reopen_dismissed(shown.stage if shown else STAGE_NONE, decision.stage):
            touched.add(aid)  # no caduca mientras siga la condición
            return SKIPPED

    touched.add(aid)  # un publish fallido no deja caducar el aviso existente

    urgency = frost_urgency(decision, muted, fetched.local_hour)
    key = _publish_key(decision, urgency, fetched.local_hour)
    if not _should_publish(current, key, decision, now):
        _resolve_others(orion, tenant_id, entity_id, mine, aid, touched, now)
        return UNCHANGED

    ok = publish_alert(
        orion_ld_url=settings.orion_ld_url,
        context_url=settings.context_url,
        tenant_id=tenant_id,
        entity_id=entity_id,
        entity_type=risk.get("target_sdm_type") or "AgriParcel",
        alert_type=ALERT_TYPE,
        category=risk.get("category") or "weather",
        severity=decision.severity,
        evaluation_data=_evaluation_data(
            decision, fetched.today, fetched.issued_at, fetched.local_hour, key),
        alert_id=aid,
        urgency=urgency,
    )
    if not ok:
        raise RuntimeError(f"frost publish rejected for {aid}")

    _resolve_others(orion, tenant_id, entity_id, mine, aid, touched, now)
    return PUBLISHED
