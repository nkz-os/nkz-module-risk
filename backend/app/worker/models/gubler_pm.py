"""Gubler-Thomas Model — Powdery Mildew (Erysiphe necator) on Grapevine.

Temperature-based model. Does NOT require leaf wetness (powdery mildew
germinates without free water — unlike downy mildew). This means it activates
at lower data fidelity than other epidemiological models.

Risk index is based on consecutive days with mean temperature 20-30°C,
which is the optimal range for conidial germination and mycelial growth.

Reference: Gubler, W.D. et al. (1999). "Control of powdery mildew using
the UC Davis powdery mildew risk index." Phytopathology 89:S30.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DiseaseRiskResult:
    disease: str = "powdery_mildew"
    crop: str = "grapevine"
    risk_level: str = "LOW"
    conditions: str = ""
    consecutive_days: int = 0
    temp_mean_3d: float = 0.0
    confidence: str = "high"
    source_model: str = "Gubler-Thomas (UC Davis Powdery Mildew Risk Index)"
    recommended_action: str = ""
    data_fidelity: str = "regional_proxy"


def evaluate_gubler_pm(
    daily_means: list[float],  # last N days mean temperatures
    fidelity: str = "regional_proxy",
) -> DiseaseRiskResult:
    """Evaluate powdery mildew risk using Gubler-Thomas index.

    Does NOT require LWD data — temperature only.
    Activates from regional_proxy fidelity (more permissive than
    other models that need LWD).

    Args:
        daily_means: List of daily mean temperatures, most recent last
        fidelity: dataFidelity level

    Returns:
        DiseaseRiskResult with risk level
    """
    result = DiseaseRiskResult(data_fidelity=fidelity)

    if not daily_means:
        result.recommended_action = "No temperature data available"
        return result

    # Count consecutive days in 20-30°C range
    consecutive = 0
    for t in reversed(daily_means):
        if 20.0 <= t <= 30.0:
            consecutive += 1
        else:
            break

    result.consecutive_days = consecutive

    if len(daily_means) >= 3:
        result.temp_mean_3d = round(sum(daily_means[-3:]) / 3, 1)
    else:
        result.temp_mean_3d = round(daily_means[-1], 1)

    result.conditions = (
        f"{consecutive} consecutive days at {result.temp_mean_3d}°C "
        f"(optimal 20-30°C for E. necator)"
    )

    if consecutive >= 5:
        result.risk_level = "HIGH"
        result.recommended_action = (
            "High powdery mildew pressure — apply preventive fungicide"
        )
        result.confidence = "high"
    elif consecutive >= 3:
        result.risk_level = "MEDIUM"
        result.recommended_action = (
            "Conditions favorable — monitor and prepare treatment"
        )
        result.confidence = "high"
    else:
        result.risk_level = "LOW"
        result.recommended_action = "Temperature not favorable for powdery mildew"
        result.confidence = "high"

    return result
