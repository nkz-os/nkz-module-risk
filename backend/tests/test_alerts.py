"""Tests de lectura de Alert (resolución de nombre de parcela)."""
from unittest.mock import MagicMock

from app.api.alerts import _parcel_names, _unwrap_ref


def test_parcel_names_resolves_name_property_and_plain():
    client = MagicMock()
    client.query_entities.return_value = [
        {"id": "urn:ngsi-ld:AgriParcel:p1", "name": {"value": "Finca Norte"}},
        {"id": "urn:ngsi-ld:AgriParcel:p2", "name": "Finca Sur"},
        {"id": "urn:ngsi-ld:AgriParcel:p3"},  # sin name -> no entra
    ]
    assert _parcel_names(client) == {
        "urn:ngsi-ld:AgriParcel:p1": "Finca Norte",
        "urn:ngsi-ld:AgriParcel:p2": "Finca Sur",
    }


def test_unwrap_ref_relationship_and_string():
    assert _unwrap_ref({"refEntity": {"object": "urn:ngsi-ld:AgriParcel:p1"}}) == "urn:ngsi-ld:AgriParcel:p1"
    assert _unwrap_ref({"refEntity": "urn:ngsi-ld:AgriParcel:p2"}) == "urn:ngsi-ld:AgriParcel:p2"
    assert _unwrap_ref({}) == ""


def test_alert_query_defaults_to_active():
    from app.api.alerts import _build_query
    assert _build_query(None, None, "active") == 'status=="active"'
    assert _build_query("pest", "high", "active") == 'category=="pest";severity=="high";status=="active"'


def test_alert_query_all_statuses():
    from app.api.alerts import _build_query
    assert _build_query(None, None, "all") is None
    assert _build_query(None, None, "dismissed") == 'status=="dismissed"'
