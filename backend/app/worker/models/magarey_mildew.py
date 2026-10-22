"""Magarey 10-10-10 Model — Downy Mildew (Plasmopara viticola) on Grapevine.

The "10-10-10 rule": at least 10mm rainfall in 24-48h AND at least 10°C
mean temperature AND at least 10 hours of leaf wetness → primary infection risk.

Reference: Magarey, R.D. et al. (2002). "A simple generic infection model
for foliar fungal plant pathogens." Plant Disease 86:1119-1124.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DiseaseRiskResult:
    disease: str = "downy_mildew"
    crop: str = "grapevine"
    risk_level: str = "LOW"  # LOW | MEDIUM | HIGH
    conditions: str = ""
    lwd_hours: float = 0.0
    lwd_method: str = "none"
    lwd_canopy_correction: str = "none"
    confidence: str = "medium"
    source_model: str = "Magarey et al. 2002, Plant Disease 86:1119-1124"
    recommended_action: str = ""
    data_fidelity: str = "regional_proxy"


def evaluate_magarey_mildew(
    precip_mm_48h: float,
    mean_temp_48h: float,
    lwd_hours: float,
    lwd_method: str = "estimated_NHRH",
    canopy_correction: str = "none",
    fidelity: str = "regional_proxy",
) -> DiseaseRiskResult:
    """Evaluate downy mildew risk using Magarey 10-10-10 rule.

    Args:
        precip_mm_48h: Total precipitation in last 48 hours (mm)
        mean_temp_48h: Mean temperature in last 48 hours (°C)
        lwd_hours: Estimated or measured leaf wetness duration (hours)
        lwd_method: How LWD was obtained
        canopy_correction: Canopy adjustment method
        fidelity: dataFidelity level

    Returns:
        DiseaseRiskResult with risk level and recommended action
    """
    result = DiseaseRiskResult(
        lwd_hours=round(lwd_hours, 1),
        lwd_method=lwd_method,
        lwd_canopy_correction=canopy_correction,
        data_fidelity=fidelity,
    )

    conditions = []
    rule_met = 0

    if precip_mm_48h >= 10.0:
        conditions.append(f"{precip_mm_48h:.0f}mm rain in 48h")
        rule_met += 1
    else:
        conditions.append(f"{precip_mm_48h:.0f}mm rain in 48h (< 10mm)")

    if mean_temp_48h >= 10.0 and mean_temp_48h <= 30.0:
        conditions.append(f"{mean_temp_48h:.1f}°C mean temp")
        rule_met += 1
    else:
        conditions.append(f"{mean_temp_48h:.1f}°C mean temp (outside 10-30°C)")

    if lwd_hours >= 10.0:
        conditions.append(f"{lwd_hours:.0f}h LWD ({lwd_method})")
        rule_met += 1
    else:
        conditions.append(f"{lwd_hours:.0f}h LWD (< 10h)")

    result.conditions = "; ".join(conditions)

    if rule_met == 3:
        result.risk_level = "HIGH"
        result.recommended_action = "Preventive fungicide application within 48h"
        result.confidence = "high" if lwd_method == "measured" else "medium"
    elif rule_met == 2:
        result.risk_level = "MEDIUM"
        result.recommended_action = (
            "Monitor conditions closely; prepare preventive treatment"
        )
    else:
        result.risk_level = "LOW"
        result.recommended_action = "No action needed at this time"

    return result
