"""Per-parcel weather for risk evaluation, read from the Orion-LD broker.

Until now this service read `weather_observations` in PostgreSQL. That table has
had **no writer since June** — and two of the columns the risk models depend on,
`soil_moisture_0_10cm` and `gdd_accumulated`, were NULL in all 20k rows it ever
held, so the water-stress and pest-cycle inputs never worked at all.

The canonical source is now the broker: `weather-worker` publishes one
`WeatherObserved` per parcel (downscaled for altitude, aspect and slope) plus a
`WeatherForecast` carrying the daily aggregates that the SDM does not define on
`WeatherObserved` — which is where `temp_min` for the frost model lives.

Two rules this module does not bend:

* **No invented inputs.** An attribute the broker did not publish is left out of
  the returned dict. The models already distinguish "absent" (lower confidence)
  from a number; a fabricated default would silently become a verdict.
* **Units are converted at this boundary, once.** The broker publishes soil
  moisture as a volumetric fraction (`unitCode: M3`, m³/m³) while the models
  compare against percentages. 0.106 read as "10.6 %" is a mild deficit; read as
  "0.1 %" it is permanent severe stress.
"""

import logging
from typing import Any, Dict, Optional

import requests

from common.ngsi_headers import inject_fiware_headers

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15


def _headers(tenant_id: str) -> dict:
    """Tenant + platform @context Link, built here so no caller can omit it.

    A read that reaches Orion without the platform context expands its type to
    the default vocabulary and comes back empty — a false zero that is
    indistinguishable from "this parcel has no weather".
    """
    return inject_fiware_headers({}, tenant=tenant_id, has_context_in_body=False)

WEATHER_OBSERVED_TYPE = "WeatherObserved"
WEATHER_FORECAST_TYPE = "WeatherForecast"

# Volumetric water content (m³/m³) -> percent, the scale the models are written
# against. See water_stress_model: thresholds 20 / 15 / 10 are documented as %.
_FRACTION_TO_PERCENT = 100.0


def _percent(value: float) -> float:
    return value * _FRACTION_TO_PERCENT


# NGSI-LD attribute -> (flat key the risk models read, converter or None).
# Several models read the same quantity under different names; the aliases are
# listed explicitly rather than guessed at call sites.
ATTRIBUTE_MAP = (
    ("airTemperature", ("temp_avg", None)),
    ("tempCurrent", ("temperature", None)),
    ("humidity", ("humidity_avg", None)),
    ("humidity", ("humidity", None)),
    ("precipitation", ("precip_mm", None)),
    ("precipitation", ("precipitation", None)),
    ("et0", ("eto_mm", None)),
    ("deltaT", ("delta_t", None)),
    ("windSpeed", ("wind_speed_ms", None)),
    ("windDirection", ("wind_direction_deg", None)),
    ("solarRadiation", ("solar_rad_w_m2", None)),
    ("solarRadiation", ("solar_rad_ghi_w_m2", None)),
    ("solarRadiation", ("radiation", None)),
    ("atmosphericPressure", ("pressure_hpa", None)),
    ("soilMoistureTop", ("soil_moisture_0_10cm", _percent)),
    ("soilMoistureSub", ("soil_moisture_10_40cm", _percent)),
    ("gddAccumulated", ("gdd_accumulated", None)),
)


def attribute_value(entity: Dict[str, Any], name: str) -> Any:
    """Read an attribute from either the normalized or keyValues representation."""
    attr = entity.get(name)
    if isinstance(attr, dict):
        return attr.get("value", attr.get("object"))
    return attr


def _as_float(raw: Any) -> Optional[float]:
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def resolve_parcel_id(entity: Dict[str, Any]) -> Optional[str]:
    """The AgriParcel this entity's weather belongs to, or None.

    Weather is published per parcel, so an entity that is not a parcel has to
    point at one. Both relationship names are accepted: `hasAgriParcel` is the
    SDM standard, `refAgriParcel` the legacy form still present on older entities.
    """
    entity_id = entity.get("id", "")
    entity_type = entity.get("type", "")
    if "AgriParcel" in entity_type or ":AgriParcel:" in entity_id:
        return entity_id or None

    for name in ("hasAgriParcel", "refAgriParcel", "locatedAt"):
        link = entity.get(name)
        target = link.get("object") if isinstance(link, dict) else link
        if isinstance(target, str) and target:
            return target
    return None


def weather_observed_id(tenant_id: str, parcel_id: str) -> str:
    """Mirror of the id `weather-worker` writes; keep the two in step."""
    identifier = parcel_id.split(":")[-1] if ":" in parcel_id else parcel_id
    return f"urn:ngsi-ld:WeatherObserved:{tenant_id}:parcel-{identifier}"


def weather_forecast_id(tenant_id: str, parcel_id: str) -> str:
    identifier = parcel_id.split(":")[-1] if ":" in parcel_id else parcel_id
    return f"urn:ngsi-ld:WeatherForecast:{tenant_id}:parcel-{identifier}"


