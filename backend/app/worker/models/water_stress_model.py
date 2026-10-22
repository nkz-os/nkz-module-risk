#!/usr/bin/env python3
# =============================================================================
# Water Stress Risk Model (Unified — DUP-1 resolved)
# =============================================================================
# Evaluates crop water stress using cascade of precision:
#
#   PRIORITY 1 — CWSI from crop-health (ground truth, IR canopy sensor)
#     CWSI < 0.3  → no stress
#     CWSI 0.3-0.6 → moderate stress
#     CWSI 0.6-0.8 → high stress
#     CWSI > 0.8   → severe stress
#
#   PRIORITY 2 — MDS from crop-health (dendrometer)
#     ratio < 1.0 → no stress
#     ratio 1.0-1.3 → moderate
#     ratio 1.3-1.6 → high
#     ratio > 1.6 → severe
#
#   PRIORITY 3 — Water balance from weather (fallback)
#     precip - ETo, same thresholds as before
#
#   PRIORITY 4 — Soil moisture (secondary fallback)
#
# Configurable thresholds (model_config keys):
#   cwsi_thresholds:   default [0.3, 0.6, 0.8]
#   mds_ratio_thresholds: default [1.0, 1.3, 1.6]
#   balance_watch:     default 0.0   mm
#   balance_moderate:  default -5.0  mm
#   balance_stress:    default -15.0 mm
#   soil_stress_min:   default 15.0  %
#   soil_severe_min:   default 10.0  %
#   soil_weight:       default 0.3

import logging
import os
from typing import Any, Dict, Optional

import requests

from common.ngsi_headers import inject_fiware_headers

from .base_model import BaseRiskModel

logger = logging.getLogger(__name__)

ORION_URL = os.getenv("ORION_URL", "http://orion-ld-service:1026")
CONTEXT_URL = os.getenv(
    "CONTEXT_URL", "http://api-gateway-service:5000/ngsi-ld-context.json"
)


def _make_headers(tenant_id: str) -> dict:
    """Build Orion-LD headers with normalized tenant ID."""
    return inject_fiware_headers({}, tenant=tenant_id, has_context_in_body=False)


