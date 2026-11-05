"""Aviso de helada por noche: fuente /daily, estado de la racha y ciclo de vida."""
import logging
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.worker import alert_lifecycle as lc
from app.worker import frost_alerts as fa
from app.worker import sources
from app.worker.frost_spell import Night

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
TODAY = date(2026, 11, 5)
T = "acme"
PARCEL = "urn:ngsi-ld:AgriParcel:p1"
OLD_ID = "urn:ngsi-ld:Alert:acme:frost:p1"  # sin fecha (anterior a las rachas)
MODEL_CONFIG = {
    "thresholds_by_lead": [
        {"max_lead": 4, "tmin_c": 3.5},
        {"max_lead": 5, "tmin_c": 4.0},
        {"max_lead": 7, "tmin_c": 4.5},
    ],
    "near_nights": 4,
    "horizon_nights": 7,
}
RISK = {"alert_type": "frost", "category": "agronomic", "model_type": "frost",
        "target_sdm_type": "AgriParcel", "model_config": MODEL_CONFIG}
SETTINGS = MagicMock(weather_api_url="http://w", orion_ld_url="http://o", context_url="http://c")


def nights(overrides=None, upto=7):
    overrides = overrides or {}
    out = [Night(TODAY, 10.0, "running")]
    for lead in range(1, upto + 1):
        out.append(Night(TODAY + timedelta(days=lead), overrides.get(lead, 10.0), "forecast"))
    return out


def fetched(overrides=None, upto=7):
    return sources.FrostNights(nights(overrides, upto), TODAY, "2026-11-05T06:00:00Z")


def conn_with(row=None, fail=False):
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    cur.fetchone.return_value = row
    if fail:
        cur.execute.side_effect = RuntimeError("no table")
    return conn


def alert(status="active", observed="2026-11-05T10:00:00Z", spell=None, ref=PARCEL):
    data = {"stage": "near"}
    if spell is not None:
        data["spell"] = spell
    return {"status": status, "category": "agronomic", "alert_type": "frost",
            "ref_entity": ref, "observed_at": observed, "evaluation_data": data}


def spell(start, last=None, vibrated=True, severe=False, stage="near"):
    return {"start": start.isoformat(), "last_night": (last or start).isoformat(),
            "vibrated": vibrated, "severe_vibrated": severe, "stage": stage}


def dated(start):
    return f"{OLD_ID}:{start:%Y%m%d}"


def run(fetch, existing=None, conn=None, publish_ok=True):
    existing = {} if existing is None else existing
    touched = set()
    orion = MagicMock()
    with patch.object(fa.sources, "fetch_frost_nights", return_value=fetch), \
            patch.object(fa, "publish_alert", return_value=publish_ok) as pub:
        out = fa.process_frost_parcel(
            orion, conn or conn_with(), SETTINGS, T, RISK, {"id": PARCEL, "type": "AgriParcel"},
            existing, touched, NOW)
    return out, orion, pub, touched


def marked(orion):
    return {c[0][0]: c[0][1]["status"]["value"] for c in orion.append_entity_attrs.call_args_list}


# ── fuente ────────────────────────────────────────────────────────────


def _resp(body, status=200):
    r = MagicMock(status_code=status, text="")
    r.json.return_value = body
    return r


DAILY = {
    "local_today": "2026-11-05", "forecast_issued_at": "2026-11-05T06:00:00Z",
    "days": [
        {"date": "2026-11-04", "kind": "closed", "tmin_c": 7.0, "tmin_coldest_c": 1.0},
        {"date": "2026-11-05", "kind": "running", "tmin_c": 6.0, "tmin_coldest_c": 4.0},
        {"date": "2026-11-06", "kind": "forecast", "tmin_c": 5.0, "tmin_coldest_c": 2.5},
        {"date": "2026-11-07", "kind": "forecast", "tmin_c": 5.0, "tmin_coldest_c": None},
    ],
}


