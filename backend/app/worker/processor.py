"""Worker: evalúa riesgos del catálogo (con fuentes de datos reales) y publica Alert.

Fase 3: `_prepare_data_sources` monta las fuentes agronómicas (weather, gdd,
telemetry, weather_alerts, soil, ndvi, crop_health) desde Orion-LD per-tenant
y TimescaleDB. Además, el estrés de cultivo (CropHealthAssessment de
crop-health) se consume como una alerta de categoría `crop`.
"""
import logging
from typing import Any, Dict, List

from nkz_platform_sdk import SyncOrionClient

from app.alerts.contract import alert_entity_id, severity_for_score
from app.config import get_settings
from app.worker import sources, weather_source
from app.worker.alert_publisher import publish_alert
from app.worker.models.factory import RiskModelFactory
from app.worker.models.threshold_model import series_requests

logger = logging.getLogger(__name__)

PUBLISH_THRESHOLD = 50.0


def _get_catalog(conn) -> List[Dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT alert_type, name, category, model_type, model_config, "
            "severity_levels, target_sdm_type, data_sources "
            "FROM risk.alert_catalog WHERE is_active = true"
        )
        return [dict(r) for r in cur.fetchall()]


def _get_entities(orion: SyncOrionClient, entity_type: str) -> List[Dict[str, Any]]:
    return orion.query_entities(type=entity_type, limit=200)


def _prepare_data_sources(tenant_id, risk, entity, orion, conn, settings) -> Dict[str, Any]:
    """Monta el dict de fuentes que el modelo consume, según risk.data_sources."""
    parcel_id = weather_source.resolve_parcel_id(entity)
    required = risk.get("data_sources") or []
    ctx = sources.build_fetch_context(orion, conn, settings, tenant_id)
    data: Dict[str, Any] = {}

    for src in required:
        if src == "telemetry":
            entity_id = entity.get("id", "")
            etype = entity.get("type", "")
            if "Device" in etype or "Vehicle" in etype:
                device_id = entity_id.split(":")[-1] if ":" in entity_id else entity_id
                for metric in ("batteryLevel", "battery", "powerLevel"):
                    val = sources.fetch_telemetry(conn, tenant_id, device_id, metric, hours=1)
                    if val:
                        data["telemetry"] = val
                        break
            continue
        fetcher = sources.SOURCE_FETCHERS.get(src)
        if fetcher and parcel_id:
            data[src] = fetcher(ctx, parcel_id, risk)

    # Series temporales para condiciones sostenidas (duration_minutes > 0).
    for (src, attr), minutes in series_requests(risk.get("model_config")).items():
        if src == "telemetry" or not parcel_id:
            continue
        series = sources.fetch_series(conn, tenant_id, parcel_id, src, attr, minutes)
        if series is not None:
            data.setdefault(src, {})[f"__series__{attr}"] = series
        else:
            logger.debug("no series for %s.%s (duration_minutes unsupported)", src, attr)
    return data


def _dispatch_alert(tenant_id: str, alert_type: str, severity: str, entity_id: str) -> None:
    """Entrega el aviso por los canales configurados (no-fatal)."""
    from app.dispatcher import NotificationDispatcher

    alert_id = alert_entity_id(tenant_id, alert_type, entity_id)
    NotificationDispatcher().dispatch(
        tenant_id,
        alert_type,
        severity,
        {
            "id": alert_id,
            "title": f"Risk Alert — {alert_type}",
            "summary": f"[{severity.upper()}] {alert_type}: {entity_id}",
            "data": {"screen": "module/risk", "entityId": entity_id, "alertType": alert_type},
        },
    )


def _evaluate_crop_stress(orion, tenant_id, settings) -> tuple[int, int]:
    """CropHealthAssessment (crop-health) → Alert(category=crop) si severidad alta."""
    evaluated = errors = 0
    for parcel in _get_entities(orion, "AgriParcel"):
        parcel_id = parcel.get("id")
        if not parcel_id:
            continue
        ch = sources.fetch_parcel_crop_health(orion, parcel_id)
        if not ch:
            continue
        sev = str(ch.get("overall_severity") or "").upper()
        if sev not in ("HIGH", "CRITICAL"):
            continue
        alert_sev = "critical" if sev == "CRITICAL" else "high"
        score = 90.0 if alert_sev == "critical" else 75.0
        if publish_alert(
            orion_ld_url=settings.orion_ld_url,
            context_url=settings.context_url,
            tenant_id=tenant_id,
            entity_id=parcel_id,
            entity_type="AgriParcel",
            alert_type="crop_stress",
            category="crop",
            severity=alert_sev,
            probability_score=score,
            evaluation_data=ch,
            confidence=1.0,
        ):
            evaluated += 1
            try:
                _dispatch_alert(tenant_id, "crop_stress", alert_sev, parcel_id)
            except Exception as e:
                logger.warning("crop dispatch failed for %s: %s", parcel_id, e)
        else:
            logger.warning("crop publish failed for %s", parcel_id)
            errors += 1
    return evaluated, errors


