"""Adapta los 4 modelos epidemiológicos (funciones) a la interfaz `evaluate()`.

Los modelos viven como funciones puras (`evaluate_gubler_pm`, ...) que reciben
entradas agronómicas concretas; el worker del catálogo espera un objeto con
`evaluate(entity_id, entity_type, tenant_id, data_sources) -> dict`. Este
adaptador traduce `data_sources["weather"]` a las entradas de cada modelo y
mapea el `DiseaseRiskResult` al contrato `{probability_score, severity,
evaluation_data, confidence}`.

El `risk_code` es el `alert_type` del catálogo (`powdery_mildew`, `downy_mildew`,
`apple_scab`, `alternaria`), que aquí se mapea a su función de modelo.
"""
from typing import Any, Dict

# risk_level -> (severity, probability_score) para la salida del catálogo.
_RISK_MAP = {
    "LOW": ("low", 30.0),
    "MEDIUM": ("medium", 60.0),
    "HIGH": ("high", 80.0),
    "CRITICAL": ("critical", 95.0),
}
_CONFIDENCE = {"low": 0.5, "medium": 0.7, "high": 0.9}


def _lwd_from_humidity(humidity: Any) -> float:
    """Horas de mojado foliar (proxy NHRH) desde una lectura puntual de HR."""
    from app.worker.models.lwd_estimator import estimate_lwd_nhrh

    if humidity is None:
        return 0.0
    try:
        return float(estimate_lwd_nhrh([float(humidity)]))
    except (TypeError, ValueError):
        return 0.0


class DiseaseModelAdapter:
    """Un modelo epidemiológico registrado como model_type del catálogo."""

    def __init__(self, risk_code: str, model_config: Dict[str, Any]):
        self.risk_code = risk_code
        self.model_config = model_config

    def evaluate(self, *, entity_id: str, entity_type: str,
                 tenant_id: str, data_sources: Dict[str, Any]) -> Dict[str, Any]:
        weather = data_sources.get("weather", {}) or {}
        temp = weather.get("temp_avg")
        humidity = weather.get("humidity") or weather.get("humidity_avg")
        precip = weather.get("precip_mm") or 0.0
        fidelity = "parcel_weather"

        from app.worker.models import (
            gubler_pm,
            magarey_mildew,
            mills_scab,
            tomcast_alternaria,
        )

        temp_f = float(temp) if temp is not None else 0.0
        daily_means = weather.get("daily_temp_means")
        if not daily_means:
            daily_means = [temp_f] if temp is not None else []
        lwd_hours = weather.get("lwd_hours", _lwd_from_humidity(humidity))
        precip_48h = weather.get("precip_48h", float(precip) if precip else 0.0)

        if self.risk_code == "powdery_mildew":
            result = gubler_pm.evaluate_gubler_pm(daily_means=daily_means, fidelity=fidelity)
        elif self.risk_code == "downy_mildew":
            result = magarey_mildew.evaluate_magarey_mildew(
                precip_mm_48h=float(precip_48h),
                mean_temp_48h=temp_f,
                lwd_hours=float(lwd_hours),
                fidelity=fidelity,
            )
        elif self.risk_code == "apple_scab":
            result = mills_scab.evaluate_mills_scab(
                mean_temp=temp_f,
                lwd_hours=float(lwd_hours),
                fidelity=fidelity,
            )
        elif self.risk_code == "alternaria":
            result = tomcast_alternaria.evaluate_tomcast(
                mean_temp=temp_f,
                lwd_hours=float(lwd_hours),
                fidelity=fidelity,
            )
        else:
            return {
                "probability_score": 0.0,
                "severity": "low",
                "confidence": 0.0,
                "evaluation_data": {},
            }

        severity, score = _RISK_MAP.get(result.risk_level, ("low", 0.0))
        return {
            "probability_score": score,
            "severity": severity,
            "confidence": _CONFIDENCE.get(result.confidence, 0.7),
            "evaluation_data": {
                "disease": result.disease,
                "crop": result.crop,
                "conditions": result.conditions,
                "source_model": result.source_model,
                "recommended_action": result.recommended_action,
                "risk_level": result.risk_level,
            },
        }