def test_fetch_nights_signs_request_and_asks_two_past_days(monkeypatch):
    monkeypatch.setenv("HMAC_SECRET", "s")
    with patch("app.worker.sources.requests.get", return_value=_resp(DAILY)) as get:
        r = sources.fetch_frost_nights("http://w", T, "urn:p:1", 7)
    url, kw = get.call_args[0][0], get.call_args[1]
    assert url == "http://w/api/weather/parcel/urn:p:1/daily"
    assert kw["headers"]["X-Auth-Signature"] and kw["headers"]["X-Tenant-ID"] == T
    utc_today = datetime.now(timezone.utc).date()
    assert kw["params"]["start"] == (utc_today - timedelta(days=2)).isoformat()
    assert kw["params"]["end"] == (utc_today - timedelta(days=1)).isoformat()
    assert kw["params"]["forecast_days"] == 7 and kw["params"]["fill"] == "open_meteo"
    assert kw["timeout"] == 30
    assert r.today == TODAY
    by = {n.day: n for n in r.nights}
    assert by[date(2026, 11, 4)].tmin_c == 7.0          # cerrado: tmin_c
    assert by[date(2026, 11, 5)].tmin_c == 4.0          # running: la más fría
    assert by[date(2026, 11, 6)].tmin_c == 2.5
    assert by[date(2026, 11, 7)].tmin_c is None         # sin coldest: nunca tmin_c


@pytest.mark.parametrize("failure", [
    {"status": 500}, {"status": 401}, {"raises": ConnectionError("down")},
])
def test_fetch_nights_failure_returns_none(failure):
    with patch("app.worker.sources.requests.get") as get:
        if "raises" in failure:
            get.side_effect = failure["raises"]
        else:
            get.return_value = _resp({}, failure["status"])
        assert sources.fetch_frost_nights("http://w", T, "urn:p:1", 7) is None


def test_missing_local_today_is_no_data():
    body = {k: v for k, v in DAILY.items() if k != "local_today"}
    with patch("app.worker.sources.requests.get", return_value=_resp(body)):
        assert sources.fetch_frost_nights("http://w", T, "urn:p:1", 7) is None


# ── no_data ───────────────────────────────────────────────────────────


def test_no_data_does_not_resolve_active_alert_and_logs_error(caplog):
    existing = {dated(date(2026, 11, 6)): alert(spell=spell(date(2026, 11, 6)))}
    with caplog.at_level(logging.ERROR):
        out, orion, pub, touched = run(None, existing)
    assert out == "no_data"
    orion.append_entity_attrs.assert_not_called()
    pub.assert_not_called()
    assert touched == set()  # el TTL sigue pudiendo caducarla
    rec = [r for r in caplog.records if r.levelno == logging.ERROR][0].getMessage()
    assert T in rec and PARCEL in rec and "weather_unavailable" in rec


def test_incomplete_near_nights_is_no_data_and_does_not_resolve():
    f = sources.FrostNights([n for n in nights() if n.day != TODAY + timedelta(days=2)], TODAY, None)
    existing = {OLD_ID: alert()}
    out, orion, pub, touched = run(f, existing)
    assert out == "no_data" and not orion.append_entity_attrs.called and not touched


def test_unconfigured_catalog_is_no_data():
    risk = dict(RISK, model_config={})
    with patch.object(fa.sources, "fetch_frost_nights") as fetch:
        out = fa.process_frost_parcel(
            MagicMock(), conn_with(), SETTINGS, T, risk, {"id": PARCEL}, {}, set(), NOW)
    assert out == "no_data"
    fetch.assert_not_called()


# ── ciclo de vida ─────────────────────────────────────────────────────


def test_no_risk_resolves_active_spell_alert():
    aid = dated(date(2026, 11, 6))
    existing = {aid: alert(spell=spell(date(2026, 11, 6)))}
    out, orion, pub, touched = run(fetched(), existing)
    assert out == "resolved" and marked(orion) == {aid: "resolved"}
    pub.assert_not_called()
    assert aid in touched


def test_no_risk_without_alert_is_none():
    out, orion, pub, _ = run(fetched(), {})
    assert out == "none" and not orion.append_entity_attrs.called and not pub.called


