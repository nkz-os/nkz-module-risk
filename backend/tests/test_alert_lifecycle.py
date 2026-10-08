"""Ciclo de vida de las Alert: resolución automática, caducidad y respeto al descarte."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from app.worker import alert_lifecycle as lc

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
THR = 50.0


def test_above_threshold_publishes():
    assert lc.decide(None, 80.0, THR) == lc.PUBLISH
    assert lc.decide("active", 80.0, THR) == lc.PUBLISH
    assert lc.decide("resolved", 80.0, THR) == lc.PUBLISH
    assert lc.decide("expired", 80.0, THR) == lc.PUBLISH


def test_dismissed_is_not_reopened_while_condition_holds():
    assert lc.decide("dismissed", 99.0, THR) == lc.SKIP


def test_below_threshold_resolves_active():
    assert lc.decide("active", 10.0, THR) == lc.RESOLVE


def test_below_threshold_clears_dismissal():
    # Condición terminada: la próxima vez que aparezca es un evento nuevo.
    assert lc.decide("dismissed", 10.0, THR) == lc.RESOLVE


def test_below_threshold_without_alert_does_nothing():
    assert lc.decide(None, 10.0, THR) == lc.NONE
    assert lc.decide("resolved", 10.0, THR) == lc.NONE


def test_ttl_defaults_and_override():
    assert lc.ttl_hours("weather", {}) == 12
    assert lc.ttl_hours("pest", {}) == 48
    assert lc.ttl_hours("disease", None) == 48
    assert lc.ttl_hours("agronomic", {}) == 24
    assert lc.ttl_hours("weather", {"alert_ttl_hours": 6}) == 6


def test_is_expired():
    old = (NOW - timedelta(hours=13)).isoformat().replace("+00:00", "Z")
    fresh = (NOW - timedelta(hours=11)).isoformat().replace("+00:00", "Z")
    assert lc.is_expired(old, 12, NOW)
    assert not lc.is_expired(fresh, 12, NOW)
    assert not lc.is_expired(None, 12, NOW)  # sin fecha: no se adivina


def _alert(aid, status, category="weather", observed_at=None, alert_type="frost"):
    return {
        "id": aid,
        "type": "Alert",
        "status": {"type": "Property", "value": status},
        "category": {"type": "Property", "value": category},
        "alertType": {"type": "Property", "value": alert_type},
        "observedAt": observed_at,
    }


def test_load_existing_indexes_status_by_id():
    orion = MagicMock()
    orion.query_entities.side_effect = [[_alert("a1", "active"), _alert("a2", "dismissed")], []]
    existing = lc.load_existing(orion)
    assert existing["a1"]["status"] == "active"
    assert existing["a2"]["status"] == "dismissed"


def test_mark_writes_status_and_timestamp():
    orion = MagicMock()
    lc.mark(orion, "a1", "resolved", NOW)
    eid, attrs = orion.append_entity_attrs.call_args[0]
    assert eid == "a1"
    assert attrs["status"]["value"] == "resolved"
    assert attrs["resolvedAt"]["value"] == "2026-10-08T12:00:00Z"


def test_sweep_expires_only_stale_untouched_active():
    stale = (NOW - timedelta(hours=50)).isoformat().replace("+00:00", "Z")
    existing = {
        "old": {"status": "active", "category": "pest", "alert_type": "gdd_pest", "observed_at": stale},
        "touched": {"status": "active", "category": "pest", "alert_type": "gdd_pest", "observed_at": stale},
        "dismissed": {"status": "dismissed", "category": "pest", "alert_type": "gdd_pest", "observed_at": stale},
    }
    orion = MagicMock()
    n = lc.sweep_expired(orion, existing, touched={"touched"}, catalog_config={}, now=NOW)
    assert n == 1
    assert orion.append_entity_attrs.call_args[0][0] == "old"
    assert orion.append_entity_attrs.call_args[0][1]["status"]["value"] == "expired"


def test_processor_skips_dismissed_and_resolves_cleared():
    from app.worker.processor import _apply_lifecycle

    orion = MagicMock()
    aid = "urn:ngsi-ld:Alert:t1:frost:p1"
    existing = {aid: {"status": "dismissed"}}
    touched = set()

    assert _apply_lifecycle(orion, "t1", "frost", "urn:ngsi-ld:AgriParcel:p1", 90.0, existing, touched, NOW) is False
    orion.append_entity_attrs.assert_not_called()
    assert aid in touched  # no caduca mientras siga la condición

    existing[aid]["status"] = "active"
    assert _apply_lifecycle(orion, "t1", "frost", "urn:ngsi-ld:AgriParcel:p1", 0.0, existing, set(), NOW) is False
    assert orion.append_entity_attrs.call_args[0][1]["status"]["value"] == "resolved"


def test_processor_publishes_new_alert():
    from app.worker.processor import _apply_lifecycle

    touched = set()
    assert _apply_lifecycle(MagicMock(), "t1", "frost", "urn:ngsi-ld:AgriParcel:p1", 90.0, {}, touched, NOW)
    assert "urn:ngsi-ld:Alert:t1:frost:p1" in touched


def test_load_existing_keeps_state_timestamp():
    orion = MagicMock()
    a = _alert("a1", "resolved")
    a["resolvedAt"] = {"type": "Property", "value": "2026-06-01T00:00:00Z"}
    orion.query_entities.side_effect = [[a], []]
    assert lc.load_existing(orion)["a1"]["state_at"] == "2026-06-01T00:00:00Z"


def test_purge_deletes_only_old_closed_alerts():
    old = (NOW - timedelta(days=91)).isoformat().replace("+00:00", "Z")
    recent = (NOW - timedelta(days=10)).isoformat().replace("+00:00", "Z")
    existing = {
        "old_resolved": {"status": "resolved", "state_at": old, "observed_at": old},
        "old_dismissed": {"status": "dismissed", "state_at": old, "observed_at": old},
        "recent_expired": {"status": "expired", "state_at": recent, "observed_at": old},
        "old_active": {"status": "active", "state_at": None, "observed_at": old},
        "no_dates": {"status": "resolved", "state_at": None, "observed_at": None},
    }
    existing["old_dismissed_still_on"] = {"status": "dismissed", "state_at": old, "observed_at": old}
    orion = MagicMock()
    n = lc.purge_closed(orion, existing, touched={"old_dismissed_still_on"}, now=NOW, max_age_days=90)
    deleted = {c[0][0] for c in orion.delete_entity.call_args_list}
    # Una descartada cuya condición sigue no se borra: el worker la reabriría.
    assert deleted == {"old_resolved", "old_dismissed"}
    assert n == 2
