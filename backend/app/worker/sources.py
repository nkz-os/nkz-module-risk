"""Fetchers de fuentes de datos agronómicas para el worker.

Cada fuente se consulta por parcela con el patrón canónico de relación
`hasAgriParcel | refAgriParcel` (Orion-LD, per-tenant) o contra TimescaleDB.
Todo vía SDK (SyncOrionClient) o psycopg2 — nunca requests crudo a Orion.
"""
import logging
from typing import Any, Dict, List, Optional

import requests

from app.worker import weather_source

logger = logging.getLogger(__name__)

_PARCEL_Q = 'hasAgriParcel=="{p}"|refAgriParcel=="{p}"'


def query_by_parcel(orion, entity_type: str, parcel_id: str, limit: int = 1) -> List[Dict[str, Any]]:
    return orion.query_entities(
        type=entity_type, q=_PARCEL_Q.format(p=parcel_id), limit=limit
    )


def _value(entity: Dict[str, Any], name: str) -> Any:
    attr = entity.get(name)
    if isinstance(attr, dict):
        return attr.get("value", attr.get("object"))
    return attr


# ── Weather + GDD (delegadas a weather_source, ya portada) ──────────────


def fetch_parcel_weather(orion_ld_url: str, tenant_id: str, parcel_id: str):
    return weather_source.fetch_parcel_weather(orion_ld_url, tenant_id, parcel_id)


def fetch_season_gdd(conn, tenant_id: str, parcel_id: str, season_start_doy: int = 1):
    return weather_source.fetch_season_gdd(conn, tenant_id, season_start_doy, parcel_id)


# ── Soil (nkz-module-soil) ─────────────────────────────────────────────


def fetch_parcel_soil(orion, parcel_id: str) -> Optional[Dict[str, Any]]:
    for etype in ("AgriSoilExtended", "AgriSoil"):
        rows = query_by_parcel(orion, etype, parcel_id, limit=1)
        if rows:
            e = rows[0]
            awc = _value(e, "awc")
            if awc is None:
                awc = _value(e, "availableWaterCapacity")
            return {
                "type": etype,
                "texture": _value(e, "texture"),
                "awc": awc,
                "field_capacity": _value(e, "fieldCapacity"),
                "wilting_point": _value(e, "wiltingPoint"),
            }
    return None


# ── Vegetation (nkz-module-vegetation-health) ──────────────────────────


def fetch_parcel_ndvi(orion, parcel_id: str) -> Optional[Dict[str, Any]]:
    rows = query_by_parcel(orion, "EOProduct", parcel_id, limit=1)
    if not rows:
        return None
    e = rows[0]
    return {
        "ndvi": _value(e, "ndvi"),
        "savi": _value(e, "savi"),
        "observed_at": _value(e, "observedAt"),
    }


# ── Crop health (nkz-module-crop-health) ───────────────────────────────


def fetch_parcel_crop_health(orion, parcel_id: str) -> Optional[Dict[str, Any]]:
    rows = query_by_parcel(orion, "CropHealthAssessment", parcel_id, limit=10)
    for e in rows:
        # Saltar stubs placeholder (status=pending / provenance=placeholder) y
        # entidades sin severidad: no son evaluaciones reales.
        status = _value(e, "status")
        if status and str(status).lower() == "pending":
            continue
        if _value(e, "overallSeverity") is None:
            continue
        return {
            "cwsi": _value(e, "cwsiValue"),
            "overall_severity": _value(e, "overallSeverity"),
            "recommended_action": _value(e, "recommendedAction"),
            "compaction_risk_score": _value(e, "compactionRiskScore"),
            "soil_water_mm": _value(e, "soilWaterMm"),
            "soil_awc_mm": _value(e, "soilAWCmm"),
            "soil_water_ratio": _value(e, "soilWaterRatio"),
            "vhi": _value(e, "vhi"),
            "vci": _value(e, "vci"),
            "gdd_accumulated": _value(e, "gddAccumulated"),
        }
    return None


# ── Weather alerts (weather-api) ───────────────────────────────────────


def fetch_weather_alerts(weather_api_url: str, tenant_id: str, parcel_id: str) -> List[dict]:
    try:
        resp = requests.get(
            f"{weather_api_url}/api/weather/parcel/{parcel_id}/alerts",
            headers={"X-Tenant-ID": tenant_id, "X-User-ID": "risk-worker"},
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json().get("alerts", []) or []
        logger.warning(
            "weather-alerts fetch %s for %s: %s",
            resp.status_code, parcel_id, resp.text[:200],
        )
    except Exception as e:
        logger.debug("weather-alerts fetch failed for %s: %s", parcel_id, e)
    return []


# ── Telemetry (TimescaleDB) ────────────────────────────────────────────


def fetch_telemetry(conn, tenant_id: str, device_id: str, metric_name: str, hours: int = 24):
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT value, observed_at
            FROM telemetry_measurements
            WHERE tenant_id = %s AND device_id = %s AND attribute_name = %s
              AND observed_at >= NOW() - INTERVAL '%s hours'
            ORDER BY observed_at DESC LIMIT 1
            """,
            (tenant_id, device_id, metric_name, hours),
        )
        row = cursor.fetchone()
        cursor.close()
        return {"value": row[0], "observed_at": row[1]} if row else None
    except Exception as e:
        logger.error("telemetry fetch failed for %s/%s: %s", tenant_id, device_id, e)
        return None