def test_new_spell_uses_dated_id_and_resolves_previous():
    old_start = date(2026, 10, 20)
    old = dated(old_start)
    existing = {old: alert(observed="2026-10-25T10:00:00Z",
                           spell=spell(old_start, last=date(2026, 10, 24)))}
    out, orion, pub, touched = run(fetched({2: 1.0}), existing)
    new_id = dated(date(2026, 11, 7))
    assert out == "published"
    assert marked(orion) == {old: "resolved"}
    kw = pub.call_args[1]
    assert kw["alert_id"] == new_id and kw["urgency"] == "vibrate"
    assert kw["evaluation_data"]["spell"]["start"] == "2026-11-07"
    assert new_id in touched


def test_legacy_undated_frost_alert_is_resolved_on_first_run():
    existing = {OLD_ID: alert(spell=None)}
    out, orion, pub, _ = run(fetched({1: 1.0}), existing)
    assert out == "published" and marked(orion) == {OLD_ID: "resolved"}
    assert pub.call_args[1]["alert_id"] == dated(date(2026, 11, 6))


def test_later_frost_within_gap_reuses_resolved_spell_without_revibrating():
    start = date(2026, 11, 6)
    aid = dated(start)
    existing = {aid: alert(status="resolved",
                           spell=spell(start, vibrated=True, stage="near"))}
    out, orion, pub, _ = run(fetched({2: 1.0}), existing)
    assert out == "published"
    kw = pub.call_args[1]
    assert kw["alert_id"] == aid and kw["urgency"] == "silent"
    assert not orion.append_entity_attrs.called


def test_malformed_spell_is_prev_none_with_warning(caplog):
    existing = {OLD_ID: alert(spell={"start": "garbage"})}
    with caplog.at_level(logging.WARNING):
        out, _, pub, _ = run(fetched({1: 1.0}), existing)
    assert out == "published" and pub.call_args[1]["urgency"] == "vibrate"
    assert any("spell" in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)


# ── descartes (Review Focus 4) ────────────────────────────────────────


def test_dismissed_far_reopens_when_near():
    start = date(2026, 11, 9)  # lead 4 es cercano; la racha empezó lejana
    aid = dated(start)
    existing = {aid: alert(status="dismissed", spell=spell(start, vibrated=False, stage="far"))}
    out, _, pub, _ = run(fetched({4: 1.0}), existing)
    assert out == "published"
    assert pub.call_args[1]["alert_id"] == aid and pub.call_args[1]["urgency"] == "vibrate"


def test_dismissed_near_stays_skipped_same_spell():
    start = date(2026, 11, 6)
    aid = dated(start)
    existing = {aid: alert(status="dismissed", spell=spell(start, stage="near"))}
    out, orion, pub, touched = run(fetched({1: 1.0}), existing)
    assert out == "skipped" and not pub.called and not orion.append_entity_attrs.called
    assert aid in touched


def test_dismissed_then_new_spell_vibrates():
    old_start = date(2026, 10, 20)
    old = dated(old_start)
    existing = {old: alert(status="dismissed", observed="2026-10-25T10:00:00Z",
                           spell=spell(old_start, last=date(2026, 10, 24), stage="near"))}
    out, _, pub, _ = run(fetched({1: 1.0}), existing)
    assert out == "published"
    kw = pub.call_args[1]
    assert kw["alert_id"] == dated(date(2026, 11, 6)) and kw["urgency"] == "vibrate"


# ── vibración ─────────────────────────────────────────────────────────


def test_vibrate_only_when_decision_vibrates_else_silent():
    _, _, pub, _ = run(fetched({4: 1.0}), {})
    assert pub.call_args[1]["urgency"] == "vibrate"
    _, _, pub, _ = run(fetched({6: 1.0}), {})  # lejana
    assert pub.call_args[1]["urgency"] == "silent"
    _, _, pub, _ = run(fetched({1: 1.0}), {}, conn=conn_with({"vibrate": False}))
    assert pub.call_args[1]["urgency"] == "silent"  # interruptor apagado


def test_settings_read_failure_keeps_vibration_on(caplog):
    conn = conn_with(fail=True)
    with caplog.at_level(logging.ERROR):
        _, _, pub, _ = run(fetched({1: 1.0}), {}, conn=conn)
    assert pub.call_args[1]["urgency"] == "vibrate"
    conn.rollback.assert_called()
    assert any(r.levelno == logging.ERROR for r in caplog.records)


