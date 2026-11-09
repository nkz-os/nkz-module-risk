"""Aviso de helada: una Alert por cadena de noches. Une la decisión pura (frost_spell) con Orion.

Cada Alert guarda su rango de noches en `evaluationData.spell` {start, last_night, stage};
un publish fallido no lo avanza. Una parcela puede tener varias Alert de helada activas.

Una evaluación sin dato (`no_data`) no publica ni resuelve: las Alert activas cuya última
noche esté por delante no caducan; pasada esa noche, TTL normal. Solo una evaluación con
las noches completas resuelve.

Resolver libera la deduplicación en notifications: una helada que vuelve a una Alert
resuelta vibra otra vez con el mismo id. Se acepta: peca de avisar.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Set

from app.alerts.contract import frost_alert_id
from app.alerts.plain import frost_body_key, round_tmin
from app.worker import alert_lifecycle as lifecycle
from app.worker import sources, weather_source
from app.worker.alert_publisher import publish_alert
from app.worker.frost_spell import (
    SEVERITIES, STAGES, STATUS_ACTIVE, STATUS_DISMISSED, STATUS_RESOLVED, URGENCY_VIBRATE,
    ChainDecision, ExistingSpell, FrostConfig, Night,
    config_from_model_config, decide_chains, keep_alive_ids,
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
# ...y si toca vibrar, cada estas (reintento de un push fallido); en cada pasada mientras
# alguna noche sea inminente (`ChainDecision.imminent`).
VIBRATE_RETRY_HOURS = 3


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


def _read_spell(info: Optional[Dict[str, Any]], alert_id: str) -> Optional[Dict[str, Any]]:
    """Rango guardado en la Alert {start, last_night, stage}; ausente o ilegible = None."""
    data = info.get("evaluation_data") if info else None
    raw = data.get("spell") if isinstance(data, dict) else None
    try:
        if not isinstance(raw, dict):
            raise ValueError("no spell")
        if raw["stage"] not in STAGES:
            raise ValueError("stage")
        return {
            "start": date.fromisoformat(raw["start"]),
            "last_night": date.fromisoformat(raw["last_night"]),
            "stage": raw["stage"],
        }
    except (KeyError, TypeError, ValueError) as e:
        logger.warning("frost alert %s has no readable spell state (%s)", alert_id, e)
        return None


_LIVE = (STATUS_ACTIVE, STATUS_DISMISSED, STATUS_RESOLVED)


def _read_dismissed_range(data: Dict[str, Any], alert_id: str) -> Optional[tuple]:
    """Rango descartado guardado [inicio, fin]; ausente = None, ilegible = None (WARNING)."""
    raw = data.get("dismissed_range")
    if raw is None:
        return None
    try:
        a, b = (date.fromisoformat(d) for d in raw)
        return (a, b)
    except (TypeError, ValueError) as e:
        logger.warning("frost alert %s has an unreadable dismissed range (%s)", alert_id, e)
        return None


def _read_announced(data: Dict[str, Any], alert_id: str) -> tuple:
    """Noches anunciadas guardadas; ausentes o ilegibles = nunca anunciada (WARNING)."""
    raw = data.get("announced", [])
    try:
        return tuple(sorted({date.fromisoformat(d) for d in raw}))
    except (TypeError, ValueError) as e:
        logger.warning("frost alert %s has unreadable announced nights (%s)", alert_id, e)
        return ()


def _frost_alerts_of(existing: Dict[str, Dict[str, Any]], entity_id: str) -> Dict[str, Dict[str, Any]]:
    return {
        aid: info for aid, info in existing.items()
        if info.get("alert_type") == ALERT_TYPE and info.get("ref_entity") == entity_id
    }


def _spells_of(mine: Dict[str, Dict[str, Any]]) -> List[ExistingSpell]:
    """Alert de helada de la parcela para la decisión. Sin rango legible = sin fecha.

    Las resueltas sin rango legible se ignoran (no casan con nada y no hay que cerrarlas).
    """
    out = []
    for aid, info in sorted(mine.items()):
        status = info.get("status")
        if status not in _LIVE:
            continue
        if status == STATUS_RESOLVED:
            data = info.get("evaluation_data")
            if not (isinstance(data, dict) and isinstance(data.get("spell"), dict)):
                continue
        spell = _read_spell(info, aid)
        if spell is None:
            if status == STATUS_RESOLVED:
                continue
            out.append(ExistingSpell(aid, status, None, None, "none"))
        else:
            data = info.get("evaluation_data") or {}
            peak = data.get("peak_severity")
            announced_severity = data.get("announced_severity")
            out.append(ExistingSpell(
                aid, status, spell["start"], spell["last_night"], spell["stage"],
                peak if peak in SEVERITIES else None,
                _read_announced(data, aid),
                announced_severity if announced_severity in SEVERITIES else None,
                _read_dismissed_range(data, aid),
            ))
    return out


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


def _publish_key(chain: ChainDecision, local_hour: int) -> Dict[str, Any]:
    """Lo que, si cambia, obliga a reescribir la Alert.

    ASSUMPTION: `text_form` se añade a la clave acordada: sin él, una Alert silenciada
    seguiría diciendo «esta noche» tras la medianoche hasta 6 h. Son 1-2 escrituras más al día.
    ASSUMPTION: también `last_night`: el rango guardado es lo que empareja la cadena en la
    siguiente pasada y lo que sostiene la histéresis; sin escribirlo, «actualizar last_night»
    no ocurriría hasta 6 h después. Solo cambia cuando la cadena crece o encoge por el final.
    """
    text = chain.text_night
    return {
        "stage": chain.stage,
        "severity": chain.severity,
        "urgency": chain.urgency,
        "text_night": text.day.isoformat(),
        "text_tmin_c": round_tmin(text.tmin_c),
        "text_form": frost_body_key(chain.text_lead, local_hour),
        "last_night": chain.last_night.isoformat(),
    }


def _should_publish(
    current: Optional[Dict[str, Any]], key: Dict[str, Any], chain: ChainDecision, now: datetime,
) -> bool:
    # Una Alert cerrada o inexistente no tiene publicación viva que conservar.
    if current is None or current.get("status") != STATUS_ACTIVE:
        return True
    stored = current.get("evaluation_data")
    if not isinstance(stored, dict) or stored.get("publish_key") != key:
        return True
    last = lifecycle._parse(current.get("observed_at"))
    if last is None or now - last >= timedelta(hours=REPUBLISH_HOURS):
        return True
    if key["urgency"] != URGENCY_VIBRATE:
        return False
    # Reintento de la vibración (notifications deduplica): cada pasada si es inminente.
    return chain.imminent or now - last >= timedelta(hours=VIBRATE_RETRY_HOURS)


def _evaluation_data(
    chain: ChainDecision, today: date, issued_at: Optional[str], local_hour: int,
    publish_key: Dict[str, Any],
) -> Dict[str, Any]:
    nxt, cold, text = chain.next_night, chain.coldest, chain.text_night
    return {
        "text_night": text.day.isoformat(),
        "text_tmin_c": text.tmin_c,
        "text_lead": chain.text_lead,
        "local_hour": local_hour,
        "publish_key": publish_key,
        "stage": chain.stage,
        "peak_severity": chain.severity,
        "announced": [d.isoformat() for d in chain.announced],
        "announced_severity": chain.announced_severity,
        "dismissed_range": (
            [d.isoformat() for d in chain.dismissed_range] if chain.dismissed_range else None
        ),
        "next_night": nxt.day.isoformat(),
        "next_tmin_c": nxt.tmin_c,
        "next_lead": (nxt.day - today).days,
        "coldest_night": cold.day.isoformat(),
        "coldest_tmin_c": cold.tmin_c,
        "nights": [_night_dict(n, today) for n in chain.nights],
        "model": "coldest_of_two",
        "local_today": today.isoformat(),
        "forecast_issued_at": issued_at,
        "spell": {
            "start": chain.start.isoformat(),
            "last_night": chain.last_night.isoformat(),
            "stage": chain.stage,
        },
    }


def _resolve(
    orion, tenant_id: str, entity_id: str, mine: Dict[str, Dict[str, Any]], aid: str,
    touched: Set[str], now: datetime,
) -> bool:
    """Cierra una Alert. Si falla, se registra y la siguiente pasada lo reintenta."""
    try:
        lifecycle.mark(orion, aid, STATUS_RESOLVED, now)
    except Exception as e:
        logger.error(
            "frost resolve failed tenant=%s parcel_id=%s alert=%s: %s",
            tenant_id, entity_id, aid, e,
        )
        return False
    if aid in mine:
        mine[aid]["status"] = STATUS_RESOLVED
    touched.add(aid)
    return True


# Resultado de la parcela con varias cadenas: `resolved` solo si no queda ninguna viva.
_OUTCOME_ORDER = (PUBLISHED, UNCHANGED, SKIPPED, RESOLVED, NONE)


def process_frost_parcel(
    orion, conn, settings, tenant_id: str, risk: Dict[str, Any], entity: Dict[str, Any],
    existing: Dict[str, Dict[str, Any]], touched: Set[str], now: datetime,
    breaker: Optional[FrostBreaker] = None,
) -> str:
    """Evalúa la parcela: publica una Alert por cadena y resuelve las que ya no casan.

    Devuelve published | unchanged | resolved | skipped | no_data | none. Si falla algún
    publish, sigue con las demás cadenas, no resuelve nada y lanza al final: el estado no
    avanza y la siguiente pasada reintenta.
    """
    entity_id = entity.get("id", "")
    mine = _frost_alerts_of(existing, entity_id)
    spells = _spells_of(mine)
    # La fecha UTC nunca va por delante de la local en Europa: si no hay fecha local, el
    # error está del lado de conservar el aviso unas horas más.
    utc_today = now.date()

    def no_data(reason: str, keep: tuple = None) -> str:
        logger.error("frost no_data tenant=%s parcel_id=%s reason=%s", tenant_id, entity_id, reason)
        touched.update(keep_alive_ids(spells, utc_today) if keep is None else keep)
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
        touched.update(keep_alive_ids(spells, utc_today))
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

    muted = _is_muted(conn, tenant_id, entity_id)
    ev = decide_chains(fetched.nights, spells, cfg, fetched.today, muted, fetched.local_hour)
    if ev.no_data:
        return no_data(ev.reason or "incomplete_nights", ev.keep_ids)
    touched.update(ev.keep_ids)

    outcomes: List[str] = []
    written: Set[str] = set()
    failed: List[str] = []
    # Primero las Alert nuevas o reabiertas; después, los cierres.
    for chain in ev.chains:
        aid = chain.alert_id or frost_alert_id(tenant_id, entity_id, chain.start)
        written.add(aid)
        touched.add(aid)  # un publish fallido no deja caducar la Alert existente
        if chain.skip:
            outcomes.append(SKIPPED)
            continue
        key = _publish_key(chain, fetched.local_hour)
        if not _should_publish(existing.get(aid), key, chain, now):
            outcomes.append(UNCHANGED)
            continue
        try:
            ok = publish_alert(
                orion_ld_url=settings.orion_ld_url,
                context_url=settings.context_url,
                tenant_id=tenant_id,
                entity_id=entity_id,
                entity_type=risk.get("target_sdm_type") or "AgriParcel",
                alert_type=ALERT_TYPE,
                category=risk.get("category") or "weather",
                severity=chain.severity,
                evaluation_data=_evaluation_data(
                    chain, fetched.today, fetched.issued_at, fetched.local_hour, key),
                alert_id=aid,
                urgency=chain.urgency,
            )
        except Exception as e:
            logger.error("frost publish failed tenant=%s parcel_id=%s alert=%s: %s",
                         tenant_id, entity_id, aid, e)
            ok = False
        if not ok:
            failed.append(aid)
            continue
        outcomes.append(PUBLISHED)
    if failed:
        raise RuntimeError(f"frost publish rejected for {', '.join(failed)}")

    for aid in ev.resolve_ids:
        if aid not in written and _resolve(orion, tenant_id, entity_id, mine, aid, touched, now):
            outcomes.append(RESOLVED)

    if ev.reason:
        logger.error("frost no_data tenant=%s parcel_id=%s reason=%s", tenant_id, entity_id, ev.reason)
        return NO_DATA
    for o in _OUTCOME_ORDER:
        if o in outcomes:
            return o
    return NONE
