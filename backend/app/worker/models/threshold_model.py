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


def _get(data_sources: Dict[str, Any], source: str, attribute: str) -> Any:
    src = data_sources.get(source)
    if isinstance(src, dict):
        return src.get(attribute)
    return None


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

        val = _get(data_sources, source, attribute)
        if val is None:
            return 0.0, 0.0, "low", [f"{source}.{attribute}: missing"]

        strength = _strength(val, operator, target)
        score = strength * 100.0
        severity = cond.get("severity", "medium") if strength > 0 else "low"
        factor = f"{source}.{attribute}={val} {operator} {target} → {strength:.2f}"
        return score, 1.0, severity, [factor]
