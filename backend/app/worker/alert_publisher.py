"""Publica Alert en Orion-LD vía el SDK (sustituye a la entidad RiskAssessment)."""
import logging

from nkz_platform_sdk import SyncOrionClient

from app.alerts.contract import AlertCategory, AlertSeverity, build_alert_entity
from app.alerts.plain import action_for, plain_for, urgency_for

logger = logging.getLogger(__name__)

# (tenant, entity_id) -> nombre de la parcela; solo aciertos, por proceso.
_PARCEL_NAMES: dict[tuple[str, str], str] = {}


def _parcel_name(client, tenant_id: str, entity_id: str, entity_type: str) -> str:
    """Nombre de la parcela (una lectura por parcela); id corto si falla."""
    short = entity_id.rsplit(":", 1)[-1][:8]
    if entity_type != "AgriParcel":
        return short
    key = (tenant_id, entity_id)
    if key in _PARCEL_NAMES:
        return _PARCEL_NAMES[key]
    try:
        name = client.get_entity(entity_id, options="keyValues").get("name")
    except Exception as exc:  # nunca bloquea la publicación
        logger.info("Parcel name lookup failed for %s: %s", entity_id, exc)
        return short
    if isinstance(name, str) and name.strip():
        _PARCEL_NAMES[key] = name.strip()
        return name.strip()
    return short


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
    alert_id: str | None = None,
    urgency: str | None = None,
) -> bool:
    """Construye y publica un Alert. Devuelve True si Orion lo aceptó."""
    cat = category if isinstance(category, AlertCategory) else AlertCategory(category)
    sev = severity if isinstance(severity, AlertSeverity) else AlertSeverity(severity)

    with SyncOrionClient(tenant_id, base_url=orion_ld_url, context_url=context_url) as client:
        parcel_name = _parcel_name(client, tenant_id, entity_id, entity_type)
        entity = _build(
            tenant_id, entity_id, entity_type, alert_type, cat, sev,
            probability_score, evaluation_data, confidence, context_url, parcel_name,
            alert_id, urgency,
        )
        result = client.upsert_entities_batch([entity])
    return _finish(result, alert_type, entity_id)


def _build(tenant_id, entity_id, entity_type, alert_type, cat, sev,
           probability_score, evaluation_data, confidence, context_url, parcel_name,
           alert_id=None, urgency=None):
    return build_alert_entity(
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
        plain=plain_for(alert_type, cat.value, sev.value, evaluation_data, parcel_name),
        urgency=urgency or urgency_for(cat, sev, alert_type),
        alert_id=alert_id,
        action=action_for(entity_id, entity_type),
    )


def _finish(result, alert_type, entity_id) -> bool:
    ok = bool(result and result.get("upserted", 0) >= 1)
    if not ok:
        logger.warning(
            "Orion rejected Alert %s/%s: %s",
            alert_type, entity_id, (result or {}).get("errors"),
        )
    return ok
