"""Modelo genérico de umbral — evalúa un árbol AND/OR recursivo de condiciones.

Un riesgo custom se declara en `model_config`:

    {"logical_operator": "OR",
     "conditions": [
       {"source": "weather", "attribute": "temp_min", "operator": "<", "value": 0, "severity": "critical"},
       {"logical_operator": "AND", "conditions": [
           {"source": "weather", "attribute": "wind_speed_ms", "operator": ">", "value": 11, "duration_minutes": 30},
           {"source": "soil", "attribute": "awc", "operator": "<", "value": 15}
       ]}
     ]}

Cada condición compara `data_sources[source][attribute]` contra `value`. La fuerza
del cumplimiento (distancia al umbral) da la probabilidad; la severidad de la
condición vencida da la severidad del aviso. Un dato ausente no se inventa: la
condición no se evalúa y la confianza cae.
"""
from typing import Any, Dict, List

from .base_model import BaseRiskModel

_SEV_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}

# Condiciones sostenidas (duration_minutes): se considera "sostenida" si la
# condición se cumple en al menos MIN_SUSTAINED_COVERAGE de las muestras de la
# ventana, con al menos MIN_SUSTAINED_SAMPLES muestras (una sola muestra no es
# "sostenida").
MIN_SUSTAINED_COVERAGE = 0.6
MIN_SUSTAINED_SAMPLES = 2


def _get(data_sources: Dict[str, Any], source: str, attribute: str) -> Any:
    src = data_sources.get(source)
    if isinstance(src, dict):
        return src.get(attribute)
    return None


def _get_series(data_sources: Dict[str, Any], source: str, attribute: str) -> Any:
    """Serie temporal inyectada bajo la convención `__series__{attribute}`."""
    src = data_sources.get(source)
    if isinstance(src, dict):
        return src.get(f"__series__{attribute}")
    return None


def series_requests(model_config: Dict[str, Any]) -> Dict[tuple, int]:
    """(source, attribute) -> max duration_minutes pedido por el árbol de condiciones."""
    requests: Dict[tuple, int] = {}

    def walk(group):
        for cond in (group or {}).get("conditions", []):
            if "conditions" in cond:
                walk(cond)
            else:
                d = cond.get("duration_minutes") or 0
                if d > 0:
                    key = (cond.get("source"), cond.get("attribute"))
                    requests[key] = max(requests.get(key, 0), d)

    walk(model_config)
    return requests


def _strength(val: Any, operator: str, target: Any) -> float:
    """0.0–1.0 fuerza con la que la condición se cumple (0 = no cumplida)."""
    try:
        if operator in ("<", "<="):
            if val < target:
                return min(1.0, max(0.0, (target - val) / (abs(target) or 1.0)))
            return 0.0
        if operator in (">", ">="):
            if val > target:
                return min(1.0, max(0.0, (val - target) / (abs(target) or 1.0)))
            return 0.0
        if operator == "==":
            return 1.0 if val == target else 0.0
        if operator == "!=":
            return 1.0 if val != target else 0.0
        if operator in ("in", "not_in"):
            haystack = target if isinstance(target, list) else [target]
            present = val in haystack
            return (1.0 if present else 0.0) if operator == "in" else (0.0 if present else 1.0)
    except (TypeError, ValueError):
        return 0.0
    return 0.0


