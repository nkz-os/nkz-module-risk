"""Descarte de una Alert por el usuario: siempre permitido, sella quién y cuándo."""
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.api.alerts import dismiss

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
AID = "urn:ngsi-ld:Alert:t1:frost:p1"


def test_dismiss_sets_status_user_and_time():
    client = MagicMock()
    client.get_entity.return_value = {"id": AID, "type": "Alert"}
    out = dismiss(client, "t1", AID, "user-7", NOW)
    eid, attrs = client.append_entity_attrs.call_args[0]
    assert eid == AID
    assert attrs["status"]["value"] == "dismissed"
    assert attrs["dismissedAt"]["value"] == "2026-10-08T12:00:00Z"
    assert attrs["dismissedBy"]["value"] == "user-7"
    assert out == {"id": AID, "status": "dismissed"}


def test_dismiss_missing_alert_is_404():
    client = MagicMock()
    client.get_entity.return_value = None
    with pytest.raises(HTTPException) as e:
        dismiss(client, "t1", AID, "u", NOW)
    assert e.value.status_code == 404


def test_dismiss_rejects_non_alert_or_other_tenant_id():
    client = MagicMock()
    for bad in ["urn:ngsi-ld:AgriParcel:t1:p1", "urn:ngsi-ld:Alert:t2:frost:p1"]:
        with pytest.raises(HTTPException) as e:
            dismiss(client, "t1", bad, "u", NOW)
        assert e.value.status_code == 404
    client.append_entity_attrs.assert_not_called()


def test_route_is_registered():
    from fastapi.testclient import TestClient
    from app.main import app
    paths = TestClient(app).get("/api/risk/openapi.json").json()["paths"]
    assert "/api/risk/alerts/{alert_id}/dismiss" in paths
