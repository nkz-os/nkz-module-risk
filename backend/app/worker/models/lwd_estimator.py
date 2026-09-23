"""Leaf Wetness Duration (LWD) Estimator.

Estimates hours of leaf wetness from meteorological data when no physical
leaf wetness sensor is available. Standard practice in commercial agronomy.

Methods:
  1. NHRH: Number of Hours with Relative Humidity >= 90% (base method)
  2. CART/DPD: Classification And Regression Tree / Dew Point Depression
     (Gleason et al. 1994) — requires wind speed data.

Canopy correction via NDVI adjusts estimated LWD to reflect microclimate
within the crop canopy (dense canopies retain moisture longer).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LWDResult:
    hours: float = 0.0
    method: str = "none"  # estimated_NHRH | estimated_CART | measured | none
    canopy_correction: str = "none"  # ndvi_adjusted | none
    confidence: str = "medium"  # high | medium | low
    data_fidelity: str = "regional_proxy"


def estimate_lwd_nhrh(
    hourly_rh: list[float],
    threshold: float = 90.0,
) -> float:
    """Estimate LWD as number of hours with RH >= threshold.

    Simple, robust, requires only relative humidity data.
    Most widely used method when no leaf wetness sensor is available.
    """
    return sum(1 for rh in hourly_rh if rh >= threshold)


def estimate_lwd_cart(
    hourly_rh: list[float],
    hourly_temp: list[float],
    hourly_wind: list[float] | None = None,
) -> float:
    """Estimate LWD using CART/DPD method (Gleason et al. 1994).

    Leaf wetness occurs when:
      - RH >= 90%, OR
      - Dew point depression < 2°C AND wind speed < 2.5 m/s (if wind available)

    Reference: Gleason, M.L. et al. (1994). "Development and validation of
    an empirical model to estimate the duration of dew periods."
    Plant Disease 78:1011-1016.
    """
    hours = 0
    for i, rh in enumerate(hourly_rh):
        if rh >= 90.0:
            hours += 1
            continue
        if hourly_wind and i < len(hourly_wind) and i < len(hourly_temp):
            # Dew point depression approximation: T - Td ≈ (100 - RH) / 5
            dpd = (100.0 - rh) / 5.0
            if dpd < 2.0 and hourly_wind[i] < 2.5:
                hours += 1
    return hours


def apply_canopy_correction(lwd_hours: float, ndvi: float | None = None) -> float:
    """Adjust LWD for canopy microclimate using NDVI.

    Dense canopies (high NDVI) retain moisture longer due to reduced
    airflow and lower radiation penetration.

    Heuristic backed by literature (Sentelhas et al. 2008, Bock et al. 2011).
    Marked as canopy_adjusted_estimated in metadata.
    """
    if ndvi is None:
        return lwd_hours
    if ndvi > 0.6:
        return lwd_hours * 1.3  # dense canopy: 30% longer wetness
    elif ndvi > 0.3:
        return lwd_hours * 1.1  # moderate canopy: 10% longer
    return lwd_hours  # bare soil / sparse: no adjustment


def estimate_lwd(
    hourly_rh: list[float],
    hourly_temp: list[float] | None = None,
    hourly_wind: list[float] | None = None,
    ndvi: float | None = None,
    fidelity: str = "regional_proxy",
) -> LWDResult:
    """Estimate leaf wetness duration with best available method.

    Priority: CART > NHRH > none
    Canopy correction applied if NDVI available.
    """
    result = LWDResult(data_fidelity=fidelity)

    if hourly_wind and hourly_temp and len(hourly_wind) > 0:
        result.hours = estimate_lwd_cart(hourly_rh, hourly_temp, hourly_wind)
        result.method = "estimated_CART"
        result.confidence = "medium"
    elif hourly_rh:
        result.hours = estimate_lwd_nhrh(hourly_rh)
        result.method = "estimated_NHRH"
        result.confidence = "medium"
    else:
        return result  # no data

    if ndvi is not None:
        result.hours = apply_canopy_correction(result.hours, ndvi)
        result.canopy_correction = "ndvi_adjusted"
        result.confidence = "low" if result.method == "estimated_NHRH" else "medium"

    return result
