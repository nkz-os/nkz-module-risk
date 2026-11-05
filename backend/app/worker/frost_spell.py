"""Decisión pura del aviso de helada: noches, escalones por antelación y racha de frío.

Sin E/S ni registro. El worker aporta las noches (mínima prevista del día local D
= noche de D-1 a D), el estado previo de la racha y la fecha local de hoy.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

STAGE_NONE = "none"
STAGE_FAR = "far"
STAGE_NEAR = "near"
STAGE_SEVERE = "severe"

_STAGE_RANK = {STAGE_NONE: 0, STAGE_FAR: 1, STAGE_NEAR: 2, STAGE_SEVERE: 3}


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
class SpellState:
    start: date
    last_night: date
    vibrated: bool
    severe_vibrated: bool
    stage: str  # último escalón publicado


@dataclass(frozen=True)
class FrostDecision:
    no_data: bool
    stage: str  # none | far | near | severe
    severity: Optional[str]
    vibrate: bool
    next_night: Optional[Night]
    coldest: Optional[Night]
    at_risk: tuple[Night, ...]
    spell: Optional[SpellState]
    new_spell: bool


def config_from_model_config(model_config: dict) -> Optional[FrostConfig]:
    """Lee la configuración de helada; sin tabla de umbrales, antelación o horizonte, None.

    Claves: thresholds_by_lead [[noches, grados], ...], near_nights, horizon_nights,
    y opcionales moderate_threshold, severe_threshold, gap_nights.
    No hay valores por defecto para los umbrales: sin tabla no hay aviso anticipado.
    """
    if not isinstance(model_config, dict):
        return None
    try:
        raw = model_config.get("thresholds_by_lead")
        if not raw:
            return None
        rows = tuple(sorted((int(r[0]), float(r[1])) for r in raw))
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
    """Un aviso descartado solo se reabre cuando el escalón sube."""
    return _STAGE_RANK.get(stage, 0) > _STAGE_RANK.get(dismissed_stage, 0)


def _no_data(prev: Optional[SpellState]) -> FrostDecision:
    return FrostDecision(True, STAGE_NONE, None, False, None, None, (), prev, False)


def decide_frost(
    nights: list[Night],
    prev: Optional[SpellState],
    cfg: FrostConfig,
    today: date,
    muted: bool,
) -> FrostDecision:
    by_lead: dict[int, Night] = {}
    for n in nights:
        by_lead[(n.day - today).days] = n

    # Una evaluación incompleta nunca resuelve ni degrada: sin dato.
    for lead in range(1, cfg.near_nights + 1):
        n = by_lead.get(lead)
        if n is None or n.tmin_c is None:
            return _no_data(prev)

    at_risk_list: list[Night] = []
    severe_hit = False
    for lead in sorted(by_lead):
        n = by_lead[lead]
        if lead < 1 or lead > cfg.horizon_nights or n.tmin_c is None:
            continue
        thr = _threshold_for_lead(cfg, lead)
        if thr is not None and n.tmin_c <= thr:
            at_risk_list.append(n)
        if lead <= cfg.near_nights and n.tmin_c <= cfg.severe_c:
            severe_hit = True
    at_risk_list.sort(key=lambda n: n.day)
    at_risk = tuple(at_risk_list)

    near_hit = any((n.day - today).days <= cfg.near_nights for n in at_risk)
    if severe_hit:
        stage = STAGE_SEVERE
    elif near_hit:
        stage = STAGE_NEAR
    elif at_risk:
        stage = STAGE_FAR
    else:
        stage = STAGE_NONE

    if not at_risk:
        return FrostDecision(False, STAGE_NONE, None, False, None, None, (), prev, False)

    coldest = min(at_risk, key=lambda n: n.tmin_c)
    if stage == STAGE_SEVERE:
        severity = "critical"
    elif stage == STAGE_NEAR:
        severity = "high"
    else:
        severity = "medium" if coldest.tmin_c <= 0.0 else "low"

    first = at_risk[0].day
    new_spell = prev is None or first > prev.last_night + timedelta(days=cfg.gap_nights)

    chain_end = first
    for n in at_risk[1:]:
        if (n.day - chain_end).days <= cfg.gap_nights:
            chain_end = n.day
        else:
            break

    if new_spell:
        start, last = first, chain_end
        was_vibrated = was_severe = False
    else:
        start = prev.start
        last = max(prev.last_night, chain_end)
        was_vibrated, was_severe = prev.vibrated, prev.severe_vibrated

    entering = stage in (STAGE_NEAR, STAGE_SEVERE)
    wants = (entering and not was_vibrated) or (stage == STAGE_SEVERE and not was_severe)
    vibrate = wants and not muted

    spell = SpellState(
        start=start,
        last_night=last,
        vibrated=was_vibrated or entering,
        severe_vibrated=was_severe or stage == STAGE_SEVERE,
        stage=stage,
    )
    return FrostDecision(False, stage, severity, vibrate, at_risk[0], coldest, at_risk, spell, new_spell)
