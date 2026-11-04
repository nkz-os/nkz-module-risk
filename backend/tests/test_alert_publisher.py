"""Test del publicador de Alert (mecanismo Fase 0-1)."""
from unittest.mock import patch

from app.worker.alert_publisher import publish_alert


def test_publish_alert_builds_alert_and_upserts():
    with patch("app.worker.alert_publisher.SyncOrionClient") as MockClient:
        mock_inst = MockClient.return_value.__enter__.return_value
        mock_inst.upsert_entities_batch.return_value = {"upserted": 1, "errors": []}

        ok = publish_alert(
            orion_ld_url="http://orion",
            context_url="http://ctx",
            tenant_id="t1",
            entity_id="urn:ngsi-ld:AgriParcel:abc",
            entity_type="AgriParcel",
            alert_type="frost",
            category="agronomic",
            severity="high",
            probability_score=82.5,
            evaluation_data={"temp": -2.0},
        )

        assert ok is True
        args, _ = mock_inst.upsert_entities_batch.call_args
        entity = args[0][0]
        assert entity["type"] == "Alert"
        assert "RiskAssessment" not in str(entity)
        assert entity["severity"]["value"] == "high"
        assert entity["probabilityScore"]["value"] == 82.5
        assert entity["refEntity"]["object"] == "urn:ngsi-ld:AgriParcel:abc"
        assert entity["category"]["value"] == "agronomic"
        # observedAt es un temporal NGSI-LD: string ISO desnudo a nivel de entidad
        # (Orion rechaza tanto el wrapper Property como el literal DateTime).
        assert isinstance(entity["observedAt"], str)
        assert entity["observedAt"].endswith("Z")


def test_publish_alert_returns_false_when_not_upserted():
    with patch("app.worker.alert_publisher.SyncOrionClient") as MockClient:
        MockClient.return_value.__enter__.return_value.upsert_entities_batch.return_value = {
            "upserted": 0, "errors": ["boom"]
        }
        ok = publish_alert(
            orion_ld_url="http://orion", context_url="http://ctx",
            tenant_id="t1", entity_id="urn:ngsi-ld:AgriParcel:abc",
            entity_type="AgriParcel", alert_type="frost",
            category="agronomic", severity="low",
        )
        assert ok is False


def test_publish_alert_adds_plain_urgency_action():
    with patch("app.worker.alert_publisher.SyncOrionClient") as MockClient:
        mock_inst = MockClient.return_value.__enter__.return_value
        mock_inst.upsert_entities_batch.return_value = {"upserted": 1, "errors": []}
        publish_alert(
            orion_ld_url="http://orion", context_url="http://ctx", tenant_id="acmefarm",
            entity_id="urn:ngsi-ld:AgriParcel:abc", entity_type="AgriParcel",
            alert_type="frost", category="weather", severity="high",
        )
        entity = mock_inst.upsert_entities_batch.call_args[0][0][0]
        assert entity["urgency"]["value"] == "vibrate"
        assert entity["action"]["value"]["params"] == {"parcelId": "urn:ngsi-ld:AgriParcel:abc"}
        assert set(entity["plain"]["value"]) == {"es", "eu", "en"}
        assert "abc" in entity["plain"]["value"]["es"]["title"]


import pytest

from app.worker import alert_publisher

_PID = "urn:ngsi-ld:AgriParcel:abc"


@pytest.fixture(autouse=True)
def _clear_name_cache():
    alert_publisher._PARCEL_NAMES.clear()
    yield
    alert_publisher._PARCEL_NAMES.clear()


def _publish(client_cls, alert_type="frost", entity_id=_PID, entity_type="AgriParcel"):
    return publish_alert(
        orion_ld_url="http://orion", context_url="http://ctx", tenant_id="acmefarm",
        entity_id=entity_id, entity_type=entity_type,
        alert_type=alert_type, category="weather", severity="high",
    )


def test_parcel_name_used_in_texts():
    with patch("app.worker.alert_publisher.SyncOrionClient") as M:
        inst = M.return_value.__enter__.return_value
        inst.upsert_entities_batch.return_value = {"upserted": 1, "errors": []}
        inst.get_entity.return_value = {"id": _PID, "name": "Soto"}
        _publish(M)
        inst.get_entity.assert_called_once_with(_PID, options="keyValues")
        entity = inst.upsert_entities_batch.call_args[0][0][0]
        for lang in ("es", "eu", "en"):
            assert "Soto" in entity["plain"]["value"][lang]["title"]


@pytest.mark.parametrize("exc", [RuntimeError("404"), ConnectionError("500")])
def test_parcel_name_falls_back_to_short_id(exc):
    with patch("app.worker.alert_publisher.SyncOrionClient") as M:
        inst = M.return_value.__enter__.return_value
        inst.upsert_entities_batch.return_value = {"upserted": 1, "errors": []}
        inst.get_entity.side_effect = exc
        assert _publish(M) is True
        entity = inst.upsert_entities_batch.call_args[0][0][0]
        assert "abc" in entity["plain"]["value"]["es"]["title"]


def test_non_string_name_falls_back():
    with patch("app.worker.alert_publisher.SyncOrionClient") as M:
        inst = M.return_value.__enter__.return_value
        inst.upsert_entities_batch.return_value = {"upserted": 1, "errors": []}
        inst.get_entity.return_value = {"name": {"type": "Property"}}
        _publish(M)
        entity = inst.upsert_entities_batch.call_args[0][0][0]
        assert "abc" in entity["plain"]["value"]["es"]["title"]


def test_two_alerts_same_parcel_one_get():
    with patch("app.worker.alert_publisher.SyncOrionClient") as M:
        inst = M.return_value.__enter__.return_value
        inst.upsert_entities_batch.return_value = {"upserted": 1, "errors": []}
        inst.get_entity.return_value = {"name": "Soto"}
        _publish(M, "frost")
        _publish(M, "weather_alert")
        assert inst.get_entity.call_count == 1


def test_non_parcel_entity_no_get():
    with patch("app.worker.alert_publisher.SyncOrionClient") as M:
        inst = M.return_value.__enter__.return_value
        inst.upsert_entities_batch.return_value = {"upserted": 1, "errors": []}
        _publish(M, entity_id="urn:ngsi-ld:Device:d1", entity_type="Device")
        inst.get_entity.assert_not_called()