def _evaluate_disease_risks(orion, tenant_id, settings) -> tuple[int, int]:
    """Modelos epidemiológicos (mildiu, oídio, etc.) por parcela desde weather."""
    from app.worker.disease_evaluator import evaluate_disease_risks

    evaluated = errors = 0
    for parcel in _get_entities(orion, "AgriParcel"):
        parcel_id = parcel.get("id")
        if not parcel_id:
            continue
        weather = sources.fetch_parcel_weather(settings.orion_ld_url, tenant_id, parcel_id)
        if not weather:
            continue
        try:
            published = evaluate_disease_risks(
                tenant_id,
                weather_data=weather,
                parcel_id=parcel_id,
                fidelity=weather.get("data_fidelity", "parcel_weather"),
            )
            evaluated += len(published)
        except Exception as e:
            logger.warning("disease eval failed for %s: %s", parcel_id, e)
            errors += 1
    return evaluated, errors


def evaluate_risks_for_tenant(conn, tenant_id: str) -> Dict[str, int]:
    """Evalúa todos los riesgos activos del catálogo para un tenant y publica Alert."""
    settings = get_settings()
    catalog = _get_catalog(conn)
    if not catalog:
        return {"evaluated": 0, "errors": 0}

    evaluated = errors = 0
    with SyncOrionClient(tenant_id, base_url=settings.orion_ld_url, context_url=settings.context_url) as orion:
        for risk in catalog:
            model = RiskModelFactory.create_model(
                risk["alert_type"], risk["category"], risk["model_config"], risk["model_type"]
            )
            if not model:
                logger.warning("Sin modelo para %s (model_type=%s)", risk["alert_type"], risk["model_type"])
                errors += 1
                continue

            for entity in _get_entities(orion, risk["target_sdm_type"]):
                entity_id = entity.get("id")
                if not entity_id:
                    continue
                try:
                    data_sources = _prepare_data_sources(tenant_id, risk, entity, orion, conn, settings)
                    result = model.evaluate(
                        entity_id=entity_id,
                        entity_type=risk["target_sdm_type"],
                        tenant_id=tenant_id,
                        data_sources=data_sources,
                    )
                    score = float(result.get("probability_score", 0.0))
                    if score < PUBLISH_THRESHOLD:
                        continue
                    severity = result.get("severity") or severity_for_score(score)
                    sev_str = severity if isinstance(severity, str) else severity.value
                    if publish_alert(
                        orion_ld_url=settings.orion_ld_url,
                        context_url=settings.context_url,
                        tenant_id=tenant_id,
                        entity_id=entity_id,
                        entity_type=risk["target_sdm_type"],
                        alert_type=risk["alert_type"],
                        category=risk["category"],
                        severity=severity,
                        probability_score=score,
                        evaluation_data=result.get("evaluation_data", {}),
                        confidence=result.get("confidence", 1.0),
                    ):
                        evaluated += 1
                        try:
                            _dispatch_alert(tenant_id, risk["alert_type"], sev_str, entity_id)
                        except Exception as e:
                            logger.warning("dispatch failed for %s/%s: %s", risk["alert_type"], entity_id, e)
                    else:
                        logger.warning("publish failed for %s/%s", risk["alert_type"], entity_id)
                        errors += 1
                except Exception as e:
                    logger.error("eval %s/%s: %s", risk["alert_type"], entity_id, e)
                    errors += 1

        # ── Estrés de cultivo (crop-health) — fuente externa, sin modelo factory ──
        try:
            ce, err = _evaluate_crop_stress(orion, tenant_id, settings)
            evaluated += ce
            errors += err
        except Exception as e:
            logger.warning("crop-stress evaluation skipped: %s", e)

        # ── Modelos de enfermedad (epidemiológicos) — fuente externa ──
        try:
            de, err = _evaluate_disease_risks(orion, tenant_id, settings)
            evaluated += de
            errors += err
        except Exception as e:
            logger.warning("disease evaluation skipped: %s", e)

    return {"evaluated": evaluated, "errors": errors}


def main() -> None:
    """CLI del worker (modo batch): evalúa todos los tenants activos una vez."""
    from app.db import get_conn

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT tenant_id FROM tenants "
                "WHERE status = 'active' AND tenant_id IS NOT NULL"
            )
            tenants = [r["tenant_id"] for r in cur.fetchall()]
        for tenant_id in tenants:
            try:
                result = evaluate_risks_for_tenant(conn, tenant_id)
                logger.info("tenant=%s result=%s", tenant_id, result)
            except Exception as e:
                logger.error("tenant=%s failed: %s", tenant_id, e)
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
