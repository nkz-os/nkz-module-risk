"""Publica Alert en Orion-LD vía el SDK (sustituye a la entidad RiskAssessment)."""
from nkz_platform_sdk import SyncOrionClient

from app.alerts.contract import AlertCategory, AlertSeverity, build_alert_entity


def publish_alert(
    *,
    orion_ld_url: str,
    context_url: str,
    tenant_id: str,
    entity_id: str,
    entity_type: str,
    alert_type: str,
    category,
    severity,
    probability_score: float | None = None,
    evaluation_data: dict | None = None,
    confidence: float = 1.0,
) -> bool:
    """Construye y publica un Alert. Devuelve True si Orion lo aceptó."""
    cat = category if isinstance(category, AlertCategory) else AlertCategory(category)
    sev = severity if isinstance(severity, AlertSeverity) else AlertSeverity(severity)

    entity = build_alert_entity(
        tenant_id=tenant_id,
        entity_id=entity_id,
        entity_type=entity_type,
        alert_type=alert_type,
        category=cat,
        severity=sev,
        probability_score=probability_score,
        evaluation_data=evaluation_data,
        confidence=confidence,
        context_url=context_url,
    )

    with SyncOrionClient(tenant_id, base_url=orion_ld_url, context_url=context_url) as client:
        result = client.upsert_entities_batch([entity])
    return bool(result and result.get("upserted", 0) >= 1)
