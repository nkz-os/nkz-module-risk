from app.alerts.contract import (
    ALERT_TYPE, AlertCategory, AlertSeverity,
    build_alert_entity, alert_entity_id,
)

def test_alert_type_is_sdm():
    assert ALERT_TYPE == "Alert"

def test_severity_values():
    assert {s.value for s in AlertSeverity} == {"low", "medium", "high", "critical"}

def test_category_values():
    assert {c.value for c in AlertCategory} == {
        "weather", "agronomic", "robotic", "energy", "sensor", "crop"}

def test_alert_entity_id_shape():
    assert alert_entity_id("t1", "frost", "urn:ngsi-ld:AgriParcel:abc") == \
        "urn:ngsi-ld:Alert:t1:frost:abc"

def test_build_alert_entity():
    e = build_alert_entity(
        tenant_id="t1", entity_id="urn:ngsi-ld:AgriParcel:abc",
        entity_type="AgriParcel", alert_type="frost",
        category=AlertCategory.AGRONOMIC, severity=AlertSeverity.HIGH,
        probability_score=82.5, evaluation_data={"temp": -2.0},
        context_url="http://ctx",
    )
    assert e["type"] == "Alert"
    assert e["severity"]["value"] == "high"
    assert e["probabilityScore"]["value"] == 82.5
    assert e["refEntity"]["object"] == "urn:ngsi-ld:AgriParcel:abc"
    assert e["status"]["value"] == "active"