def flatten_weather_observed(entity: Dict[str, Any]) -> Dict[str, Any]:
    """Map a WeatherObserved entity onto the flat dict the risk models read."""
    flat: Dict[str, Any] = {}
    for attribute, (key, convert) in ATTRIBUTE_MAP:
        value = _as_float(attribute_value(entity, attribute))
        if value is None:
            continue
        flat[key] = convert(value) if convert else value

    observed_at = attribute_value(entity, "dateObserved")
    if isinstance(observed_at, dict):
        observed_at = observed_at.get("@value")
    if observed_at:
        flat["observed_at"] = observed_at

    precip = flat.get("precip_mm")
    eto = flat.get("eto_mm")
    if precip is not None and eto is not None:
        flat["water_balance"] = precip - eto

    return flat


def merge_forecast(flat: Dict[str, Any], forecast: Dict[str, Any]) -> Dict[str, Any]:
    """Add the daily minimum/maximum the SDM keeps on WeatherForecast.

    `WeatherObserved` is an instantaneous observation in the Smart Data Model and
    defines no daily aggregates, so `temp_min` — what the frost model runs on —
    can only come from here.
    """
    for attribute, key in (("dayMinimum", "temp_min"), ("dayMaximum", "temp_max")):
        value = attribute_value(forecast, attribute)
        if isinstance(value, dict):
            value = value.get("temperature")
        value = _as_float(value)
        if value is not None:
            flat[key] = value
    return flat


def _get_entity(
    orion_url: str, headers: dict, entity_id: str, timeout: int
) -> Optional[Dict[str, Any]]:
    try:
        response = requests.get(
            f"{orion_url}/ngsi-ld/v1/entities/{entity_id}",
            headers=headers,
            timeout=timeout,
        )
    except Exception as exc:
        logger.error("Broker unreachable reading %s: %s", entity_id, exc)
        return None

    if response.status_code == 200:
        return response.json()
    if response.status_code == 404:
        return None
    # Never swallowed: a 400 here is what a missing @context Link looks like, and
    # it is indistinguishable from "no data" unless it is logged.
    logger.error(
        "Broker returned %s reading %s: %s",
        response.status_code,
        entity_id,
        response.text[:200],
    )
    return None


def fetch_parcel_weather(
    orion_url: str,
    tenant_id: str,
    parcel_id: str,
    timeout: int = DEFAULT_TIMEOUT,
) -> Optional[Dict[str, Any]]:
    """Latest downscaled weather for one parcel, or None if the broker has none."""
    headers = _headers(tenant_id)
    observed = _get_entity(
        orion_url, headers, weather_observed_id(tenant_id, parcel_id), timeout
    )
    if not observed:
        logger.warning(
            "No WeatherObserved in the broker for parcel %s (tenant %s); "
            "weather-driven risks cannot be evaluated for it.",
            parcel_id,
            tenant_id,
        )
        return None

    flat = flatten_weather_observed(observed)
    flat["data_fidelity"] = "parcel_weather"
    flat["parcel_id"] = parcel_id

    forecast = _get_entity(
        orion_url, headers, weather_forecast_id(tenant_id, parcel_id), timeout
    )
    if forecast:
        merge_forecast(flat, forecast)
    else:
        logger.info(
            "No WeatherForecast for parcel %s; daily minimum/maximum unavailable "
            "(frost risk will report reduced confidence).",
            parcel_id,
        )

    return flat


def fetch_season_gdd(
    connection,
    tenant_id: str,
    season_start_doy: int = 1,
    parcel_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Season-to-date GDD, summed from the timeseries the broker feeds.

    The daily increment arrives in `telemetry_measurements` through the
    WeatherObserved subscription, under its compacted NGSI-LD attribute name.
    """
    query = """
        SELECT COALESCE(SUM(value), 0) AS gdd_season_total,
               COUNT(*)                AS days_accumulated
        FROM telemetry_measurements
        WHERE tenant_id = %s
          AND attribute_name = 'gddAccumulated'
          AND observed_at >= make_date(
                EXTRACT(year FROM CURRENT_DATE)::int, 1, 1
              ) + INTERVAL '1 day' * (%s - 1)
    """
    params: list = [tenant_id, season_start_doy]
    if parcel_id:
        query += " AND entity_id = %s"
        params.append(weather_observed_id(tenant_id, parcel_id))

    try:
        cursor = connection.cursor()
        cursor.execute(query, params)
        row = cursor.fetchone()
        cursor.close()
    except Exception as exc:
        logger.error("Failed to read season GDD for %s: %s", tenant_id, exc)
        return None

    if not row:
        return None
    days = int(row["days_accumulated"] if isinstance(row, dict) else row[1])
    if days <= 0:
        return None
    total = float(row["gdd_season_total"] if isinstance(row, dict) else row[0])
    return {
        "gdd_season_total": total,
        "season_start_doy": season_start_doy,
        "days_accumulated": days,
    }