def test_failed_publish_does_not_mark_vibrated():
    existing = {}
    with pytest.raises(RuntimeError):
        run(fetched({1: 1.0}), existing, publish_ok=False)
    assert existing == {}  # el estado vive en la entidad que no se escribió
    out, _, pub, _ = run(fetched({1: 1.0}), existing)  # siguiente hora
    assert out == "published" and pub.call_args[1]["urgency"] == "vibrate"


def test_one_failing_parcel_does_not_stop_the_others():
    from app.worker.processor import _evaluate_frost_risk

    orion = MagicMock()
    orion.query_entities.return_value = [{"id": "urn:ngsi-ld:AgriParcel:a"},
                                         {"id": "urn:ngsi-ld:AgriParcel:b"}]
    outcomes = [RuntimeError("boom"), "published"]
    with patch.object(fa, "process_frost_parcel", side_effect=outcomes):
        c = _evaluate_frost_risk(orion, MagicMock(), SETTINGS, T, RISK, {}, set(), NOW, None)
    assert c == {"evaluated": 1, "errors": 1, "frost_no_data": 0}


# ── contrato y modelos ────────────────────────────────────────────────


def test_published_entity_carries_dated_id_vibrate_and_valid_plain():
    with patch("app.worker.alert_publisher.SyncOrionClient") as M:
        inst = M.return_value.__enter__.return_value
        inst.upsert_entities_batch.return_value = {"upserted": 1, "errors": []}
        inst.get_entity.return_value = {"name": "Soto"}
        with patch.object(fa.sources, "fetch_frost_nights", return_value=fetched({1: 1.4})):
            fa.process_frost_parcel(MagicMock(), conn_with(), SETTINGS, T, RISK,
                                    {"id": PARCEL}, {}, set(), NOW)
    e = inst.upsert_entities_batch.call_args[0][0][0]
    assert e["id"] == dated(date(2026, 11, 6))
    assert e["urgency"]["value"] == "vibrate"
    assert e["action"]["value"]["kind"] == "open_parcel"
    assert e["plain"]["value"]["es"]["body"] == "Esta noche puede bajar a 1 °C."
    assert e["evaluationData"]["value"]["spell"]["start"] == "2026-11-06"


def test_load_existing_exposes_ref_entity_and_evaluation_data():
    orion = MagicMock()
    e = {"id": "a1", "type": "Alert", "alertType": {"type": "Property", "value": "frost"},
         "refEntity": {"type": "Relationship", "object": PARCEL},
         "evaluationData": {"type": "Property", "value": {"spell": {"start": "2026-11-06"}}}}
    orion.query_entities.side_effect = [[e], []]
    info = lc.load_existing(orion)["a1"]
    assert info["ref_entity"] == PARCEL
    assert info["evaluation_data"] == {"spell": {"start": "2026-11-06"}}


def test_other_models_unchanged():
    from app.worker import processor

    risk = {"alert_type": "water_stress", "name": "x", "category": "agronomic",
            "model_type": "water_stress", "model_config": {}, "severity_levels": {},
            "target_sdm_type": "AgriParcel", "data_sources": [], "applicable_crop_groups": []}
    model = MagicMock()
    model.evaluate.return_value = {"probability_score": 90.0, "severity": "high",
                                   "evaluation_data": {}, "confidence": 1.0}
    orion = MagicMock()
    orion.query_entities.side_effect = lambda **kw: (
        [] if kw.get("type") == "Alert" else [{"id": PARCEL, "type": "AgriParcel"}])
    ctx = MagicMock()
    ctx.__enter__.return_value = orion
    with patch.object(processor, "_get_catalog", return_value=[risk]), \
            patch.object(processor, "SyncOrionClient", return_value=ctx), \
            patch.object(processor, "get_settings", return_value=SETTINGS), \
            patch.object(processor.RiskModelFactory, "create_model", return_value=model), \
            patch.object(processor, "_prepare_data_sources", return_value={}), \
            patch.object(processor, "publish_alert", return_value=True) as pub, \
            patch.object(processor.frost_alerts, "process_frost_parcel") as frost:
        out = processor._evaluate_risks(MagicMock(), T, include_crop_stress=False)
    frost.assert_not_called()
    pub.assert_called_once()
    assert pub.call_args[1]["alert_type"] == "water_stress"
    assert "alert_id" not in pub.call_args[1] and "urgency" not in pub.call_args[1]
    assert out["evaluated"] == 1 and out["errors"] == 0


