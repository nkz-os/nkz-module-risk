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


def _first(entity: Dict[str, Any], *names: str) -> Any:
    """Primer atributo presente (no None) de la lista de alias."""
    for name in names:
        v = _value(entity, name)
        if v is not None:
            return v
    return None


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
                # Conductividad eléctrica (salinidad) y temperatura del suelo.
                # Hoy pueden no existir en el broker: se leen si están, si no
                # quedan ausentes y la condición no se evalúa (sin inventar dato).
                "ec": _first(e, "electroConductivity", "electricalConductivity", "ec"),
                "temperature": _value(e, "soilTemperature"),
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


# ── Series temporales (duration_minutes) ────────────────────────────
# Mapa inverso: flat key -> (attribute_name en telemetry_measurements, converter).
# Solo los atributos que el feed de WeatherObserved escribe como serie soportan
# `duration_minutes`; el resto (p.ej. temp_min/temp_max, que viven solo en
# WeatherForecast) cae al valor puntual.
_FLAT_TO_NGSI = {
    "temp_avg": ("airTemperature", None),
    "temperature": ("tempCurrent", None),
    "humidity": ("humidity", None),
    "humidity_avg": ("humidity", None),
    "precip_mm": ("precipitation", None),
    "precipitation": ("precipitation", None),
    "eto_mm": ("et0", None),
    "delta_t": ("deltaT", None),
    "wind_speed_ms": ("windSpeed", None),
    "wind_direction_deg": ("windDirection", None),
    "solar_rad_w_m2": ("solarRadiation", None),
    "solar_rad_ghi_w_m2": ("solarRadiation", None),
    "radiation": ("solarRadiation", None),
    "pressure_hpa": ("atmosphericPressure", None),
    "soil_moisture_0_10cm": ("soilMoistureTop", weather_source._percent),
    "soil_moisture_10_40cm": ("soilMoistureSub", weather_source._percent),
    "gdd_accumulated": ("gddAccumulated", None),
}


def fetch_weather_series(
    conn, tenant_id: str, parcel_id: str, attribute: str, minutes: int
) -> Optional[List[tuple]]:
    """Serie temporal de un atributo weather sobre [now - minutes, now].

    Lee el feed que la suscripción de WeatherObserved vuelca en
    telemetry_measurements (entity_id = id canónico de WeatherObserved).
    Devuelve [(valor, observed_at), ...] (ascendente) o None si el atributo
    no tiene serie disponible.
    """
    entry = _FLAT_TO_NGSI.get(attribute)
    if entry is None:
        return None
    ngsi_name, convert = entry
    entity_id = weather_source.weather_observed_id(tenant_id, parcel_id)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT value, observed_at
            FROM telemetry_measurements
            WHERE tenant_id = %s AND entity_id = %s AND attribute_name = %s
              AND observed_at >= NOW() - make_interval(mins => %s)
            ORDER BY observed_at ASC
            """,
            (tenant_id, entity_id, ngsi_name, minutes),
        )
        rows = cur.fetchall()
        cur.close()
        return [(convert(v) if convert else v, t) for (v, t) in rows]
    except Exception as e:
        logger.error("weather series fetch failed for %s/%s: %s", tenant_id, attribute, e)
        return None


def fetch_series(
    conn, tenant_id: str, parcel_id: str, source: str, attribute: str, minutes: int
) -> Optional[List[tuple]]:
    """Serie temporal genérica por (source, attribute) — None si no hay serie."""
    if source == "weather":
        return fetch_weather_series(conn, tenant_id, parcel_id, attribute, minutes)
    return None


def fetch_leaf_wetness(conn, tenant_id: str, parcel_id: str, window_hours: int = 24):
    """Horas de mojado foliar (LWD) estimadas por NHRH (RH>=90%) sobre la ventana.

    Fuente derivada: no hay sensor de mojado, se estima a partir de la serie de
    humedad relativa del feed de WeatherObserved (ya en telemetry_measurements).
    """
    from app.worker.models.lwd_estimator import estimate_lwd_nhrh

    series = fetch_weather_series(conn, tenant_id, parcel_id, "humidity", window_hours * 60)
    if not series:
        return None
    hourly_rh = [v for v, _t in series]
    hours = estimate_lwd_nhrh(hourly_rh)
    return {"hours": hours, "method": "estimated_NHRH"}


# ── Registry de fuentes por parcela ────────────────────────────────────
# Cada entrada es un callable (ctx, parcel_id, risk) -> data. Añadir una fuente
# nueva = añadir una entrada aquí (no hay que tocar processor.py).
def build_fetch_context(orion, conn, settings, tenant_id: str) -> dict:
    return {"orion": orion, "conn": conn, "settings": settings, "tenant_id": tenant_id}


SOURCE_FETCHERS = {
    "weather": lambda ctx, pid, risk: fetch_parcel_weather(
        ctx["settings"].orion_ld_url, ctx["tenant_id"], pid
    ),
    "gdd": lambda ctx, pid, risk: fetch_season_gdd(
        ctx["conn"], ctx["tenant_id"], pid,
        (risk.get("model_config") or {}).get("season_start_doy", 1),
    ),
    "soil": lambda ctx, pid, risk: fetch_parcel_soil(ctx["orion"], pid),
    "ndvi": lambda ctx, pid, risk: fetch_parcel_ndvi(ctx["orion"], pid),
    "crop_health": lambda ctx, pid, risk: fetch_parcel_crop_health(ctx["orion"], pid),
    "weather_alerts": lambda ctx, pid, risk: fetch_weather_alerts(
        ctx["settings"].weather_api_url, ctx["tenant_id"], pid
    ),
    "leaf_wetness": lambda ctx, pid, risk: fetch_leaf_wetness(
        ctx["conn"], ctx["tenant_id"], pid
    ),
}


# ── Catálogo de fuentes (para la UI: autocompletado de atributos) ──────
SOURCE_CATALOG = {
    "weather": {
        "label": "Meteorología",
        "attributes": [
            "temp_min", "temp_max", "temp_avg", "humidity", "precip_mm",
            "wind_speed_ms", "wind_direction_deg", "eto_mm", "solar_rad_w_m2",
            "pressure_hpa", "soil_moisture_0_10cm", "soil_moisture_10_40cm",
            "gdd_accumulated",
        ],
    },
    "soil": {
        "label": "Suelo",
        "attributes": ["texture", "awc", "field_capacity", "wilting_point", "ec", "temperature"],
    },
    "ndvi": {"label": "Vegetación (índice)", "attributes": ["ndvi", "savi"]},
    "crop_health": {
        "label": "Salud de cultivo",
        "attributes": [
            "cwsi", "compaction_risk_score", "soil_water_ratio",
            "vhi", "vci", "gdd_accumulated",
        ],
    },
    "gdd": {"label": "Grados-día", "attributes": ["gdd_season_total", "days_accumulated"]},
    "weather_alerts": {"label": "Avisos meteorológicos", "attributes": []},
    "leaf_wetness": {"label": "Mojado foliar", "attributes": ["hours"]},
    "telemetry": {"label": "Telemetría de dispositivo", "attributes": ["value"]},
}
