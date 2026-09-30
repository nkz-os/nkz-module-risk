"""Tests del DiseaseModelAdapter (modelos epidemiológicos → interfaz catálogo)."""
from unittest.mock import patch

from app.worker.models.disease_adapter import DiseaseModelAdapter
from app.worker.models.gubler_pm import DiseaseRiskResult


def _weather(**overrides):
    w = {"temp_avg": 25.0, "humidity": 60.0, "precip_mm": 0.0}
    w.update(overrides)
    return {"weather": w}


def test_gubler_pm_low():
    m = DiseaseModelAdapter("powdery_mildew", {})
    r = m.evaluate(
        entity_id="e",
        entity_type="AgriParcel",
        tenant_id="t",
        data_sources=_weather(),
    )
    assert r["severity"] == "low"
    assert r["probability_score"] == 30.0
    assert r["evaluation_data"]["disease"] == "powdery_mildew"
    assert r["evaluation_data"]["crop"] == "grapevine"


def test_high_risk_maps_to_score_80():
    high = DiseaseRiskResult(risk_level="HIGH", confidence="high")
    with patch("app.worker.models.gubler_pm.evaluate_gubler_pm", return_value=high):
        m = DiseaseModelAdapter("powdery_mildew", {})
        r = m.evaluate(
            entity_id="e",
            entity_type="AgriParcel",
            tenant_id="t",
            data_sources=_weather(),
        )
    assert r["severity"] == "high"
    assert r["probability_score"] == 80.0
    assert r["confidence"] == 0.9


def test_unknown_risk_code():
    m = DiseaseModelAdapter("unknown_disease", {})
    r = m.evaluate(
        entity_id="e",
        entity_type="AgriParcel",
        tenant_id="t",
        data_sources=_weather(),
    )
    assert r["probability_score"] == 0.0
