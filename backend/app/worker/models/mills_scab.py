"""Mills Model — Apple Scab (Venturia inaequalis).

Mills table: defines infection risk based on hours of leaf wetness at a
given temperature. Published by Mills (1944) and modified by MacHardy & Gadoury (1989).

Reference: Mills, W.D. (1944). "Efficient use of sulphur dusts and sprays
during rain to control apple scab." Cornell Extension Bulletin 630.
"""

from __future__ import annotations

from dataclasses import dataclass

# Mills table: (temperature_C, minimum_hours_wetness_for_infection)
# Modified by MacHardy & Gadoury (1989)
MILLS_TABLE: list[tuple[float, float]] = [
    (6.0, 30.0),  # 6°C → 30h wetness needed for infection
    (8.0, 22.0),
    (10.0, 15.0),
    (12.0, 11.0),
    (14.0, 9.5),
    (16.0, 9.0),
    (18.0, 8.5),
    (20.0, 8.0),
    (22.0, 9.0),
    (24.0, 10.0),
    (26.0, 13.0),
]


@dataclass
class DiseaseRiskResult:
    disease: str = "apple_scab"
    crop: str = "apple"
    risk_level: str = "LOW"
    conditions: str = ""
    lwd_hours: float = 0.0
    lwd_method: str = "none"
    lwd_canopy_correction: str = "none"
    temp_mean: float = 0.0
    hours_needed: float = 99.0
    confidence: str = "medium"
    source_model: str = "Mills 1944, modified MacHardy & Gadoury 1989"
    recommended_action: str = ""
    data_fidelity: str = "regional_proxy"


def evaluate_mills_scab(
    mean_temp: float,
    lwd_hours: float,
    lwd_method: str = "estimated_NHRH",
    canopy_correction: str = "none",
    fidelity: str = "regional_proxy",
) -> DiseaseRiskResult:
    """Evaluate apple scab risk using Mills table.

    Compares actual leaf wetness hours against the minimum required
    for infection at the current mean temperature.

    Args:
        mean_temp: Mean temperature during wetness period (°C)
        lwd_hours: Hours of leaf wetness
        lwd_method: How LWD was obtained
        canopy_correction: Canopy adjustment
        fidelity: dataFidelity level

    Returns:
        DiseaseRiskResult with risk level
    """
    result = DiseaseRiskResult(
        lwd_hours=round(lwd_hours, 1),
        lwd_method=lwd_method,
        lwd_canopy_correction=canopy_correction,
        temp_mean=round(mean_temp, 1),
        data_fidelity=fidelity,
    )

    # Find required hours for current temperature (interpolate between table values)
    hours_needed = 99.0
    for t, h in MILLS_TABLE:
        if mean_temp <= t:
            hours_needed = h
            break
    else:
        # Above 26°C, use the top value (13h)
        hours_needed = MILLS_TABLE[-1][1]

    result.hours_needed = hours_needed
    result.conditions = f"{mean_temp:.1f}°C, {lwd_hours:.0f}h LWD ({lwd_method}), needs {hours_needed:.0f}h"

    if mean_temp < 6.0 or mean_temp > 28.0:
        result.risk_level = "LOW"
        result.recommended_action = "Temperature outside infection range"
        result.conditions += " (temp outside 6-28°C range)"
    elif lwd_hours >= hours_needed:
        result.risk_level = "HIGH"
        result.recommended_action = "Primary infection likely — apply curative fungicide within 96h of infection"
        result.confidence = "high" if lwd_method == "measured" else "medium"
    elif lwd_hours >= hours_needed * 0.7:
        result.risk_level = "MEDIUM"
        result.recommended_action = "Monitor — approaching infection threshold"
    else:
        result.risk_level = "LOW"
        result.recommended_action = "No infection risk at current wetness duration"

    return result
