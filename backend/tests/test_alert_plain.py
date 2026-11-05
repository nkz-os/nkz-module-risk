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


@pytest.mark.parametrize("code,es,eu,en", [
    ("downy_mildew", "mildiu", "mihiu", "downy mildew"),
    ("powdery_mildew", "oídio", "hauts-izerdia", "powdery mildew"),
    ("apple_scab", "moteado del manzano", "sagarrondoko zikinkeria", "apple scab"),
    ("alternaria", "alternaria", "alternaria", "alternaria"),
    ("botrytis", "botritis", "botritisa", "botrytis"),
    ("rust_yellow", "roya amarilla", "ñabardura horia", "yellow rust"),
    ("fire_blight", "fuego bacteriano", "sute bakterianoa", "fire blight"),
])
def test_disease_code_is_localized_per_language(code, es, eu, en):
    p = plain_for("x", "disease", "high", {"disease": code}, "Soto")
    assert es in p["es"]["title"] and eu in p["eu"]["title"] and en in p["en"]["title"]
    assert "_" not in str(p)


def test_downy_mildew_spanish_title():
    p = plain_for("downy_mildew", "disease", "high", {"disease": "downy_mildew"}, "Soto")
    assert p["es"]["title"] == "Riesgo de mildiu en Soto"


def test_unknown_code_uses_nameless_variant_in_all_languages():
    p = plain_for("x", "disease", "high", {"disease": "foo_bar"}, "Soto")
    assert p == plain_for("x", "disease", "high", None, "Soto")
    assert "foo" not in str(p)


def test_friendly_free_text_passes_through():
    p = plain_for("x", "disease", "high", {"disease_name": "Roña"}, "Soto")
    for lang in ("es", "eu", "en"):
        assert "Roña" in p[lang]["title"]


def _frost_data(night, lead, tmin, hour=12):
    return {"next_night": night, "next_lead": lead, "next_tmin_c": tmin, "local_hour": hour}


def test_frost_plain_tonight_and_dated_in_three_languages():
    tonight = plain_for("frost", "agronomic", "high", _frost_data("2026-11-06", 1, 1.4), "Soto")
    assert tonight["es"] == {"title": "Posible helada en Soto", "body": "Esta noche puede bajar a 1 °C."}
    assert "Soto" in tonight["eu"]["title"] and "Soto" in tonight["en"]["title"]
    assert "1 °C" in tonight["eu"]["body"] and tonight["en"]["body"].startswith("Tonight")
    # Noche de D-1 a D con D = 2026-11-08: empieza el sábado 07/11.
    dated = plain_for("frost", "agronomic", "low", _frost_data("2026-11-08", 3, -0.6), "Soto")
    assert dated["es"]["body"] == "Noche del sábado 07/11: puede bajar a -1 °C."
    assert dated["en"]["body"] == "Night of Saturday 07/11: it may drop to -1 °C."
    assert dated["eu"]["body"] == "larunbata 07/11 gaua: -1 °C-ra jaitsi daiteke."
    for lang in ("es", "eu", "en"):
        assert "esta noche" not in dated[lang]["body"].lower()
        assert "tonight" not in dated[lang]["body"].lower()


def test_frost_plain_has_no_model_names_or_thresholds():
    p = plain_for("frost", "agronomic", "high", _frost_data("2026-11-08", 3, 2.0), "Soto")
    text = str(p).lower()
    for banned in ("ecmwf", "open-meteo", "modelo", "umbral", "3.5", "3,5"):
        assert banned not in text


def test_frost_plain_without_night_data_keeps_previous_text():
    assert plain_for("frost", "agronomic", "high", None, "Soto")["es"]["title"] == "Helada prevista en Soto"


def test_frost_plain_lead_zero_is_this_dawn():
    p = plain_for("frost", "agronomic", "high", _frost_data("2026-11-05", 0, 1.4, hour=3), "Soto")
    assert p["es"]["body"] == "Esta madrugada puede bajar a 1 °C."
    assert p["en"]["body"] == "Early this morning it may drop to 1 °C."
    assert p["eu"]["body"] == "Gaur goizaldean 1 °C-ra jaitsi daiteke."


def test_frost_plain_lead_one_before_six_is_dated():
    # A las 02:00 «esta noche» se leería como la que ya está en curso.
    p = plain_for("frost", "agronomic", "high", _frost_data("2026-11-06", 1, 1.4, hour=2), "Soto")
    assert p["es"]["body"] == "Noche del jueves 05/11: puede bajar a 1 °C."
    assert p["en"]["body"] == "Night of Thursday 05/11: it may drop to 1 °C."
    assert p["eu"]["body"] == "osteguna 05/11 gaua: 1 °C-ra jaitsi daiteke."
    at_six = plain_for("frost", "agronomic", "high", _frost_data("2026-11-06", 1, 1.4, hour=6), "Soto")
    assert at_six["es"]["body"] == "Esta noche puede bajar a 1 °C."


def test_frost_plain_lead_one_without_hour_is_dated():
    data = {"next_night": "2026-11-06", "next_lead": 1, "next_tmin_c": 1.4}
    assert plain_for("frost", "agronomic", "high", data, "Soto")["es"]["body"].startswith("Noche del")