class WaterStressRiskModel(BaseRiskModel):
    """Unified water stress model: CWSI > MDS > weather balance > soil moisture."""

    def _get_crop_health_assessment(
        self, entity_id: str, tenant_id: str
    ) -> Optional[Dict[str, Any]]:
        """Query Orion-LD for the latest CropHealthAssessment for an entity."""
        try:
            headers = _make_headers(tenant_id)
            resp = requests.get(
                f"{ORION_URL}/ngsi-ld/v1/entities",
                params={
                    "type": "CropHealthAssessment",
                    "q": f'hasAgriParcel=="urn:ngsi-ld:AgriParcel:{entity_id}"',
                    "limit": 1,
                    "options": "keyValues",
                },
                headers=headers,
                timeout=10,
            )
            if resp.status_code == 200:
                entities = resp.json()
                if entities:
                    return entities[0]
        except Exception as e:
            logger.debug("Crop-health query skipped: %s", e)
        return None

    def _evaluate_from_cwsi(self, cwsi: float) -> Dict[str, Any]:
        """Evaluate stress from CWSI (ground truth)."""
        thresholds = self._get_config_value("cwsi_thresholds", [0.3, 0.6, 0.8])
        if cwsi < thresholds[0]:
            return {"probability": 5.0, "condition": "no_stress", "source": "cwsi"}
        elif cwsi < thresholds[1]:
            return {"probability": 45.0, "condition": "mild_stress", "source": "cwsi"}
        elif cwsi < thresholds[2]:
            return {
                "probability": 72.0,
                "condition": "moderate_stress",
                "source": "cwsi",
            }
        else:
            return {"probability": 94.0, "condition": "severe_stress", "source": "cwsi"}

    def _evaluate_from_mds(self, mds_value: float, mds_ref: float) -> Dict[str, Any]:
        """Evaluate stress from MDS ratio (dendrometer)."""
        ratio = mds_value / mds_ref if mds_ref > 0 else 0
        thresholds = self._get_config_value("mds_ratio_thresholds", [1.0, 1.3, 1.6])
        if ratio < thresholds[0]:
            return {
                "probability": 5.0,
                "condition": "no_stress",
                "source": "mds",
                "mds_ratio": round(ratio, 2),
            }
        elif ratio < thresholds[1]:
            return {
                "probability": 40.0,
                "condition": "mild_stress",
                "source": "mds",
                "mds_ratio": round(ratio, 2),
            }
        elif ratio < thresholds[2]:
            return {
                "probability": 68.0,
                "condition": "moderate_stress",
                "source": "mds",
                "mds_ratio": round(ratio, 2),
            }
        else:
            return {
                "probability": 90.0,
                "condition": "severe_stress",
                "source": "mds",
                "mds_ratio": round(ratio, 2),
            }

    def evaluate(
        self,
        entity_id: str,
        entity_type: str,
        tenant_id: str,
        data_sources: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Evaluate water stress with cascade: CWSI → MDS → weather → soil."""

        # ── PRIORITY 1: CWSI from crop-health (ground truth) ──────────────────
        assessment = self._get_crop_health_assessment(entity_id, tenant_id)
        if assessment:
            cwsi = assessment.get("cwsiValue")
            if cwsi is not None:
                result = self._evaluate_from_cwsi(float(cwsi))
                factors = [f"CWSI={cwsi:.2f} (sensor IR — crop-health)"]
                return self._build_response(
                    result["probability"],
                    result["condition"],
                    factors,
                    0.95,
                    result["source"],
                )

            mds_val = assessment.get("mdsValue")
            mds_sev = assessment.get("mdsSeverity")
            if mds_val is not None and mds_sev is not None:
                result = self._evaluate_from_mds(float(mds_val), 150.0)
                factors = [f"MDS={mds_val:.0f}µm ({mds_sev}) — dendrómetro"]
                return self._build_response(
                    result["probability"], result["condition"], factors, 0.85, "mds"
                )

        # ── PRIORITY 3: Water balance from weather (fallback) ─────────────────
        weather_data = data_sources.get("weather")
        if not weather_data:
            return self._build_response(
                0.0, "no_data", ["Missing weather data"], 0.0, "none"
            )

        precip = weather_data.get("precip_mm")
        eto = weather_data.get("eto_mm")
        soil_moisture: Optional[float] = weather_data.get("soil_moisture_0_10cm")

        if precip is None and eto is None and soil_moisture is None:
            return self._build_response(
                0.0,
                "no_data",
                ["Sin datos de precipitación, ETo ni humedad"],
                0.0,
                "none",
            )

        balance_watch = self._get_config_value("balance_watch", 0.0)
        balance_moderate = self._get_config_value("balance_moderate", -5.0)
        balance_stress = self._get_config_value("balance_stress", -15.0)
        soil_stress_min = self._get_config_value("soil_stress_min", 15.0)
        soil_severe_min = self._get_config_value("soil_severe_min", 10.0)
        soil_weight = self._get_config_value("soil_weight", 0.3)

        confidence = 0.7  # weather-only is less confident than sensor-based
        factors = []
        balance_score: Optional[float] = None
        water_balance: Optional[float] = None

        if precip is not None and eto is not None:
            water_balance = precip - eto
            if water_balance > balance_watch:
                balance_score = 5.0
                factors.append(f"Balance hídrico {water_balance:+.1f} mm: sin déficit")
            elif water_balance > balance_moderate:
                balance_score = 35.0
                factors.append(f"Balance hídrico {water_balance:+.1f} mm: déficit leve")
            elif water_balance > balance_stress:
                balance_score = 68.0
                factors.append(
                    f"Balance hídrico {water_balance:+.1f} mm: estrés hídrico"
                )
            else:
                balance_score = 92.0
                factors.append(
                    f"Balance hídrico {water_balance:+.1f} mm: estrés severo"
                )
        else:
            confidence -= 0.3
            factors.append("Balance hídrico no disponible (falta precip o ETo)")

        soil_score: Optional[float] = None
        if soil_moisture is not None:
            if soil_moisture >= 20.0:
                soil_score = 5.0
                factors.append(f"Humedad suelo {soil_moisture:.1f}%: óptima")
            elif soil_moisture >= soil_stress_min:
                soil_score = 25.0
                factors.append(f"Humedad suelo {soil_moisture:.1f}%: adecuada")
            elif soil_moisture >= soil_severe_min:
                soil_score = 65.0
                factors.append(f"Humedad suelo {soil_moisture:.1f}%: inicio de estrés")
            else:
                soil_score = 90.0
                factors.append(f"Humedad suelo {soil_moisture:.1f}%: estrés severo")
        else:
            confidence -= 0.1

        if balance_score is not None and soil_score is not None:
            probability = balance_score * (1 - soil_weight) + soil_score * soil_weight
        elif balance_score is not None:
            probability = balance_score
        elif soil_score is not None:
            probability = soil_score
        else:
            probability = 0.0
            confidence = 0.0

        if probability < 30:
            condition = "no_stress"
        elif probability < 55:
            condition = "mild_stress"
        elif probability < 75:
            condition = "moderate_stress"
        else:
            condition = "severe_stress"

        result_data: Dict[str, Any] = {
            "condition": condition,
            "factors": factors,
            "thresholds": {
                "balance_watch_mm": balance_watch,
                "balance_moderate_mm": balance_moderate,
                "balance_stress_mm": balance_stress,
            },
        }
        if water_balance is not None:
            result_data["water_balance_mm"] = round(water_balance, 2)
            result_data["precip_mm"] = precip
            result_data["eto_mm"] = eto
        if soil_moisture is not None:
            result_data["soil_moisture_0_10cm_pct"] = soil_moisture

        return self._build_response(
            probability, condition, factors, confidence, "weather"
        )

    def _build_response(
        self,
        probability: float,
        condition: str,
        factors: list,
        confidence: float,
        source: str,
    ) -> Dict[str, Any]:
        """Build standardized response dict."""
        if probability < 30:
            recommendation = "Sin estrés hídrico. No se requiere riego adicional."
        elif probability < 55:
            recommendation = (
                "Estrés hídrico leve. Monitorizar y considerar riego preventivo."
            )
        elif probability < 75:
            recommendation = (
                "Estrés hídrico moderado. Programar riego en las próximas 24-48h."
            )
        else:
            recommendation = "Estrés hídrico severo. Riego urgente recomendado."

        return {
            "probability_score": round(probability, 2),
            "evaluation_data": {
                "condition": condition,
                "recommendation": recommendation,
                "factors": factors,
                "source": source,
            },
            "confidence": round(max(confidence, 0.0), 2),
        }
