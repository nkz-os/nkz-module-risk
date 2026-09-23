"""Tests del ThresholdRiskModel (árbol AND/OR recursivo)."""
from app.worker.models.threshold_model import ThresholdRiskModel


def _m(config):
    return ThresholdRiskModel("custom_test", config)


def _ds(**sources):
    return sources


def test_single_condition_matched():
    m = _m({"conditions": [
        {"source": "weather", "attribute": "temp_min", "operator": "<", "value": 0, "severity": "critical"},
    ]})
    r = m.evaluate("e", "AgriParcel", "t", _ds(weather={"temp_min": -2.0}))
    assert r["probability_score"] > 80
    assert r["severity"] == "critical"
    assert r["confidence"] == 1.0


def test_single_condition_not_matched():
    m = _m({"conditions": [
        {"source": "weather", "attribute": "temp_min", "operator": "<", "value": 0, "severity": "critical"},
    ]})
    r = m.evaluate("e", "AgriParcel", "t", _ds(weather={"temp_min": 5.0}))
    assert r["probability_score"] == 0.0
    assert r["severity"] == "low"


def test_and_group_requires_all():
    m = _m({"logical_operator": "AND", "conditions": [
        {"source": "weather", "attribute": "wind_speed_ms", "operator": ">", "value": 11},
        {"source": "soil", "attribute": "awc", "operator": "<", "value": 15},
    ]})
    r = m.evaluate("e", "AgriParcel", "t", _ds(weather={"wind_speed_ms": 14}, soil={"awc": 10}))
    assert r["probability_score"] > 0
    r2 = m.evaluate("e", "AgriParcel", "t", _ds(weather={"wind_speed_ms": 5}, soil={"awc": 10}))
    assert r2["probability_score"] == 0.0


def test_or_group_any():
    m = _m({"logical_operator": "OR", "conditions": [
        {"source": "weather", "attribute": "temp_min", "operator": "<", "value": 0},
        {"source": "weather", "attribute": "wind_speed_ms", "operator": ">", "value": 11},
    ]})
    r = m.evaluate("e", "AgriParcel", "t", _ds(weather={"temp_min": 5, "wind_speed_ms": 14}))
    assert r["probability_score"] > 0


def test_nested_group():
    m = _m({"logical_operator": "OR", "conditions": [
        {"source": "weather", "attribute": "temp_min", "operator": "<", "value": 0, "severity": "critical"},
        {"logical_operator": "AND", "conditions": [
            {"source": "weather", "attribute": "wind_speed_ms", "operator": ">", "value": 11},
            {"source": "soil", "attribute": "awc", "operator": "<", "value": 15},
        ]},
    ]})
    r = m.evaluate("e", "AgriParcel", "t", _ds(
        weather={"temp_min": 5, "wind_speed_ms": 14}, soil={"awc": 10},
    ))
    assert r["probability_score"] > 0  # la rama AND anidada se cumple


def test_in_operator():
    m = _m({"conditions": [
        {"source": "soil", "attribute": "texture", "operator": "in", "value": ["sand", "loamy_sand"], "severity": "high"},
    ]})
    r = m.evaluate("e", "AgriParcel", "t", _ds(soil={"texture": "sand"}))
    assert r["probability_score"] == 100.0
    assert r["severity"] == "high"


def test_missing_source_lowers_confidence():
    m = _m({"conditions": [
        {"source": "weather", "attribute": "temp_min", "operator": "<", "value": 0},
    ]})
    r = m.evaluate("e", "AgriParcel", "t", _ds(weather={}))
    assert r["confidence"] == 0.0
    assert r["probability_score"] == 0.0


def test_no_conditions():
    m = _m({})
    r = m.evaluate("e", "AgriParcel", "t", _ds())
    assert r["confidence"] == 0.0
    assert r["probability_score"] == 0.0
