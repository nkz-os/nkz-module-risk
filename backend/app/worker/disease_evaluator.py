"""Disease Risk Evaluator — runs epidemiological models and publishes to Orion-LD.

Called from the risk-worker batch evaluation cycle.
Consumes weather data from the same sources as other risk models.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

from common.ngsi_headers import inject_fiware_headers

logger = logging.getLogger(__name__)

ORION_URL = os.getenv("ORION_URL", "http://orion-ld-service:1026")
CONTEXT_URL = os.getenv(
    "CONTEXT_URL", "http://api-gateway-service:5000/ngsi-ld-context.json"
)

_SEVERITY_MAP = {"LOW": "low", "MEDIUM": "medium", "HIGH": "high", "CRITICAL": "critical"}


def _make_headers(tenant_id: str) -> dict:
    """Build Orion-LD headers with normalized tenant ID."""
    return inject_fiware_headers({}, tenant=tenant_id, has_context_in_body=False)


def evaluate_disease_risks(
    tenant_id: str,
    weather_data: dict[str, Any],
    parcel_id: str = "",
    fidelity: str = "regional_proxy",
) -> list[dict[str, Any]]:
    """Run all applicable disease models and publish results to Orion-LD.

    Returns list of published DiseaseRiskAssessment entity IDs.
    """
    published: list[dict[str, Any]] = []

    # Only run if we have basic weather data
    temp_avg = weather_data.get("temp_avg")
    humidity = weather_data.get("humidity_avg")
    precip = weather_data.get("precip_mm")
    if temp_avg is None or humidity is None:
        return published

    # ── Gubler-Thomas: Powdery Mildew (temperature only, no LWD needed) ──
    try:
        from app.worker.models.gubler_pm import evaluate_gubler_pm

        daily_means = [temp_avg]  # Simplified: single reading
        result = evaluate_gubler_pm(daily_means=daily_means, fidelity=fidelity)
        if result.risk_level != "LOW":
            entity = _publish_disease_risk(tenant_id, result, parcel_id)
            if entity:
                published.append(entity)
    except Exception as e:
        logger.debug("Gubler-Thomas skipped: %s", e)

    # ── Magarey: Downy Mildew (needs LWD) ──
    try:
        from app.worker.models.lwd_estimator import estimate_lwd_nhrh
        from app.worker.models.magarey_mildew import evaluate_magarey_mildew

        hourly_rh = [humidity]  # Simplified: single reading proxy
        lwd_hours = estimate_lwd_nhrh(hourly_rh)
        if lwd_hours > 0:
            precip_48h = precip or 0
            result = evaluate_magarey_mildew(
                precip_mm_48h=precip_48h,
                mean_temp_48h=temp_avg,
                lwd_hours=lwd_hours,
                fidelity=fidelity,
            )
            if result.risk_level != "LOW":
                entity = _publish_disease_risk(tenant_id, result, parcel_id)
                if entity:
                    published.append(entity)
    except Exception as e:
        logger.debug("Magarey skipped: %s", e)

    # ── Mills: Apple Scab (needs LWD) ──
    try:
        from app.worker.models.mills_scab import evaluate_mills_scab

        hourly_rh = [humidity]
        lwd_hours = estimate_lwd_nhrh(hourly_rh)
        if lwd_hours > 0:
            result = evaluate_mills_scab(
                mean_temp=temp_avg,
                lwd_hours=lwd_hours,
                fidelity=fidelity,
            )
            if result.risk_level != "LOW":
                entity = _publish_disease_risk(tenant_id, result, parcel_id)
                if entity:
                    published.append(entity)
    except Exception as e:
        logger.debug("Mills skipped: %s", e)

    # ── TomCast: Alternaria (needs LWD) ──
    try:
        from app.worker.models.tomcast_alternaria import evaluate_tomcast

        hourly_rh = [humidity]
        lwd_hours = estimate_lwd_nhrh(hourly_rh)
        if lwd_hours > 0:
            result = evaluate_tomcast(
                mean_temp=temp_avg,
                lwd_hours=lwd_hours,
                fidelity=fidelity,
            )
            if result.risk_level != "LOW":
                entity = _publish_disease_risk(tenant_id, result, parcel_id)
                if entity:
                    published.append(entity)
    except Exception as e:
        logger.debug("TomCast skipped: %s", e)

    return published


def _publish_disease_risk(
    tenant_id: str, result: Any, parcel_id: str = ""
) -> dict[str, Any] | None:
    """Publish a disease risk as a canonical Alert entity (category=agronomic)."""
    try:
        short = parcel_id.split(":")[-1] if ":" in parcel_id else (parcel_id or result.disease)
        entity_id = f"urn:ngsi-ld:Alert:{tenant_id}:disease:{result.disease}-{short}"
        severity = _SEVERITY_MAP.get(str(result.risk_level).upper(), "medium")

        entity: dict[str, Any] = {
            "id": entity_id,
            "type": "Alert",
            "@context": CONTEXT_URL,
            "category": {"type": "Property", "value": "agronomic"},
            "alertType": {"type": "Property", "value": f"disease_{result.disease}"},
            "severity": {"type": "Property", "value": severity},
            "description": {"type": "Property", "value": result.conditions or result.disease},
            "disease": {"type": "Property", "value": result.disease},
            "crop": {"type": "Property", "value": result.crop},
            "confidence": {"type": "Property", "value": result.confidence},
            "sourceModel": {"type": "Property", "value": result.source_model},
            "recommendedAction": {
                "type": "Property",
                "value": result.recommended_action,
            },
            "dataFidelity": {"type": "Property", "value": result.data_fidelity},
            "status": {"type": "Property", "value": "active"},
        }
        if hasattr(result, "lwd_method") and result.lwd_method != "none":
            entity["lwdMethod"] = {"type": "Property", "value": result.lwd_method}

        if parcel_id:
            entity["refEntity"] = {
                "type": "Relationship",
                "object": f"urn:ngsi-ld:AgriParcel:{parcel_id}",
            }

        headers = _make_headers(tenant_id)
        headers["Content-Type"] = "application/ld+json"
        headers.pop("Link", None)  # @context is in body
        resp = requests.post(
            f"{ORION_URL}/ngsi-ld/v1/entityOperations/upsert",
            json=[entity],
            headers=headers,
            timeout=10,
        )
        if resp.status_code in (200, 201, 204):
            logger.info(
                "Published disease Alert: %s (%s)", result.disease, result.risk_level
            )
            return entity
        else:
            logger.warning(
                "Orion-LD returned %d for disease Alert", resp.status_code
            )
    except Exception as e:
        logger.error("Failed to publish disease Alert: %s", e)
    return None
