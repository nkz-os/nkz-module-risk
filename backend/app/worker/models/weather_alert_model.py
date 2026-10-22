#!/usr/bin/env python3
# =============================================================================
# Weather Alert Risk Model
# =============================================================================
# Pure mapping: given the active MeteoAlarm alerts covering a parcel (fetched by
# the RiskProcessor via weather-api), pick the most severe and map its
# CAP-derived severity to a probability score. Scores >= 50 trigger a tenant
# notification through the risk pipeline (RiskProcessor._publish_risk_event
# threshold). The covering-alert trace lives in evaluation_data (NOT a
# ref<Type> relationship — deprecated for new attributes).

from typing import Any, Dict, List

from .base_model import BaseRiskModel

# WeatherAlert.severity (SDM, from the weather-worker _CAP_SEVERITY_MAP) ->
# probability score. Tunable per catalog row via model_config["severity_scores"].
_DEFAULT_SEVERITY_SCORES: Dict[str, float] = {
    "informational": 10.0,
    "minor": 35.0,     # below 50 → recorded, not notified
    "moderate": 60.0,  # notify
    "severe": 80.0,    # notify
    "critical": 95.0,  # notify
}
_RANK = {"informational": 0, "minor": 1, "moderate": 2, "severe": 3, "critical": 4}


class WeatherAlertRiskModel(BaseRiskModel):
    """Map the most severe covering weather alert to a probability score."""

    def evaluate(
        self,
        entity_id: str,
        entity_type: str,
        tenant_id: str,
        data_sources: Dict[str, Any],
    ) -> Dict[str, Any]:
        alerts: List[Dict[str, Any]] = data_sources.get("weather_alerts") or []
        scores = {
            **_DEFAULT_SEVERITY_SCORES,
            **(self._get_config_value("severity_scores", {}) or {}),
        }

        if not alerts:
            return {
                "probability_score": 0.0,
                "evaluation_data": {"condition": "no_active_alert"},
                "confidence": 1.0,
            }

        def _severity(alert: Dict[str, Any]) -> str:
            s = alert.get("severity")
            s = s.get("value") if isinstance(s, dict) else s
            return (s or "informational").lower()

        worst = max(alerts, key=lambda a: _RANK.get(_severity(a), 0))
        severity = _severity(worst)
        sub = worst.get("subCategory")
        sub = sub.get("value") if isinstance(sub, dict) else sub

        return {
            "probability_score": round(float(scores.get(severity, 35.0)), 2),
            "evaluation_data": {
                "condition": "active_alert",
                "alert_id": worst.get("id"),
                "severity": severity,
                "subCategory": sub,
                "description": worst.get("description"),
                "covering_alert_count": len(alerts),
            },
            "confidence": 1.0,
        }
