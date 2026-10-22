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
