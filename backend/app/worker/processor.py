"""Worker: evalúa riesgos del catálogo y publica Alert en Orion-LD.

Fase 0-1: publica Alert (mecanismo). El cableado de fuentes de datos
(weather/gdd/telemetry/weather_alerts) llega en Fase 2/3 — por ahora los
modelos reciben data_sources vacío y devuelven score 0 (graceful), y solo se
publica cuando score >= umbral, igual que el risk_processor original.
"""
import logging
from typing import Any, Dict, List

from nkz_platform_sdk import SyncOrionClient

from app.alerts.contract import severity_for_score
from app.config import get_settings
from app.worker.alert_publisher import publish_alert
from app.worker.models.factory import RiskModelFactory

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
                    result = model.evaluate(
                        entity_id=entity_id,
                        entity_type=risk["target_sdm_type"],
                        tenant_id=tenant_id,
                        data_sources={},  # Fase 2/3: cablear weather/gdd/telemetry/weather_alerts
                    )
                    score = float(result.get("probability_score", 0.0))
                    if score < PUBLISH_THRESHOLD:
                        continue
                    severity = severity_for_score(score)
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
                    else:
                        errors += 1
                except Exception as e:
                    logger.error("eval %s/%s: %s", risk["alert_type"], entity_id, e)
                    errors += 1
    return {"evaluated": evaluated, "errors": errors}
