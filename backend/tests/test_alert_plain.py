"""Textos llanos, urgencia y acción de las Alert de risk."""
import pytest

from app.alerts.contract import AlertCategory, AlertSeverity, build_alert_entity
from app.alerts.plain import action_for, plain_for, urgency_for

PARCEL = "Finca Norte"
TYPES = [
    ("downy_mildew", "disease"), ("gdd_pest", "pest"), ("gdd_pest", "agronomic"),
    ("crop_stress", "crop"), ("frost", "agronomic"), ("heat_stress", "weather"),
    ("hail_proxy", "weather"), ("wind_damage", "weather"),
    ("fire_30_30_30", "weather"), ("weather_alert", "weather"),
    ("something_new", "sensor"),
]


@pytest.mark.parametrize("alert_type,category", TYPES)
def test_three_languages_with_title_and_body(alert_type, category):
    p = plain_for(alert_type, category, "high", {"disease": "mildiu"}, PARCEL)
    assert set(p) == {"es", "eu", "en"}
    for lang in p.values():
        assert set(lang) == {"title", "body"}
        assert lang["title"].strip() and lang["body"].strip()
        assert "{{" not in lang["title"] + lang["body"]
        assert PARCEL in lang["title"]


def test_disease_uses_name_and_spanish_wording():
    p = plain_for("downy_mildew", "disease", "high", {"disease": "mildiu"}, PARCEL)
    assert p["es"]["title"] == "Riesgo de mildiu en Finca Norte"
    assert p["es"]["body"] == "Hay que tratar pronto: condiciones favorables para mildiu."


def test_disease_medium_is_watch_text():
    p = plain_for("downy_mildew", "disease", "medium", {"disease_name": "oídio"}, PARCEL)
    assert p["es"]["title"] == "Vigilar oídio en Finca Norte"


@pytest.mark.parametrize("sev", ["medium", "high"])
@pytest.mark.parametrize("data", [None, {}, {"disease": ""}])
def test_disease_without_name_has_no_dangling_separator(sev, data):
    p = plain_for("downy_mildew", "disease", sev, data, PARCEL)
    for lang in p.values():
        text = lang["title"] + " " + lang["body"]
        assert ": " not in lang["title"]
        assert "  " not in text and ":." not in text and "None" not in text
    assert p["es"]["title"] in ("Riesgo de enfermedad en Finca Norte", "Vigilar enfermedad en Finca Norte")


def test_pest_crop_stress_and_weather_spanish():
    assert plain_for("gdd_pest", "agronomic", "high", None, PARCEL)["es"] == {
        "title": "Riesgo de plaga en Finca Norte",
        "body": "Conviene revisar la parcela: la acumulación de calor favorece la plaga.",
    }
    assert plain_for("crop_stress", "crop", "medium", None, PARCEL)["es"]["body"] == \
        "Estrés detectado; mira la ficha de la parcela."
    assert plain_for("frost", "agronomic", "high", None, PARCEL)["es"] == {
        "title": "Helada prevista en Finca Norte", "body": "Protege lo sensible esta noche."}


def test_unknown_type_is_generic():
    p = plain_for("zzz", "sensor", "low", None, PARCEL)
    assert p["es"] == {"title": "Aviso en Finca Norte", "body": "Mira la ficha de la parcela."}
    assert set(p) == {"es", "eu", "en"}


@pytest.mark.parametrize("cat,sev,typ,exp", [
    ("disease", "high", "downy_mildew", "vibrate"),
    ("disease", "critical", "downy_mildew", "vibrate"),
    ("disease", "medium", "downy_mildew", "silent"),
    ("pest", "high", "gdd_pest", "vibrate"),
    ("pest", "low", "gdd_pest", "silent"),
    ("agronomic", "high", "frost", "vibrate"),
    ("weather", "critical", "hail_proxy", "vibrate"),
    ("weather", "high", "weather_alert", "vibrate"),
    ("weather", "medium", "weather_alert", "silent"),
    ("weather", "low", "weather_alert", "silent"),
    ("crop", "critical", "crop_stress", "silent"),
    ("agronomic", "critical", "water_stress", "silent"),
    ("sensor", "critical", "sensor_health", "silent"),
])
def test_urgency_table(cat, sev, typ, exp):
    assert urgency_for(cat, sev, typ) == exp


def test_urgency_accepts_enums():
    assert urgency_for(AlertCategory.DISEASE, AlertSeverity.HIGH, "x") == "vibrate"


def test_action():
    assert action_for("urn:ngsi-ld:AgriParcel:abc", "AgriParcel") == {
        "kind": "open_parcel", "params": {"parcelId": "urn:ngsi-ld:AgriParcel:abc"}}
    assert action_for("urn:ngsi-ld:Device:d1", "Device") == {"kind": "none", "params": {}}


def test_build_alert_entity_serialises_properties():
    plain = plain_for("frost", "agronomic", "high", None, PARCEL)
    act = action_for("urn:ngsi-ld:AgriParcel:abc", "AgriParcel")
    e = build_alert_entity(
        tenant_id="acmefarm", entity_id="urn:ngsi-ld:AgriParcel:abc", entity_type="AgriParcel",
        alert_type="frost", category=AlertCategory.WEATHER, severity=AlertSeverity.HIGH,
        context_url="http://ctx", plain=plain, urgency="vibrate", action=act)
    assert e["plain"] == {"type": "Property", "value": plain}
    assert e["urgency"] == {"type": "Property", "value": "vibrate"}
    assert e["action"] == {"type": "Property", "value": act}


def test_build_alert_entity_without_plain_is_unchanged():
    e = build_alert_entity(
        tenant_id="acmefarm", entity_id="urn:ngsi-ld:AgriParcel:abc", entity_type="AgriParcel",
        alert_type="frost", category=AlertCategory.WEATHER, severity=AlertSeverity.HIGH,
        context_url="http://ctx")
    assert not {"plain", "urgency", "action"} & set(e)
