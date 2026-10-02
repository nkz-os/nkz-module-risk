"""Tests del router de Alert heterogéneas (traducción + dedup)."""
from unittest.mock import MagicMock

from app.dispatcher.alert_router import _alert_to_info, dispatch_alert_entity


def test_alert_to_info_normalizes():
    entity = {
        "id": "urn:ngsi-ld:Alert:montiko:gdd_pest:parcel1",
        "type": "Alert",
        "alertType": {"type": "Property", "value": "gdd_pest"},
        "severity": {"type": "Property", "value": "high"},
        "refEntity": {"type": "Relationship", "object": "urn:ngsi-ld:AgriParcel:parcel1"},
        "status": {"type": "Property", "value": "active"},
    }
    info = _alert_to_info(entity)
    assert info["alert_type"] == "gdd_pest"
    assert info["severity"] == "high"
    assert info["entity_id"] == "urn:ngsi-ld:AgriParcel:parcel1"


def test_alert_to_info_skips_non_active():
    entity = {"id": "a", "type": "Alert", "status": {"type": "Property", "value": "resolved"}}
    assert _alert_to_info(entity) is None


def test_alert_to_info_defaults_missing_severity():
    entity = {
        "id": "a",
        "type": "Alert",
        "alertType": {"type": "Property", "value": "x"},
        "status": {"type": "Property", "value": "active"},
    }
    info = _alert_to_info(entity)
    assert info["severity"] == "medium"


def test_alert_to_info_invalid_severity_falls_back():
    entity = {
        "id": "a",
        "type": "Alert",
        "alertType": {"value": "x"},
        "severity": {"value": "WAT"},
        "status": {"value": "active"},
    }
    info = _alert_to_info(entity)
    assert info["severity"] == "medium"


def test_dispatch_alert_entity_dedups(monkeypatch):
    entity = {
        "id": "a",
        "type": "Alert",
        "alertType": {"value": "x"},
        "severity": {"value": "high"},
        "status": {"value": "active"},
    }
    disp = MagicMock()
    disp.has_delivered.return_value = True
    monkeypatch.setattr("app.dispatcher.alert_router.NotificationDispatcher", lambda: disp)
    assert dispatch_alert_entity("t", entity) is False
    disp.dispatch.assert_not_called()


def test_dispatch_alert_entity_dispatches(monkeypatch):
    entity = {
        "id": "a",
        "type": "Alert",
        "alertType": {"value": "x"},
        "severity": {"value": "high"},
        "status": {"value": "active"},
    }
    disp = MagicMock()
    disp.has_delivered.return_value = False
    monkeypatch.setattr("app.dispatcher.alert_router.NotificationDispatcher", lambda: disp)
    assert dispatch_alert_entity("t", entity) is True
    disp.dispatch.assert_called_once()
