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


# ── Condiciones sostenidas (duration_minutes) ────────────────────────


def test_sustained_passes_with_high_coverage():
    m = _m({"conditions": [
        {"source": "weather", "attribute": "soil_moisture_0_10cm", "operator": "<",
         "value": 15, "duration_minutes": 2880, "severity": "high"},
    ]})
    series = [(10.0, "t1"), (11.0, "t2"), (9.5, "t3"), (12.0, "t4")]  # 100% < 15
    r = m.evaluate("e", "AgriParcel", "t", _ds(weather={
        "soil_moisture_0_10cm": 10.0, "__series__soil_moisture_0_10cm": series,
    }))
    assert r["probability_score"] > 0
    assert r["severity"] == "high"
    assert r["confidence"] == 1.0


def test_sustained_fails_with_low_coverage():
    m = _m({"conditions": [
        {"source": "weather", "attribute": "soil_moisture_0_10cm", "operator": "<",
         "value": 15, "duration_minutes": 2880},
    ]})
    series = [(10.0, "t1"), (20.0, "t2"), (18.0, "t3"), (22.0, "t4")]  # 25% < 15
    r = m.evaluate("e", "AgriParcel", "t", _ds(weather={
        "soil_moisture_0_10cm": 10.0, "__series__soil_moisture_0_10cm": series,
    }))
    assert r["probability_score"] == 0.0
    assert r["severity"] == "low"


def test_sustained_insufficient_samples():
    m = _m({"conditions": [
        {"source": "weather", "attribute": "wind_speed_ms", "operator": ">",
         "value": 11, "duration_minutes": 30},
    ]})
    series = [(14.0, "t1")]  # una sola muestra no es "sostenida"
    r = m.evaluate("e", "AgriParcel", "t", _ds(weather={
        "wind_speed_ms": 14.0, "__series__wind_speed_ms": series,
    }))
    assert r["probability_score"] == 0.0


def test_duration_without_series_falls_back_to_latest():
    m = _m({"conditions": [
        {"source": "weather", "attribute": "temp_min", "operator": "<",
         "value": 0, "duration_minutes": 60, "severity": "critical"},
    ]})
    # temp_min no tiene serie (vive solo en WeatherForecast) → último valor
    r = m.evaluate("e", "AgriParcel", "t", _ds(weather={"temp_min": -2.0}))
    assert r["probability_score"] > 80
    assert r["severity"] == "critical"
    assert r["confidence"] == 0.7  # confianza rebajada: sin serie


def test_series_requests_walks_tree():
    from app.worker.models.threshold_model import series_requests
    cfg = {"logical_operator": "OR", "conditions": [
        {"source": "weather", "attribute": "soil_moisture_0_10cm", "operator": "<",
         "value": 15, "duration_minutes": 2880},
        {"logical_operator": "AND", "conditions": [
            {"source": "weather", "attribute": "wind_speed_ms", "operator": ">",
             "value": 11, "duration_minutes": 30},
            {"source": "weather", "attribute": "wind_speed_ms", "operator": ">",
             "value": 11, "duration_minutes": 60},
            {"source": "soil", "attribute": "awc", "operator": "<", "value": 15},
        ]},
    ]}
    reqs = series_requests(cfg)
    assert reqs[("weather", "soil_moisture_0_10cm")] == 2880
    assert reqs[("weather", "wind_speed_ms")] == 60  # max de 30 y 60
    assert ("soil", "awc") not in reqs  # sin duration_minutes