class ThresholdRiskModel(BaseRiskModel):
    """Evalúa `model_config.conditions` (árbol AND/OR recursivo) sobre las fuentes."""

    def evaluate(
        self,
        entity_id: str,
        entity_type: str,
        tenant_id: str,
        data_sources: Dict[str, Any],
    ) -> Dict[str, Any]:
        conditions = self.model_config.get("conditions", [])
        if not conditions:
            return {
                "probability_score": 0.0,
                "evaluation_data": {"error": "no conditions declared"},
                "confidence": 0.0,
            }

        score, confidence, severity, factors = self._eval_group(self.model_config, data_sources)
        return {
            "probability_score": round(min(100.0, max(0.0, score)), 2),
            "severity": severity,
            "evaluation_data": {"factors": factors, "conditions": conditions},
            "confidence": round(min(1.0, max(0.0, confidence)), 2),
        }

    def _eval_group(self, group: Dict[str, Any], data_sources: Dict[str, Any]):
        op = (group.get("logical_operator") or "AND").upper()
        scores: List[float] = []
        confs: List[float] = []
        sevs: List[str] = []
        factors: List[str] = []

        for cond in group.get("conditions", []):
            if "conditions" in cond:
                s, c, sev, f = self._eval_group(cond, data_sources)
            else:
                s, c, sev, f = self._eval_condition(cond, data_sources)
            scores.append(s)
            confs.append(c)
            sevs.append(sev)
            factors.extend(f)

        if not scores:
            return 0.0, 1.0, "low", factors

        if op == "OR":
            return (
                max(scores),
                max(confs),
                max(sevs, key=lambda s: _SEV_ORDER.get(s, 0)),
                factors,
            )
        # AND
        return (
            min(scores),
            min(confs),
            max(sevs, key=lambda s: _SEV_ORDER.get(s, 0)),
            factors,
        )

    def _eval_condition(self, cond: Dict[str, Any], data_sources: Dict[str, Any]):
        source = cond.get("source")
        attribute = cond.get("attribute")
        operator = cond.get("operator")
        target = cond.get("value")
        duration = cond.get("duration_minutes") or 0

        val = _get(data_sources, source, attribute)
        series = _get_series(data_sources, source, attribute) if duration > 0 else None

        if duration > 0 and series is not None:
            latest = val if val is not None else (series[-1][0] if series else None)
            if latest is None:
                return 0.0, 0.0, "low", [f"{source}.{attribute}: missing"]
            return self._eval_sustained(cond, series, latest, source, attribute, duration)

        if val is None:
            return 0.0, 0.0, "low", [f"{source}.{attribute}: missing"]

        if duration > 0:
            # Sin serie disponible: se evalúa el último valor y se anota la caída
            # de confianza (no se inventa una serie).
            strength = _strength(val, operator, target)
            score = strength * 100.0
            severity = cond.get("severity", "medium") if strength > 0 else "low"
            factor = (
                f"{source}.{attribute}={val} {operator} {target} "
                f"(no series; latest only) → {strength:.2f}"
            )
            return score, 0.7, severity, [factor]

        strength = _strength(val, operator, target)
        score = strength * 100.0
        severity = cond.get("severity", "medium") if strength > 0 else "low"
        factor = f"{source}.{attribute}={val} {operator} {target} → {strength:.2f}"
        return score, 1.0, severity, [factor]

    def _eval_sustained(self, cond, series, latest_val, source, attribute, duration):
        """Condición sostenida: cobertura de la ventana + fuerza del último valor."""
        operator = cond.get("operator")
        target = cond.get("value")
        if len(series) < MIN_SUSTAINED_SAMPLES:
            return 0.0, 0.5, "low", [
                f"{source}.{attribute}: {len(series)} sample(s) over {duration}m — "
                "insufficient for sustained"
            ]
        satisfied = sum(1 for (v, _t) in series if _strength(v, operator, target) > 0)
        coverage = satisfied / len(series)
        if coverage < MIN_SUSTAINED_COVERAGE:
            return 0.0, 1.0, "low", [
                f"{source}.{attribute}: held {coverage:.0%} "
                f"< {MIN_SUSTAINED_COVERAGE:.0%} over {duration}m"
            ]
        strength = _strength(latest_val, operator, target)
        score = strength * coverage * 100.0
        severity = cond.get("severity", "medium")
        factor = (
            f"{source}.{attribute}={latest_val} {operator} {target} "
            f"sustained {coverage:.0%} over {duration}m → {score:.0f}"
        )
        return score, 1.0, severity, [factor]
