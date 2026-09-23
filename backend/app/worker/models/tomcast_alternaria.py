"""TomCast Model — Alternaria (Early Blight) on Tomato and Potato.

TomCast uses Daily Severity Values (DSV) accumulated over the season.
A DSV is calculated daily based on hours of leaf wetness and mean temperature
during the wetness period. When accumulated DSV exceeds a threshold (usually 15),
a fungicide application is recommended.

Reference: Pitblado, R.E. (1992). "The development and implementation of
TOM-CAST." Ontario Ministry of Agriculture and Food.
"""

from __future__ import annotations

from dataclasses import dataclass


def _dsv_from_temp_wetness(temp_c: float, wetness_hours: float) -> int:
    """Calculate Daily Severity Value (DSV) for TomCast."""
    if wetness_hours < 3:
        return 0
    if temp_c < 13.0:
        return 0
    elif temp_c < 17.0:
        return 1 if wetness_hours >= 6 else 0
    elif temp_c < 20.0:
        if wetness_hours >= 12:
            return 3
        elif wetness_hours >= 9:
            return 2
        elif wetness_hours >= 6:
            return 1
        return 0
    elif temp_c < 26.0:
        if wetness_hours >= 15:
            return 4
        elif wetness_hours >= 12:
            return 3
        elif wetness_hours >= 6:
            return 2
        return 0
    else:  # >= 26°C
        if wetness_hours >= 12:
            return 3
        elif wetness_hours >= 6:
            return 2
        return 0


@dataclass
class DiseaseRiskResult:
    disease: str = "alternaria"
    crop: str = "tomato"
    risk_level: str = "LOW"
    conditions: str = ""
    lwd_hours: float = 0.0
    lwd_method: str = "none"
    lwd_canopy_correction: str = "none"
    temp_mean: float = 0.0
    dsv_today: int = 0
    dsv_accumulated: int = 0
    dsv_threshold: int = 15
    confidence: str = "medium"
    source_model: str = "TomCast (Pitblado 1992, OMAF)"
    recommended_action: str = ""
    data_fidelity: str = "regional_proxy"


def evaluate_tomcast(
    mean_temp: float,
    lwd_hours: float,
    dsv_accumulated: int = 0,
    dsv_threshold: int = 15,
    lwd_method: str = "estimated_NHRH",
    canopy_correction: str = "none",
    fidelity: str = "regional_proxy",
) -> DiseaseRiskResult:
    """Evaluate Alternaria risk using TomCast DSV accumulation.

    Args:
        mean_temp: Mean temperature during wetness period (°C)
        lwd_hours: Hours of leaf wetness today
        dsv_accumulated: Cumulative DSV since season start
        dsv_threshold: DSV threshold for spray recommendation (default 15)
        lwd_method: How LWD was obtained
        canopy_correction: Canopy adjustment
        fidelity: dataFidelity level

    Returns:
        DiseaseRiskResult with risk level
    """
    dsv = _dsv_from_temp_wetness(mean_temp, lwd_hours)
    new_total = dsv_accumulated + dsv

    result = DiseaseRiskResult(
        lwd_hours=round(lwd_hours, 1),
        lwd_method=lwd_method,
        lwd_canopy_correction=canopy_correction,
        temp_mean=round(mean_temp, 1),
        dsv_today=dsv,
        dsv_accumulated=new_total,
        dsv_threshold=dsv_threshold,
        data_fidelity=fidelity,
    )

    result.conditions = (
        f"{mean_temp:.1f}°C, {lwd_hours:.0f}h LWD ({lwd_method}) → "
        f"DSV today={dsv}, accumulated={new_total}/{dsv_threshold}"
    )

    if new_total >= dsv_threshold:
        result.risk_level = "HIGH"
        result.recommended_action = (
            "DSV threshold exceeded — apply fungicide and reset accumulator"
        )
        result.confidence = "high" if lwd_method == "measured" else "medium"
    elif new_total >= dsv_threshold * 0.7:
        result.risk_level = "MEDIUM"
        result.recommended_action = (
            f"Approaching threshold ({new_total}/{dsv_threshold}) — prepare treatment"
        )
    else:
        result.risk_level = "LOW"
        result.recommended_action = "Continue monitoring"

    return result
