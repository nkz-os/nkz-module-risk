"""Traduce una Alert NGSI-LD (de cualquier productor) a una entrega de aviso.

Los productores son heterogéneos (risk, sensor-health-beat, entity-manager
calibración, greenhouse-dt) y escriben campos distintos. Aquí se normalizan
`alert_type` y `severity` con valores por defecto y se deduplica por
`(alert_id, severity)` vía `risk.alert_deliveries` para no reenviar la misma
Alert cada hora (risk republica con el mismo id).
"""
import logging

from app.dispatcher import NotificationDispatcher

logger = logging.getLogger(__name__)


def _unwrap(value):
    """Extrae el valor de un Property/Relationship normalizado, o el valor crudo."""
    if isinstance(value, dict):
        return value.get("value", value.get("object"))
    return value


def _alert_to_info(entity: dict) -> dict | None:
    """Normaliza una Alert a {id, alert_type, severity, entity_id}, o None."""
    if not isinstance(entity, dict):
        return None
    alert_id = entity.get("id")
    if not alert_id:
        return None

    status = _unwrap(entity.get("status")) or "active"
    if status != "active":
        return None

    alert_type = _unwrap(entity.get("alertType"))
    if not alert_type:
        # Algunos productores ponen el tipo en el último segmento del id o en `type`.
        alert_type = entity.get("type") or alert_id.split(":")[-1] or "unknown"
    severity = str(_unwrap(entity.get("severity")) or "medium").lower()
    if severity not in ("low", "medium", "high", "critical"):
        severity = "medium"
    entity_id = _unwrap(entity.get("refEntity")) or ""

    return {
        "id": alert_id,
        "alert_type": str(alert_type),
        "severity": severity,
        "entity_id": str(entity_id) if entity_id else "",
    }


def dispatch_alert_entity(tenant_id: str, entity: dict) -> bool:
    """Entrega una Alert por los canales del tenant. False si se ignoró/deduplicó."""
    info = _alert_to_info(entity)
    if not info:
        return False

    dispatcher = NotificationDispatcher()
    if dispatcher.has_delivered(info["id"], info["severity"]):
        logger.debug("alert already delivered: %s/%s", info["id"], info["severity"])
        return False

    dispatcher.dispatch(
        tenant_id,
        info["alert_type"],
        info["severity"],
        {
            "id": info["id"],
            "title": f"Risk Alert — {info['alert_type']}",
            "summary": f"[{info['severity'].upper()}] {info['alert_type']}: {info['entity_id']}",
        },
    )
    return True
