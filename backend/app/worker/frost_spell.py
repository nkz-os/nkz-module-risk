"""Decisión pura del aviso de helada: noches en riesgo, cadenas de noches y una Alert por cadena.

Sin E/S ni registro. El worker aporta las noches (mínima prevista del día local D
= noche de D-1 a D), las Alert de helada ya existentes de la parcela (su rango de noches),
y la fecha y la hora locales.

Modelo:
- Cadena: noches en riesgo ordenadas; dos seguidas van en la misma cadena si las separan
  `gap_nights` días o menos.
- Una Alert por cadena. Su id nace de la primera noche de la cadena al crearla y no cambia.
- Una cadena se empareja con la Alert cuyo rango [start, last_night] queda a `gap_nights`
  días o menos de la suya.
- La entrega la deduplica notifications por (id, severidad): una vez por Alert y severidad.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional, Sequence

STAGE_NONE = "none"
STAGE_FAR = "far"
STAGE_NEAR = "near"
STAGE_SEVERE = "severe"

_STAGE_RANK = {STAGE_NONE: 0, STAGE_FAR: 1, STAGE_NEAR: 2, STAGE_SEVERE: 3}
STAGES = frozenset(_STAGE_RANK)
_VIBRATE_STAGES = (STAGE_NEAR, STAGE_SEVERE)

_SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}
SEVERITIES = frozenset(_SEVERITY_RANK)

URGENCY_VIBRATE = "vibrate"
URGENCY_SILENT = "silent"

STATUS_ACTIVE = "active"
STATUS_DISMISSED = "dismissed"
STATUS_RESOLVED = "resolved"

REASON_INCOMPLETE = "incomplete_nights"
REASON_PARTIAL = "partial"

# La mínima de la noche en curso cae al amanecer: hasta esta hora local esa noche sigue viva.
RUNNING_NIGHT_UNTIL_HOUR = 10

# Horas de silencio, en hora local. El owner puede cambiarlas.
QUIET_START = 21
QUIET_END = 7
# Dentro de las horas de silencio solo vibra lo inminente: la noche en curso, o la de
# mañana (lead 1) a partir de esta hora.
IMMINENT_LEAD_1_FROM_HOUR = 18

# Histéresis: una noche dentro del rango de una Alert viva sigue en riesgo hasta que su
# mínima supere el umbral en este margen. Así no parpadea con cada pasada del modelo.
RELEASE_MARGIN_C = 0.5


@dataclass(frozen=True)
class Night:
    day: date  # noche de day-1 a day
    tmin_c: Optional[float]
    kind: str  # closed | running | forecast


@dataclass(frozen=True)
class FrostConfig:
    thresholds_by_lead: tuple[tuple[int, float], ...]
    near_nights: int
    horizon_nights: int
    moderate_c: float = -2.0
    severe_c: float = -5.0
    gap_nights: int = 2


@dataclass(frozen=True)
class ExistingSpell:
    """Una Alert de helada ya guardada. Sin `start` es una Alert sin fecha (anterior a las cadenas)."""
    alert_id: str
    status: str  # active | dismissed | resolved
    start: Optional[date]
    last_night: Optional[date]
    stage: str  # último escalón publicado
    peak_severity: Optional[str] = None  # severidad más alta publicada con este id
    announced: tuple[date, ...] = ()  # noches de las publicaciones que notifications empuja
    announced_severity: Optional[str] = None  # severidad del último anuncio
    # Rango que el usuario descartó, si la Alert se reabrió por noches fuera de él.
    dismissed_range: Optional[tuple[date, date]] = None


@dataclass(frozen=True)
class ChainDecision:
    alert_id: Optional[str]  # Alert emparejada; None = Alert nueva
    status: Optional[str]    # estado de la Alert emparejada
    start: date              # fija el id: el de la Alert emparejada, o la primera noche
    last_night: date
    stage: str               # far | near | severe
    severity: str            # publicada: nunca por debajo del pico de la Alert
    urgency: str
    nights: tuple[Night, ...]  # noches que disparan, por fecha
    next_night: Night
    coldest: Night
    text_night: Night
    text_lead: int
    imminent: bool           # alguna noche inminente: lead 0, o lead 1 desde las 18:00
    announced: tuple[date, ...]  # noches anunciadas tras esta publicación
    announced_severity: Optional[str]
    dismissed_range: Optional[tuple[date, date]]
    skip: bool               # descartada y el escalón no ha subido


@dataclass(frozen=True)
class FrostEvaluation:
    no_data: bool
    reason: Optional[str]
    chains: tuple[ChainDecision, ...] = ()
    resolve_ids: tuple[str, ...] = ()
    keep_ids: tuple[str, ...] = ()


def config_from_model_config(model_config: dict) -> Optional[FrostConfig]:
    """Lee la configuración de helada; sin tabla de umbrales, antelación o horizonte, None.

    Claves: thresholds_by_lead [{"max_lead": noches, "tmin_c": grados}, ...] (también
    se aceptan pares [noches, grados]), near_nights, horizon_nights, y opcionales moderate_threshold, severe_threshold, gap_nights.
    No hay valores por defecto para los umbrales: sin tabla no hay aviso anticipado.
    """
    if not isinstance(model_config, dict):
        return None
    try:
        raw = model_config.get("thresholds_by_lead")
        if not raw or not isinstance(raw, (list, tuple)):
            return None
        parsed = []
        for r in raw:
            if isinstance(r, dict):
                lead, thr = r["max_lead"], r["tmin_c"]
            else:
                lead, thr = r[0], r[1]
            if isinstance(lead, bool) or isinstance(thr, bool):
                return None
            lead, thr = int(lead), float(thr)
            if not math.isfinite(thr):
                return None
            parsed.append((lead, thr))
        rows = tuple(sorted(parsed))
        near = int(model_config["near_nights"])
        horizon = int(model_config["horizon_nights"])
        moderate = float(model_config.get("moderate_threshold", -2.0))
        severe = float(model_config.get("severe_threshold", -5.0))
        gap = int(model_config.get("gap_nights", 2))
    except (KeyError, TypeError, ValueError, IndexError):
        return None
    if near < 1 or horizon < near or gap < 0:
        return None
    return FrostConfig(rows, near, horizon, moderate, severe, gap)


def _threshold_for_lead(cfg: FrostConfig, lead: int) -> Optional[float]:
    for max_lead, thr in cfg.thresholds_by_lead:
        if lead <= max_lead:
            return thr
    return None


def should_reopen_dismissed(dismissed_stage: str, stage: str) -> bool:
    """Una Alert descartada solo se reabre cuando el escalón sube."""
    return _STAGE_RANK.get(stage, 0) > _STAGE_RANK.get(dismissed_stage, 0)


def _finite(v: Optional[float]) -> Optional[float]:
    """Un nulo o un valor no finito (NaN, inf) cuenta como sin dato."""
    if v is None or not math.isfinite(v):
        return None
    return v


def first_trigger_lead(local_hour: int) -> int:
    """Antes de RUNNING_NIGHT_UNTIL_HOUR la noche en curso (lead 0) aún dispara."""
    return 0 if local_hour < RUNNING_NIGHT_UNTIL_HOUR else 1


def is_imminent(lead: int, local_hour: int) -> bool:
    return lead == 0 or (lead == 1 and local_hour >= IMMINENT_LEAD_1_FROM_HOUR)


def _quiet(local_hour: int) -> bool:
    return local_hour >= QUIET_START or local_hour < QUIET_END


def _by_lead(nights: Sequence[Night], today: date) -> dict[int, Night]:
    """Una noche por antelación. Duplicados: gana el valor más frío; un nulo nunca pisa un valor."""
    out: dict[int, Night] = {}
    for n in nights:
        lead = (n.day - today).days
        cur = out.get(lead)
        if cur is None or _finite(cur.tmin_c) is None or (
            _finite(n.tmin_c) is not None and n.tmin_c < cur.tmin_c
        ):
            out[lead] = n
    return out


def _dated(s: ExistingSpell) -> bool:
    return s.start is not None and s.last_night is not None


def _span(s: ExistingSpell) -> tuple[date, date]:
    return min(s.start, s.last_night), max(s.start, s.last_night)


def at_risk_nights(
    by_lead: dict[int, Night], existing: Sequence[ExistingSpell], cfg: FrostConfig,
) -> tuple[Night, ...]:
    """Noches en riesgo, de lead 0 al horizonte, por fecha.

    Lead 0 usa el umbral de la primera fila. Una noche dentro del rango de una Alert
    activa o descartada sigue en riesgo hasta umbral + RELEASE_MARGIN_C.
    """
    held = [_span(s) for s in existing
            if _dated(s) and s.status in (STATUS_ACTIVE, STATUS_DISMISSED)]
    out = []
    for lead in sorted(by_lead):
        n = by_lead[lead]
        if lead < 0 or lead > cfg.horizon_nights or _finite(n.tmin_c) is None:
            continue
        thr = _threshold_for_lead(cfg, lead)
        if thr is None:
            continue
        if any(a <= n.day <= b for a, b in held):
            thr += RELEASE_MARGIN_C
        if n.tmin_c <= thr:
            out.append(n)
    return tuple(out)


def build_chains(at_risk: Sequence[Night], gap_nights: int) -> list[tuple[Night, ...]]:
    """Agrupa las noches en riesgo: dos seguidas a gap_nights días o menos, misma cadena."""
    chains: list[list[Night]] = []
    for n in sorted(at_risk, key=lambda n: n.day):
        if chains and (n.day - chains[-1][-1].day).days <= gap_nights:
            chains[-1].append(n)
        else:
            chains.append([n])
    return [tuple(c) for c in chains]


def _matches(s: ExistingSpell, chain: tuple[Night, ...], gap: int, today: date) -> bool:
    if not _dated(s) or s.status not in (STATUS_ACTIVE, STATUS_DISMISSED, STATUS_RESOLVED):
        return False
    a, b = _span(s)
    if s.status == STATUS_RESOLVED and b + timedelta(days=gap) < today:
        return False
    return (chain[0].day - b).days <= gap and (a - chain[-1].day).days <= gap


def _may_keep(s: ExistingSpell, chain: tuple[Night, ...], cfg: FrostConfig, today: date,
              first: int) -> bool:
    """Una Alert ya anunciada solo se queda con la cadena si esta aún contiene una noche
    anunciada y ninguna de sus noches cercanas es anterior a la primera anunciada que
    queda por delante.

    Si no, el push ya enviado nombra otra noche: la cadena abre Alert nueva (id nuevo,
    push nuevo) y la vieja se resuelve. Una Alert nunca anunciada casa como siempre.
    ASSUMPTION: la regla del brief decía «contiene una noche anunciada, o ninguna noche
    cercana es anterior»; con «o», el oráculo por noche encuentra heladas sin push (una
    noche anterior que se suma a la cadena, o el frío que se va a una noche posterior).
    """
    if not s.announced:
        return True
    if not any(n.day in s.announced for n in chain):
        return False
    ahead = [d for d in s.announced if d > today or (d == today and first == 0)]
    if not ahead:
        return True
    earliest = min(ahead)
    return all(
        n.day >= earliest for n in chain
        if first <= (n.day - today).days <= cfg.near_nights
    )


def match_chains(
    chains: Sequence[tuple[Night, ...]], existing: Sequence[ExistingSpell], cfg: FrostConfig,
    today: date, local_hour: int,
) -> tuple[list[Optional[ExistingSpell]], list[ExistingSpell]]:
    """Empareja cada cadena con una Alert existente, o con ninguna (Alert nueva).

    Por orden de cadena: de las Alert que casan, nadie ha tomado y pueden quedarse con la
    cadena (`_may_keep`), la de `start` más temprano la toma; las demás que casan con esa
    cadena quedan fundidas (se resuelven, sin push). Devuelve la Alert de cada cadena y
    las fundidas.
    """
    first = first_trigger_lead(local_hour)
    taken: set[str] = set()
    merged: list[ExistingSpell] = []
    out: list[Optional[ExistingSpell]] = []
    for chain in chains:
        cands = sorted(
            (s for s in existing
             if s.alert_id not in taken and _matches(s, chain, cfg.gap_nights, today)
             and _may_keep(s, chain, cfg, today, first)),
            key=lambda s: (s.start, s.alert_id),
        )
        out.append(cands[0] if cands else None)
        for s in cands:
            taken.add(s.alert_id)
        merged.extend(cands[1:])
    return out, merged


def chain_urgency(stage: str, muted: bool, local_hour: int, imminent: bool) -> str:
    """`vibrate` si el escalón es cercano o severo, salvo silenciada o en horas de silencio.

    En horas de silencio vibra solo si alguna noche de la cadena es inminente. Se repite
    en cada publicación: notifications deduplica por (id, severidad) y solo anota lo
    enviado, así que reintenta un push fallido y no vuelve a vibrar tras el éxito.
    """
    if muted or stage not in _VIBRATE_STAGES:
        return URGENCY_SILENT
    if not _quiet(local_hour) or imminent:
        return URGENCY_VIBRATE
    return URGENCY_SILENT


def _decide_chain(
    chain: tuple[Night, ...], spell: Optional[ExistingSpell], cfg: FrostConfig, today: date,
    muted: bool, local_hour: int, used_starts: frozenset = frozenset(),
) -> ChainDecision:
    first = first_trigger_lead(local_hour)

    def lead(n: Night) -> int:
        return (n.day - today).days

    triggers = tuple(n for n in chain if lead(n) >= first)
    if any(lead(n) <= cfg.near_nights and n.tmin_c <= cfg.severe_c for n in triggers):
        stage = STAGE_SEVERE
    elif any(lead(n) <= cfg.near_nights for n in triggers):
        stage = STAGE_NEAR
    else:
        stage = STAGE_FAR
    coldest = min(triggers, key=lambda n: n.tmin_c)
    if stage == STAGE_SEVERE:
        severity = "critical"
    elif stage == STAGE_NEAR:
        severity = "high"
    else:
        severity = "medium" if coldest.tmin_c <= 0.0 else "low"

    # La severidad publicada nunca baja mientras viva el id: notifications guarda una sola
    # severidad por canal, y bajar de critical a high volvería a hacer vibrar.
    if spell is not None and spell.peak_severity in _SEVERITY_RANK:
        severity = max(severity, spell.peak_severity, key=_SEVERITY_RANK.__getitem__)

    # Descartada: se salta mientras todas las noches de la cadena caigan en su rango y el
    # escalón no suba. Si hay noches fuera, se trata como no descartada, y el texto y la
    # inminencia salen de esas noches, como para una cadena nueva.
    # Reabierta por esas noches, la Alert recuerda el rango descartado y el texto sigue
    # nombrando solo noches fuera de él mientras las haya.
    pool = triggers
    skip = False
    dismissed_range = (
        spell.dismissed_range if spell is not None and spell.status == STATUS_ACTIVE else None
    )
    if spell is not None and spell.status == STATUS_DISMISSED and _dated(spell) \
            and not should_reopen_dismissed(spell.stage, stage):
        a, b = _span(spell)
        outside = tuple(n for n in triggers if not a <= n.day <= b)
        skip = not outside
        pool = outside or triggers
        if outside:
            dismissed_range = (a, b)
    elif dismissed_range is not None:
        a, b = dismissed_range
        pool = tuple(n for n in triggers if not a <= n.day <= b) or triggers

    imminent = [n for n in pool if is_imminent(lead(n), local_hour)]
    severe = [n for n in pool if lead(n) <= cfg.near_nights and n.tmin_c <= cfg.severe_c]
    if imminent:
        text = imminent[0]
    elif stage == STAGE_SEVERE and severe:
        text = severe[0]
    else:
        text = pool[0]

    urgency = chain_urgency(stage, muted, local_hour, bool(imminent))
    # ASSUMPTION: anuncio = una publicación que notifications empuja: vibrate con id nuevo,
    # con la Alert no activa (su deduplicación se liberó) o con una severidad por encima de
    # la del último anuncio. El brief decía «cada publicación con vibrate», pero las
    # republicaciones deduplicadas no llegan al teléfono: una noche que se suma después
    # quedaba «anunciada» sin que ningún push la nombrara.
    announced = spell.announced if spell is not None else ()
    announced_severity = spell.announced_severity if spell is not None else None
    if spell is not None and spell.status != STATUS_ACTIVE:
        # Cerrada o descartada: notifications liberó su deduplicación; la próxima
        # publicación con vibrate empuja aunque esta salga en silencio.
        announced_severity = None
    pushes = urgency == URGENCY_VIBRATE and (
        spell is None or spell.status != STATUS_ACTIVE or not announced
        or announced_severity not in _SEVERITY_RANK
        or _SEVERITY_RANK[severity] > _SEVERITY_RANK[announced_severity]
    )
    trigger_days = {n.day for n in triggers}
    if urgency == URGENCY_VIBRATE and not pushes:
        # Reintento deduplicado: el push entregado puede ser este, así que una noche
        # anunciada que ya no está en la cadena (y no ha pasado) deja de contar.
        announced = tuple(d for d in announced
                          if d in trigger_days or d < today or (d == today and first == 1))
    if pushes:
        announced = tuple(sorted(set(announced) | trigger_days))
        announced_severity = severity
    if spell is not None:
        start = spell.start
    else:
        # ASSUMPTION: una Alert nueva nunca nace con el inicio (y por tanto el id) de una
        # Alert viva de la parcela: se tomaría la fila de deduplicación de esa y la misma
        # pasada publicaría y resolvería un id. Solo pasa si una Alert anunciada se queda
        # sin su cadena y el frío vuelve a su primera noche; se usa el día libre siguiente.
        start = chain[0].day
        while start in used_starts:
            start += timedelta(days=1)
    # ASSUMPTION: last_night nunca queda antes de start. Una cadena que solo tiene noches
    # anteriores al inicio deja el rango en [start, start]: el id sigue siendo el de la
    # primera noche y ninguna cadena nueva puede nacer con ese mismo id.
    last = max(chain[-1].day, start)
    return ChainDecision(
        alert_id=spell.alert_id if spell is not None else None,
        status=spell.status if spell is not None else None,
        start=start,
        last_night=last,
        stage=stage,
        severity=severity,
        urgency=urgency,
        nights=triggers,
        next_night=triggers[0],
        coldest=coldest,
        text_night=text,
        text_lead=lead(text),
        imminent=bool(imminent),
        announced=announced,
        announced_severity=announced_severity,
        dismissed_range=dismissed_range,
        skip=skip,
    )


def keep_alive_ids(existing: Sequence[ExistingSpell], today: date) -> tuple[str, ...]:
    """Alert activas cuya última noche aún no ha pasado: sin dato, no caducan."""
    return tuple(
        s.alert_id for s in existing
        if s.status == STATUS_ACTIVE and s.last_night is not None and s.last_night >= today
    )


def decide_chains(
    nights: Sequence[Night],
    existing_spells: Sequence[ExistingSpell],
    cfg: FrostConfig,
    today: date,
    muted: bool,
    local_hour: int,
) -> FrostEvaluation:
    by_lead = _by_lead(nights, today)
    first = first_trigger_lead(local_hour)

    def missing(lead: int) -> bool:
        n = by_lead.get(lead)
        return n is None or _finite(n.tmin_c) is None

    # Una evaluación sin las noches cercanas nunca resuelve: sin dato.
    if any(missing(lead) for lead in range(first, cfg.near_nights + 1)):
        return FrostEvaluation(True, REASON_INCOMPLETE, keep_ids=keep_alive_ids(existing_spells, today))

    unknown = [today + timedelta(days=lead)
               for lead in range(first, cfg.horizon_nights + 1) if missing(lead)]

    # Una cadena sin noche que dispare (solo la noche en curso pasadas las 10:00) no es
    # un aviso: su Alert, si la hay, se resuelve.
    chains = [
        c for c in build_chains(at_risk_nights(by_lead, existing_spells, cfg), cfg.gap_nights)
        if any((n.day - today).days >= first for n in c)
    ]
    matched, merged = match_chains(chains, existing_spells, cfg, today, local_hour)
    used_starts = frozenset(
        s.start for s in existing_spells
        if s.start is not None and s.status in (STATUS_ACTIVE, STATUS_DISMISSED)
    )
    decisions = tuple(
        _decide_chain(c, s, cfg, today, muted, local_hour, used_starts)
        for c, s in zip(chains, matched)
    )

    taken = {s.alert_id for s in matched if s is not None}
    merged_ids = {s.alert_id for s in merged}
    resolve: list[str] = []
    keep: list[str] = []
    for s in existing_spells:
        if s.alert_id in taken:
            continue
        if s.alert_id in merged_ids:
            if s.status in (STATUS_ACTIVE, STATUS_DISMISSED):
                resolve.append(s.alert_id)
            continue
        if s.status not in (STATUS_ACTIVE, STATUS_DISMISSED):
            continue
        if s.start is None:
            # Alert sin fecha (anterior a las cadenas): se cierra con el dato completo.
            if not unknown:
                resolve.append(s.alert_id)
            continue
        if s.status != STATUS_ACTIVE:
            continue
        a, b = _span(s)
        if any(a <= d <= b for d in unknown):
            # Una noche lejana sin dato dentro de su rango podría ser la fría.
            if s.last_night >= today:
                keep.append(s.alert_id)
            continue
        resolve.append(s.alert_id)

    reason = REASON_PARTIAL if not decisions and unknown else None
    return FrostEvaluation(False, reason, decisions, tuple(resolve), tuple(keep))
