"""Congela el contrato de la entidad Alert (SDM) para el módulo risk."""
from datetime import datetime, timezone
from enum import Enum

ALERT_TYPE = "Alert"

class AlertCategory(str, Enum):
    WEATHER = "weather"
    AGRONOMIC = "agronomic"
    DISEASE = "disease"
    PEST = "pest"
    ROBOTIC = "robotic"
    ENERGY = "energy"
    SENSOR = "sensor"
    CROP = "crop"

class AlertSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

SEVERITY_THRESHOLDS = {"critical": 95, "high": 80, "medium": 60}

def severity_for_score(score: float) -> AlertSeverity:
    if score >= SEVERITY_THRESHOLDS["critical"]:
        return AlertSeverity.CRITICAL
    if score >= SEVERITY_THRESHOLDS["high"]:
        return AlertSeverity.HIGH
    if score >= SEVERITY_THRESHOLDS["medium"]:
        return AlertSeverity.MEDIUM
    return AlertSeverity.LOW

def alert_entity_id(tenant_id: str, alert_type: str, entity_id: str) -> str:
    short = entity_id.rsplit(":", 1)[-1] if ":" in entity_id else entity_id
    return f"urn:ngsi-ld:Alert:{tenant_id}:{alert_type}:{short}"

def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def build_alert_entity(*, tenant_id, entity_id, entity_type, alert_type,
                       category, severity, probability_score=None,
                       evaluation_data=None, confidence=1.0, context_url) -> dict:
    e = {
        "@context": context_url,
        "id": alert_entity_id(tenant_id, alert_type, entity_id),
        "type": ALERT_TYPE,
        "category": {"type": "Property", "value": category.value},
        "alertType": {"type": "Property", "value": alert_type},
        "severity": {"type": "Property", "value": severity.value},
        "refEntity": {"type": "Relationship", "object": entity_id},
        "refEntityType": {"type": "Property", "value": entity_type},
        "confidence": {"type": "Property", "value": confidence},
        "evaluationData": {"type": "Property", "value": evaluation_data or {}},
        "status": {"type": "Property", "value": "active"},
        "observedAt": _now(),
    }
    if probability_score is not None:
        e["probabilityScore"] = {"type": "Property", "value": probability_score}
    return e
