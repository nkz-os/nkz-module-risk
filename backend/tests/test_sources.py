"""Tests de los fetchers de fuentes de datos (Fase 3)."""
from unittest.mock import MagicMock, patch

from app.worker import sources


def _orion(returned):
    o = MagicMock()
    o.query_entities.return_value = returned
    return o


def test_fetch_parcel_soil_prefers_extended():
    r = sources.fetch_parcel_soil(_orion([{
        "id": "urn:x", "type": "AgriSoilExtended",
        "horizons": {"value": [{
            "usdaTextureClass": "clay",
            "availableWaterCapacity": 120.0,
            "fieldCapacity": 0.3,
            "wiltingPoint": 0.1,
            "ec": 1.5,
        }]},
    }]), "urn:ngsi-ld:AgriParcel:abc")
    assert r["type"] == "AgriSoilExtended"
    assert r["texture"] == "clay"
    assert r["awc"] == 120.0
    assert r["field_capacity"] == 0.3
    assert r["wilting_point"] == 0.1
    assert r["ec"] == 1.5


def test_fetch_parcel_soil_reads_top_horizon():
    r = sources.fetch_parcel_soil(_orion([{
        "id": "urn:x", "type": "AgriSoil",
        "horizons": {"value": [
            {"availableWaterCapacity": 88.0, "usdaTextureClass": "sandy_loam"},
            {"availableWaterCapacity": 50.0},
        ]},
    }]), "urn:ngsi-ld:AgriParcel:abc")
    assert r["awc"] == 88.0
    assert r["texture"] == "sandy_loam"


def test_fetch_parcel_ndvi_extracts_value():
    r = sources.fetch_parcel_ndvi(_orion([{
        "id": "urn:x", "type": "EOProduct",
        "ndvi": {"value": 0.62},
        "savi": {"value": 0.4},
    }]), "urn:ngsi-ld:AgriParcel:abc")
    assert r["ndvi"] == 0.62
    assert r["savi"] == 0.4


def test_fetch_parcel_ndvi_none_when_absent():
    r = sources.fetch_parcel_ndvi(_orion([]), "urn:ngsi-ld:AgriParcel:abc")
    assert r is None


def test_fetch_parcel_crop_health_fields():
    r = sources.fetch_parcel_crop_health(_orion([{
        "id": "urn:x", "type": "CropHealthAssessment",
        "cwsiValue": {"value": 0.45},
        "overallSeverity": {"value": "HIGH"},
        "compactionRiskScore": {"value": 55},
    }]), "urn:ngsi-ld:AgriParcel:abc")
    assert r["cwsi"] == 0.45
    assert r["overall_severity"] == "HIGH"
    assert r["compaction_risk_score"] == 55


def test_fetch_parcel_crop_health_skips_placeholder():
    o = _orion([
        {"id": "urn:x1", "type": "CropHealthAssessment",
         "status": {"value": "pending"}, "provenance": {"value": "placeholder"}},
        {"id": "urn:x2", "type": "CropHealthAssessment",
         "overallSeverity": {"value": "CRITICAL"}, "cwsiValue": {"value": 0.8}},
    ])
    r = sources.fetch_parcel_crop_health(o, "urn:ngsi-ld:AgriParcel:abc")
    assert r["overall_severity"] == "CRITICAL"


def test_fetch_weather_alerts():
    with patch("app.worker.sources.requests.get") as get:
        get.return_value.status_code = 200
        get.return_value.json.return_value = {"alerts": [{"id": "a"}]}
        r = sources.fetch_weather_alerts("http://w", "t1", "urn:p:1")
        assert r == [{"id": "a"}]
        assert get.call_args[0][0] == "http://w/api/weather/parcel/urn:p:1/alerts"