def test_frost_removed_from_factory():
    from app.worker.models.factory import MODEL_TYPE_MAP
    assert "frost" not in MODEL_TYPE_MAP


# ── ronda de correcciones ─────────────────────────────────────────────


def test_failed_publish_keeps_previous_alert_active():
    old_start = date(2026, 10, 20)
    old = dated(old_start)
    existing = {old: alert(observed="2026-10-25T10:00:00Z",
                           spell=spell(old_start, last=date(2026, 10, 24)))}
    touched = set()
    orion = MagicMock()
    with patch.object(fa.sources, "fetch_frost_nights", return_value=fetched({1: 1.0})), \
            patch.object(fa, "publish_alert", return_value=False):
        with pytest.raises(RuntimeError):
            fa.process_frost_parcel(orion, conn_with(), SETTINGS, T, RISK,
                                    {"id": PARCEL}, existing, touched, NOW)
    orion.append_entity_attrs.assert_not_called()
    assert existing[old]["status"] == "active"


def test_failed_old_resolve_does_not_block_new_spell_or_vibration(caplog):
    old_start = date(2026, 10, 20)
    old = dated(old_start)
    existing = {old: alert(observed="2026-10-25T10:00:00Z",
                           spell=spell(old_start, last=date(2026, 10, 24)))}
    touched = set()
    orion = MagicMock()
    orion.append_entity_attrs.side_effect = RuntimeError("orion down")
    with caplog.at_level(logging.ERROR), \
            patch.object(fa.sources, "fetch_frost_nights", return_value=fetched({1: 1.0})), \
            patch.object(fa, "publish_alert", return_value=True) as pub:
        out = fa.process_frost_parcel(orion, conn_with(), SETTINGS, T, RISK,
                                      {"id": PARCEL}, existing, touched, NOW)
    assert out == "published"
    assert pub.call_args[1]["urgency"] == "vibrate"
    assert existing[old]["status"] == "active"  # se reintenta en la siguiente pasada
    assert any(old in r.getMessage() for r in caplog.records if r.levelno == logging.ERROR)


def test_publish_happens_before_resolving_old():
    order = []
    old_start = date(2026, 10, 20)
    existing = {dated(old_start): alert(observed="2026-10-25T10:00:00Z",
                                        spell=spell(old_start, last=date(2026, 10, 24)))}
    orion = MagicMock()
    orion.append_entity_attrs.side_effect = lambda *a: order.append("resolve")
    with patch.object(fa.sources, "fetch_frost_nights", return_value=fetched({1: 1.0})), \
            patch.object(fa, "publish_alert", side_effect=lambda **k: order.append("publish") or True):
        fa.process_frost_parcel(orion, conn_with(), SETTINGS, T, RISK, {"id": PARCEL},
                                existing, set(), NOW)
    assert order == ["publish", "resolve"]


def test_severe_alert_text_names_the_severe_night():
    # Noche 1 fría pero no severa (2 C); la severa (-6 C) es la del día 3.
    _, _, pub, _ = run(fetched({1: 2.0, 3: -6.0}), {})
    data = pub.call_args[1]["evaluation_data"]
    assert data["stage"] == "severe"
    assert data["next_night"] == "2026-11-06" and data["coldest_night"] == "2026-11-08"
    assert data["text_night"] == "2026-11-08" and data["text_tmin_c"] == -6.0
    from app.alerts.plain import plain_for
    p = plain_for("frost", "agronomic", "critical", data, "Soto")
    assert p["es"]["body"] == "Noche del sábado 07/11: puede bajar a -6 °C."
    assert p["en"]["body"] == "Night of Saturday 07/11: it may drop to -6 °C."
    assert p["eu"]["body"] == "larunbata 07/11 gaua: -6 °C-ra jaitsi daiteke."


def test_non_severe_text_stays_on_next_night():
    _, _, pub, _ = run(fetched({1: 2.0, 3: -1.0}), {})
    data = pub.call_args[1]["evaluation_data"]
    assert data["stage"] == "near" and data["text_night"] == data["next_night"] == "2026-11-06"
