"""Aviso de helada por noche: fuente /daily, estado de la racha y ciclo de vida."""
import json
import logging
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
import requests

from app.worker import alert_lifecycle as lc
from app.worker import frost_alerts as fa
from app.worker import sources
from app.worker import frost_spell as fs
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


def fetched(overrides=None, upto=7, hour=12):
    return sources.FrostNights(nights(overrides, upto), TODAY, "2026-11-05T06:00:00Z", hour)


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


def spell(start, last=None, stage="near"):
    return {"start": start.isoformat(), "last_night": (last or start).isoformat(), "stage": stage}


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
    "local_today": "2026-11-05", "local_now": "2026-11-05T07:30",
    "forecast_issued_at": "2026-11-05T06:00:00Z",
    "days": [
        {"date": "2026-11-04", "kind": "closed", "tmin_c": 7.0, "tmin_coldest_c": 1.0},
        {"date": "2026-11-05", "kind": "running", "tmin_c": 6.0, "tmin_coldest_c": 4.0},
        {"date": "2026-11-06", "kind": "forecast", "tmin_c": 5.0, "tmin_coldest_c": 2.5},
        {"date": "2026-11-07", "kind": "forecast", "tmin_c": 5.0, "tmin_coldest_c": None},
    ],
}


def test_fetch_nights_signs_request_and_asks_no_fill(monkeypatch):
    monkeypatch.setenv("HMAC_SECRET", "s")
    with patch("app.worker.sources.requests.get", return_value=_resp(DAILY)) as get:
        r = sources.fetch_frost_nights("http://w", T, "urn:p:1", 7)
    url, kw = get.call_args[0][0], get.call_args[1]
    assert url == "http://w/api/weather/parcel/urn:p:1/daily"
    assert kw["headers"]["X-Auth-Signature"] and kw["headers"]["X-Tenant-ID"] == T
    utc_today = datetime.now(timezone.utc).date()
    assert kw["params"]["start"] == (utc_today - timedelta(days=1)).isoformat()
    assert kw["params"]["end"] == (utc_today - timedelta(days=1)).isoformat()
    assert kw["params"]["forecast_days"] == 7 and kw["params"]["fill"] == "none"
    assert kw["timeout"] == 30
    assert r.today == TODAY and r.local_hour == 7
    by = {n.day: n for n in r.nights}
    assert by[date(2026, 11, 4)].tmin_c == 7.0          # cerrado: tmin_c
    assert by[date(2026, 11, 5)].tmin_c == 4.0          # running: la más fría
    assert by[date(2026, 11, 6)].tmin_c == 2.5
    assert by[date(2026, 11, 7)].tmin_c is None         # sin coldest: nunca tmin_c


@pytest.mark.parametrize("status", [400, 401, 404])
def test_fetch_nights_client_error_returns_none(status):
    with patch("app.worker.sources.requests.get", return_value=_resp({}, status)):
        assert sources.fetch_frost_nights("http://w", T, "urn:p:1", 7) is None


@pytest.mark.parametrize("failure", [
    {"status": 500}, {"status": 503}, {"raises": ConnectionError("down")},
    {"raises": requests.exceptions.Timeout("slow")},
])
def test_fetch_nights_transport_or_5xx_raises_outage(failure):
    with patch("app.worker.sources.requests.get") as get:
        if "raises" in failure:
            get.side_effect = failure["raises"]
        else:
            get.return_value = _resp({}, failure["status"])
        with pytest.raises(sources.WeatherOutage):
            sources.fetch_frost_nights("http://w", T, "urn:p:1", 7)


@pytest.mark.parametrize("field", ["local_today", "local_now"])
def test_missing_local_clock_is_no_data(field):
    body = {k: v for k, v in DAILY.items() if k != field}
    with patch("app.worker.sources.requests.get", return_value=_resp(body)):
        assert sources.fetch_frost_nights("http://w", T, "urn:p:1", 7) is None


@pytest.mark.parametrize("field", ["local_today", "local_now"])
def test_null_or_garbled_local_clock_is_no_data(field):
    for bad in (None, "garbage"):
        with patch("app.worker.sources.requests.get", return_value=_resp({**DAILY, field: bad})):
            assert sources.fetch_frost_nights("http://w", T, "urn:p:1", 7) is None


def test_only_forecast_gaps_are_logged(caplog):
    quiet = {**DAILY, "missing_reasons": {"2026-11-04": "no_data"}}
    with caplog.at_level(logging.WARNING), \
            patch("app.worker.sources.requests.get", return_value=_resp(quiet)):
        assert sources.fetch_frost_nights("http://w", T, "urn:p:1", 7) is not None
    assert not caplog.records
    loud = {**DAILY, "missing_reasons": {"2026-11-04": "no_data", "2026-11-09": "forecast_unavailable"}}
    with caplog.at_level(logging.WARNING), \
            patch("app.worker.sources.requests.get", return_value=_resp(loud)):
        sources.fetch_frost_nights("http://w", T, "urn:p:1", 7)
    msg = caplog.records[0].getMessage()
    assert "2026-11-09" in msg and "2026-11-04" not in msg


# ── no_data ───────────────────────────────────────────────────────────


def test_no_data_does_not_resolve_active_alert_and_logs_error(caplog):
    past = date(2026, 11, 4)
    existing = {dated(past): alert(spell=spell(past))}
    with caplog.at_level(logging.ERROR):
        out, orion, pub, touched = run(None, existing)
    assert out == "no_data"
    orion.append_entity_attrs.assert_not_called()
    pub.assert_not_called()
    assert touched == set()  # noche ya pasada: el TTL sigue pudiendo caducarla
    rec = [r for r in caplog.records if r.levelno == logging.ERROR][0].getMessage()
    assert T in rec and PARCEL in rec and "weather_unavailable" in rec


def test_incomplete_near_nights_is_no_data_and_does_not_resolve():
    f = sources.FrostNights([n for n in nights() if n.day != TODAY + timedelta(days=2)], TODAY, None, 12)
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


def test_frost_returning_after_a_resolve_vibrates_again_on_the_same_id():
    # Resolver libera la deduplicación en notifications: vuelve a vibrar (peca de avisar).
    start = date(2026, 11, 6)
    aid = dated(start)
    existing = {aid: alert(status="resolved",
                           spell=spell(start, stage="near"))}
    out, orion, pub, _ = run(fetched({2: 1.0}), existing)
    assert out == "published"
    kw = pub.call_args[1]
    assert kw["alert_id"] == aid and kw["urgency"] == "vibrate"
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
    existing = {aid: alert(status="dismissed", spell=spell(start, stage="far"))}
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


def test_near_keeps_sending_vibrate_on_every_publish():
    # Notifications deduplica por (id, severidad) y solo anota lo enviado: repetir
    # `vibrate` reintenta un push fallido y no vuelve a vibrar tras el éxito.
    start = date(2026, 11, 7)
    aid = dated(start)
    existing = {aid: alert(observed="2026-11-05T05:00:00Z", spell=spell(start))}
    out, _, pub, _ = run(fetched({2: 1.0}), existing)
    assert out == "published" and pub.call_args[1]["urgency"] == "vibrate"
    assert pub.call_args[1]["evaluation_data"]["spell"] == {
        "start": "2026-11-07", "last_night": "2026-11-07", "stage": "near"}


def test_running_night_before_ten_publishes_this_dawn():
    with patch("app.worker.alert_publisher.SyncOrionClient") as M:
        inst = M.return_value.__enter__.return_value
        inst.upsert_entities_batch.return_value = {"upserted": 1, "errors": []}
        inst.get_entity.return_value = {"name": "Soto"}
        f = sources.FrostNights(
            [Night(TODAY, 1.0, "running")] + nights()[1:], TODAY, None, 3)
        with patch.object(fa.sources, "fetch_frost_nights", return_value=f):
            out = fa.process_frost_parcel(
                MagicMock(), conn_with(), SETTINGS, T, RISK, {"id": PARCEL}, {}, set(), NOW)
    e = inst.upsert_entities_batch.call_args[0][0][0]
    assert out == "published"
    assert e["id"] == dated(TODAY) and e["urgency"]["value"] == "vibrate"
    assert e["plain"]["value"]["es"]["body"] == "Esta madrugada puede bajar a 1 °C."


def test_running_night_after_ten_resolves():
    aid = dated(TODAY)
    existing = {aid: alert(spell=spell(TODAY))}
    f = sources.FrostNights([Night(TODAY, 1.0, "running")] + nights()[1:], TODAY, None, 10)
    out, orion, _, _ = run(f, existing)
    assert out == "resolved" and marked(orion) == {aid: "resolved"}


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


def test_failed_publish_does_not_advance_state():
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
    assert c == {"evaluated": 1, "errors": 1, "frost_no_data": 0, "frost_parcels": 2}


# ── freno por tenant (I1) ─────────────────────────────────────────────


def _parcels(n):
    return [{"id": f"urn:ngsi-ld:AgriParcel:p{i}"} for i in range(n)]


def _frost_pass(n, fetch_side_effect, caplog):
    from app.worker.processor import _evaluate_frost_risk

    orion = MagicMock()
    orion.query_entities.return_value = _parcels(n)
    touched = set()
    with caplog.at_level(logging.ERROR), \
            patch.object(fa.sources, "fetch_frost_nights", side_effect=fetch_side_effect) as fetch, \
            patch.object(fa, "publish_alert", return_value=True):
        c = _evaluate_frost_risk(orion, conn_with(), SETTINGS, T, RISK, {}, touched, NOW, None)
    return c, fetch


def test_breaker_opens_after_three_outages_and_logs_one_count(caplog):
    outage = sources.WeatherOutage("down")
    c, fetch = _frost_pass(10, [outage, outage, outage], caplog)
    assert fetch.call_count == 3
    assert c["frost_no_data"] == 10
    lines = [r.getMessage() for r in caplog.records if "breaker_open" in r.getMessage()]
    assert len(lines) == 1 and "skipped=7" in lines[0] and T in lines[0]


def test_breaker_resets_on_any_answer(caplog):
    outage = sources.WeatherOutage("down")
    effects = [outage, outage, fetched(), outage, outage, None, outage, outage, fetched(), fetched()]
    c, fetch = _frost_pass(10, effects, caplog)
    assert fetch.call_count == 10
    assert not [r for r in caplog.records if "breaker_open" in r.getMessage()]
    assert c["frost_no_data"] == 7


def test_breaker_does_not_open_on_answers_without_data(caplog):
    # 200 sin local_today (parcela sin geometría) no es caída: no apaga al resto.
    c, fetch = _frost_pass(5, [None] * 5, caplog)
    assert fetch.call_count == 5 and c["frost_no_data"] == 5


def test_breaker_open_keeps_a_frost_still_ahead():
    breaker = fa.FrostBreaker()
    for _ in range(fa.BREAKER_FAILURES):
        breaker.failure()
    start = date(2026, 11, 7)
    aid = dated(start)
    existing = {aid: alert(spell=spell(start))}
    touched = set()
    with patch.object(fa.sources, "fetch_frost_nights") as fetch:
        out = fa.process_frost_parcel(
            MagicMock(), conn_with(), SETTINGS, T, RISK, {"id": PARCEL}, existing, touched, NOW, breaker)
    assert out == "no_data" and aid in touched and breaker.skipped == 1
    fetch.assert_not_called()


# ── sin dato con la helada por delante (I5) ───────────────────────────


def test_no_data_keeps_alert_while_its_night_is_ahead():
    for last, kept in ((date(2026, 11, 7), True), (TODAY, True), (date(2026, 11, 4), False)):
        aid = dated(date(2026, 11, 3))
        existing = {aid: alert(spell=spell(date(2026, 11, 3), last=last))}
        out, orion, pub, touched = run(None, existing)
        assert out == "no_data" and (aid in touched) is kept, last
        assert not orion.append_entity_attrs.called and not pub.called


def test_no_data_keeps_every_active_alert_whose_night_is_ahead():
    a, b, past = dated(date(2026, 11, 6)), dated(date(2026, 11, 10)), dated(date(2026, 11, 1))
    existing = {
        a: alert(observed="2026-11-01T10:00:00Z", spell=spell(date(2026, 11, 6))),
        b: alert(observed="2026-11-05T10:00:00Z", spell=spell(date(2026, 11, 10))),
        past: alert(observed="2026-11-01T10:00:00Z", spell=spell(date(2026, 11, 1))),
    }
    _, _, _, touched = run(None, existing)
    assert touched == {a, b}


def test_no_data_ignores_resolved_alert_and_other_parcels():
    aid = dated(date(2026, 11, 7))
    other = "urn:ngsi-ld:Alert:acme:frost:p2:20261107"
    existing = {
        aid: alert(status="resolved", spell=spell(date(2026, 11, 7))),
        other: alert(spell=spell(date(2026, 11, 7)), ref="urn:ngsi-ld:AgriParcel:p2"),
    }
    _, _, _, touched = run(None, existing)
    assert touched == set()


def test_no_data_partial_keeps_alert_and_logs_reason(caplog):
    start = date(2026, 11, 11)  # lead 6
    aid = dated(start)
    existing = {aid: alert(spell=spell(start, stage="far"))}
    with caplog.at_level(logging.ERROR):
        out, orion, _, touched = run(fetched({5: None, 6: None, 7: None}), existing)
    assert out == "no_data" and not orion.append_entity_attrs.called and aid in touched
    assert any("reason=partial" in r.getMessage() for r in caplog.records)


def test_no_data_with_expiry_sweep_only_expires_past_nights():
    ahead = dated(date(2026, 11, 7))
    past = dated(date(2026, 11, 1))
    old = "2026-11-03T10:00:00Z"  # 50 h: fuera del TTL de 24 h
    for aid, start in ((ahead, date(2026, 11, 7)), (past, date(2026, 11, 1))):
        existing = {aid: alert(observed=old, spell=spell(start))}
        out, orion, _, touched = run(None, existing)
        assert out == "no_data"
        n = lc.sweep_expired(orion, existing, touched, {"frost": MODEL_CONFIG}, NOW)
        assert n == (0 if aid == ahead else 1)


# ── regla de publicación (I9) ─────────────────────────────────────────


def _published_state(fetch, **alert_kw):
    """Publica una vez y devuelve la Alert tal y como la leería la siguiente pasada."""
    out, _, pub, _ = run(fetch, {})
    assert out == "published"
    kw = pub.call_args[1]
    return kw["alert_id"], {
        **alert(**alert_kw), "evaluation_data": kw["evaluation_data"],
    }


def test_publish_rule_unchanged_key_recent_does_not_write():
    aid, info = _published_state(fetched({3: 1.0}), observed="2026-11-05T11:00:00Z")
    out, orion, pub, touched = run(fetched({3: 1.0}), {aid: info})
    assert out == "unchanged" and not pub.called and aid in touched
    assert not orion.append_entity_attrs.called


def test_publish_rule_rounding_same_degree_does_not_write():
    aid, info = _published_state(fetched({3: 1.1}), observed="2026-11-05T11:00:00Z")
    assert run(fetched({3: 1.4}), {aid: info})[0] == "unchanged"


@pytest.mark.parametrize("changed", [
    {3: 0.4},                 # mínima redondeada
    {3: -6.0},                # escalón y severidad
    {2: 1.0, 3: 1.0},         # noche del texto
])
def test_publish_rule_changed_key_writes(changed):
    aid, info = _published_state(fetched({3: 1.0}), observed="2026-11-05T11:59:00Z")
    out, _, pub, _ = run(fetched(changed), {aid: info})
    assert out == "published" and pub.called


def test_publish_rule_urgency_change_writes():
    aid, info = _published_state(fetched({3: 1.0}), observed="2026-11-05T11:59:00Z")
    out, _, pub, _ = run(fetched({3: 1.0}, hour=23), {aid: info})  # horas de silencio
    assert out == "published" and pub.call_args[1]["urgency"] == "silent"


def test_publish_rule_six_hours_refreshes_a_silent_alert():
    aid, info = _published_state(fetched({6: 1.0}), observed="2026-11-05T06:00:00Z")
    out, _, pub, _ = run(fetched({6: 1.0}), {aid: info})
    assert out == "published" and pub.call_args[1]["urgency"] == "silent"
    aid, info = _published_state(fetched({6: 1.0}), observed="2026-11-05T06:00:01Z")
    assert run(fetched({6: 1.0}), {aid: info})[0] == "unchanged"


def test_publish_rule_vibrate_retries_every_three_hours_when_not_imminent():
    for lead in (1, 2, 4):  # lead 1 a mediodía no es inminente
        aid, info = _published_state(fetched({lead: 1.0}), observed="2026-11-05T09:00:01Z")
        assert run(fetched({lead: 1.0}), {aid: info})[0] == "unchanged", lead
        aid, info = _published_state(fetched({lead: 1.0}), observed="2026-11-05T09:00:00Z")
        out, _, pub, _ = run(fetched({lead: 1.0}), {aid: info})
        assert out == "published" and pub.call_args[1]["urgency"] == "vibrate", lead


def test_publish_rule_vibrate_retries_hourly_while_imminent():
    at_19 = datetime(2026, 11, 5, 19, 0, tzinfo=timezone.utc)
    _, _, pub, _ = run(fetched({1: 1.0}, hour=19), {})
    aid = pub.call_args[1]["alert_id"]
    info = {**alert(observed="2026-11-05T18:59:00Z"), "evaluation_data": pub.call_args[1]["evaluation_data"]}
    with patch.object(fa.sources, "fetch_frost_nights", return_value=fetched({1: 1.0}, hour=19)), \
            patch.object(fa, "publish_alert", return_value=True) as pub2:
        out = fa.process_frost_parcel(MagicMock(), conn_with(), SETTINGS, T, RISK, {"id": PARCEL},
                                      {aid: info}, set(), at_19)
    assert out == "published" and pub2.call_args[1]["urgency"] == "vibrate"


def test_publish_rule_silent_close_does_not_retry():
    muted = conn_with({"vibrate": False})
    out, _, pub, _ = run(fetched({1: 1.0}), {}, conn=muted)
    aid = pub.call_args[1]["alert_id"]
    info = {**alert(observed="2026-11-05T11:59:00Z"), "evaluation_data": pub.call_args[1]["evaluation_data"]}
    assert run(fetched({1: 1.0}), {aid: info}, conn=muted)[0] == "unchanged"


def test_publish_rule_silent_alert_refreshes_text_when_its_wording_changes():
    # Silenciada: a medianoche pasa de lead 1 («esta noche») a lead 0 («esta madrugada»).
    muted = conn_with({"vibrate": False})
    _, _, pub, _ = run(fetched({1: 1.0}, hour=23), {}, conn=muted)
    aid = pub.call_args[1]["alert_id"]
    info = {**alert(observed="2026-11-05T23:00:00Z"), "evaluation_data": pub.call_args[1]["evaluation_data"]}
    tomorrow = TODAY + timedelta(days=1)
    roll = sources.FrostNights(
        [Night(tomorrow + timedelta(days=i), 1.0 if i == 0 else 10.0, "running" if i == 0 else "forecast")
         for i in range(0, 8)], tomorrow, None, 0)
    with patch.object(fa.sources, "fetch_frost_nights", return_value=roll), \
            patch.object(fa, "publish_alert", return_value=True) as pub2:
        out = fa.process_frost_parcel(
            MagicMock(), muted, SETTINGS, T, RISK, {"id": PARCEL}, {aid: info}, set(),
            datetime(2026, 11, 5, 23, 30, tzinfo=timezone.utc))
    assert out == "published" and pub2.call_args[1]["evaluation_data"]["text_lead"] == 0


@pytest.mark.parametrize("status", ["resolved", "expired"])
def test_publish_rule_closed_alert_with_same_key_is_reopened(status):
    # El aviso volvió a estar en riesgo: «sin cambios» no puede dejarlo cerrado.
    aid, info = _published_state(fetched({3: 1.0}), status=status, observed="2026-11-05T11:59:00Z")
    out, _, pub, _ = run(fetched({3: 1.0}), {aid: info})
    assert out == "published" and pub.called


def test_publish_rule_unreadable_date_publishes():
    aid, info = _published_state(fetched({3: 1.0}), observed=None)
    assert run(fetched({3: 1.0}), {aid: info})[0] == "published"


def test_unchanged_still_retries_resolving_the_old_alert():
    aid, info = _published_state(fetched({3: 1.0}), observed="2026-11-05T11:00:00Z")
    old = dated(date(2026, 10, 20))
    existing = {aid: info, old: alert(observed="2026-10-25T10:00:00Z",
                                      spell=spell(date(2026, 10, 20), last=date(2026, 10, 24)))}
    out, orion, pub, _ = run(fetched({3: 1.0}), existing)
    assert out == "unchanged" and not pub.called and marked(orion) == {old: "resolved"}


def test_unmuting_mid_spell_vibrates_on_the_next_run():
    muted = conn_with({"vibrate": False})
    _, _, pub, _ = run(fetched({3: 1.0}), {}, conn=muted)
    assert pub.call_args[1]["urgency"] == "silent"
    aid = pub.call_args[1]["alert_id"]
    info = {**alert(observed="2026-11-05T11:59:00Z"), "evaluation_data": pub.call_args[1]["evaluation_data"]}
    out, _, pub2, _ = run(fetched({3: 1.0}), {aid: info})
    assert out == "published" and pub2.call_args[1]["urgency"] == "vibrate"


# ── noches anunciadas (C1 de la revisión 2) ───────────────────────────


def test_announced_nights_are_stored_only_by_vibrate_publishes():
    _, _, pub, _ = run(fetched({2: 1.0, 6: 1.0}), {})  # dos cadenas: cercana y lejana
    assert [c[1]["evaluation_data"]["announced"] for c in pub.call_args_list] == [["2026-11-07"], []]
    _, _, pub, _ = run(fetched({2: 1.0}, hour=23), {})  # horas de silencio: silent
    assert pub.call_args[1]["evaluation_data"]["announced"] == []


def test_announced_nights_round_trip_and_grow_only_on_a_publish_that_pushes():
    aid, info = _published_state(fetched({2: 1.0}), observed="2026-11-05T09:00:00Z")
    spells = fa._spells_of({aid: info})
    assert spells[0].announced == (date(2026, 11, 7),) and spells[0].announced_severity == "high"
    out, _, pub, _ = run(fetched({2: 1.0, 3: 1.0}), {aid: info})  # misma severidad: deduplicado
    assert out == "published"
    assert pub.call_args[1]["evaluation_data"]["announced"] == ["2026-11-07"]
    out, _, pub, _ = run(fetched({2: -6.0, 3: 1.0}), {aid: info})  # sube a critical: empuja
    data = pub.call_args[1]["evaluation_data"]
    assert data["announced"] == ["2026-11-07", "2026-11-08"] and data["announced_severity"] == "critical"


def test_unreadable_announced_reads_as_never_announced(caplog):
    aid, info = _published_state(fetched({2: 1.0}), observed="2026-11-05T09:00:00Z")
    info["evaluation_data"]["announced"] = ["garbage"]
    assert fa._spells_of({aid: info})[0].announced == ()


# ── historial y salida del worker ─────────────────────────────────────


def test_closed_frost_alerts_are_purged_after_fourteen_days_others_after_ninety():
    def at(days):
        return (NOW - timedelta(days=days)).isoformat().replace("+00:00", "Z")
    existing = {
        "frost_15": {"status": "resolved", "alert_type": "frost", "state_at": at(15)},
        "frost_13": {"status": "expired", "alert_type": "frost", "state_at": at(13)},
        "pest_15": {"status": "resolved", "alert_type": "gdd_pest", "state_at": at(15)},
        "pest_91": {"status": "resolved", "alert_type": "gdd_pest", "state_at": at(91)},
    }
    orion = MagicMock()
    assert lc.purge_closed(orion, existing, touched=set(), now=NOW) == 2
    assert {c[0][0] for c in orion.delete_entity.call_args_list} == {"frost_15", "pest_91"}


def test_frost_pass_counts_parcels():
    from app.worker.processor import _evaluate_frost_risk

    orion = MagicMock()
    orion.query_entities.return_value = _parcels(3)
    with patch.object(fa, "process_frost_parcel", side_effect=["no_data", "published", "no_data"]):
        c = _evaluate_frost_risk(orion, MagicMock(), SETTINGS, T, RISK, {}, set(), NOW, None)
    assert c == {"evaluated": 1, "errors": 0, "frost_no_data": 2, "frost_parcels": 3}


def _main_with(results):
    from app.worker import processor

    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value.fetchall.return_value = [
        {"tenant_id": t} for t in results]
    with patch("app.db.get_conn", return_value=conn), \
            patch.object(processor, "evaluate_risks_for_tenant",
                         side_effect=lambda c, t: results[t]):
        return processor.main()


def test_worker_exits_non_zero_when_a_tenant_had_no_frost_data_at_all(caplog):
    ok = {"evaluated": 1, "errors": 0, "frost_no_data": 1, "frost_parcels": 3}
    dark = {"evaluated": 0, "errors": 0, "frost_no_data": 4, "frost_parcels": 4}
    with caplog.at_level(logging.ERROR):
        assert _main_with({"tenant-a": ok, "tenant-b": dark}) == 1
    lines = [r.getMessage() for r in caplog.records if "frost_all_no_data" in r.getMessage()]
    assert len(lines) == 1 and "tenant=tenant-b" in lines[0]
    assert "parcels=4" in lines[0] and "no_data=4" in lines[0]


def test_worker_exits_zero_otherwise():
    assert _main_with({}) == 0
    assert _main_with({"tenant-a": {"evaluated": 0, "errors": 2, "frost_no_data": 0,
                                    "frost_parcels": 0}}) == 0
    assert _main_with({"tenant-a": {"evaluated": 0, "errors": 0, "frost_no_data": 0}}) == 0
    assert _main_with({"tenant-a": {"evaluated": 2, "errors": 0, "frost_no_data": 1,
                                    "frost_parcels": 3}}) == 0


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


# ── processor: orden del catálogo, paginado, resultado ────────────────


def test_catalog_runs_frost_first_and_keeps_the_rest_in_order():
    from app.worker import processor

    catalog = [{"alert_type": "a", "model_type": "x"}, {"alert_type": "b", "model_type": "y"},
               {"alert_type": "frost", "model_type": "frost"}, {"alert_type": "c", "model_type": "z"}]
    assert [r["alert_type"] for r in processor._frost_first(catalog)] == ["frost", "a", "b", "c"]


def test_get_entities_pages_until_a_short_page():
    from app.worker import processor

    page = lc._PAGE
    orion = MagicMock()
    orion.query_entities.side_effect = [
        [{"id": f"p{i}"} for i in range(page)],
        [{"id": f"q{i}"} for i in range(page)],
        [{"id": "last"}],
    ]
    out = processor._get_entities(orion, "AgriParcel")
    assert len(out) == 2 * page + 1
    offsets = [c.kwargs["offset"] for c in orion.query_entities.call_args_list]
    assert offsets == [0, page, 2 * page]
    assert all(c.kwargs["limit"] == page and c.kwargs["type"] == "AgriParcel"
               for c in orion.query_entities.call_args_list)


def test_get_entities_exact_page_stops_on_empty():
    from app.worker import processor

    page = lc._PAGE
    orion = MagicMock()
    orion.query_entities.side_effect = [[{"id": f"p{i}"} for i in range(page)], []]
    assert len(processor._get_entities(orion, "AgriParcel")) == page


def test_empty_catalog_reports_frost_no_data_key():
    from app.worker import processor

    with patch.object(processor, "_get_catalog", return_value=[]), \
            patch.object(processor, "get_settings", return_value=SETTINGS):
        assert processor._evaluate_risks(MagicMock(), T) == {
            "evaluated": 0, "errors": 0, "frost_no_data": 0, "frost_parcels": 0}


# ── ida y vuelta del estado de la racha por Orion (forma normalizada) ─


class _NormalizedOrion:
    """Guarda y devuelve Alert como las devuelve Orion: observedAt es Property con cadena."""

    def __init__(self):
        self.entities = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_entity(self, eid, options=None):
        return {"name": "Soto"}

    def upsert_entities_batch(self, ents):
        for raw in ents:
            e = dict(raw)
            e.pop("@context", None)
            e["observedAt"] = {"type": "Property", "value": raw["observedAt"]}
            self.entities[e["id"]] = json.loads(json.dumps(e))
        return {"upserted": len(ents), "errors": []}

    def query_entities(self, type=None, limit=100, offset=0, q=None):
        return [json.loads(json.dumps(v)) for v in self.entities.values()][offset:offset + limit]

    def append_entity_attrs(self, eid, attrs):
        self.entities[eid].update(json.loads(json.dumps(attrs)))


def _roundtrip_run(broker, fetch, now):
    existing = lc.load_existing(broker)
    touched = set()
    with patch("app.worker.alert_publisher.SyncOrionClient", return_value=broker), \
            patch("app.alerts.contract._now", lambda: now.isoformat().replace("+00:00", "Z")), \
            patch.object(fa.sources, "fetch_frost_nights", return_value=fetch):
        out = fa.process_frost_parcel(
            broker, conn_with(), SETTINGS, T, RISK, {"id": PARCEL, "type": "AgriParcel"},
            existing, touched, now)
    return out, existing, touched


def test_round_trip_two_runs_no_second_publish_keeps_spell():
    broker = _NormalizedOrion()
    f = fetched({3: 1.0})
    out, _, _ = _roundtrip_run(broker, f, NOW)
    assert out == "published"
    aid = dated(date(2026, 11, 8))
    written = broker.entities[aid]
    assert written["observedAt"]["type"] == "Property"
    assert written["evaluationData"]["value"]["spell"] == {
        "start": "2026-11-08", "last_night": "2026-11-08", "stage": "near"}

    before = json.dumps(broker.entities[aid], sort_keys=True)
    out, existing, touched = _roundtrip_run(broker, f, NOW + timedelta(hours=1))
    assert out == "unchanged" and aid in touched
    assert json.dumps(broker.entities[aid], sort_keys=True) == before  # sin escritura

    # El estado leído de la Alert normalizada es el que escribió la primera pasada.
    info = existing[aid]
    assert info["ref_entity"] == PARCEL and info["status"] == "active"
    assert fa._spells_of(fa._frost_alerts_of(existing, PARCEL)) == [
        fs.ExistingSpell(aid, "active", date(2026, 11, 8), date(2026, 11, 8), "near", "high",
                         (date(2026, 11, 8),), "high")]
    assert lc._parse(info["observed_at"]) == NOW


def test_round_trip_peak_severity_survives_a_drop_in_stage():
    broker = _NormalizedOrion()
    _roundtrip_run(broker, fetched({3: -6.0}), NOW)
    aid = dated(date(2026, 11, 8))
    assert broker.entities[aid]["severity"]["value"] == "critical"
    out, _, _ = _roundtrip_run(broker, fetched({3: 1.0}), NOW + timedelta(hours=1))
    e = broker.entities[aid]
    assert out == "published" and e["evaluationData"]["value"]["stage"] == "near"
    assert e["severity"]["value"] == "critical"
    assert e["evaluationData"]["value"]["peak_severity"] == "critical"
    assert e["plain"]["value"]["es"]["body"] == "Noche del sábado 07/11: puede bajar a 1 °C."


def test_round_trip_two_active_alerts_over_runs_without_a_second_write():
    broker = _NormalizedOrion()
    f = fetched({1: 1.0, 5: 1.0})  # dos cadenas separadas: dos Alert activas
    out, _, _ = _roundtrip_run(broker, f, NOW)
    first, second = dated(date(2026, 11, 6)), dated(date(2026, 11, 10))
    assert out == "published"
    assert {k: v["status"]["value"] for k, v in broker.entities.items()} == {
        first: "active", second: "active"}

    before = json.dumps(broker.entities, sort_keys=True)
    out, existing, touched = _roundtrip_run(broker, fetched({1: 1.0, 5: 1.0}, hour=13),
                                            NOW + timedelta(hours=1))
    # Ninguna es inminente (lead 1 antes de las 18:00): ninguna se reescribe.
    assert out == "unchanged" and {first, second} <= touched
    assert json.loads(json.dumps(broker.entities)) == json.loads(before)
    spells = fa._spells_of(fa._frost_alerts_of(existing, PARCEL))
    assert [(s.alert_id, s.start, s.last_night) for s in spells] == [
        (first, date(2026, 11, 6), date(2026, 11, 6)),
        (second, date(2026, 11, 10), date(2026, 11, 10))]


def test_round_trip_failed_old_resolve_is_retried_next_run():
    broker = _NormalizedOrion()
    old_start = date(2026, 10, 20)
    old_nights = sources.FrostNights(
        [Night(old_start + timedelta(days=i), 1.0 if i == 1 else 10.0, "forecast") for i in range(0, 8)],
        old_start, None, 12)
    _roundtrip_run(broker, old_nights, datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc))
    old_id = dated(old_start + timedelta(days=1))

    new_id = dated(date(2026, 11, 8))
    with patch.object(_NormalizedOrion, "append_entity_attrs", side_effect=RuntimeError("down")):
        out, _, _ = _roundtrip_run(broker, fetched({3: 1.0}), NOW)
    assert out == "published"
    assert broker.entities[old_id]["status"]["value"] == "active"

    out, _, _ = _roundtrip_run(broker, fetched({3: 1.0}), NOW + timedelta(hours=1))
    assert out == "unchanged"
    assert broker.entities[new_id]["observedAt"]["value"].startswith("2026-11-05T12:00")
    assert broker.entities[old_id]["status"]["value"] == "resolved"


def test_round_trip_resolved_alert_keeps_state_for_the_next_spell():
    broker = _NormalizedOrion()
    _roundtrip_run(broker, fetched({3: 1.0}), NOW)
    aid = dated(date(2026, 11, 8))
    out, _, _ = _roundtrip_run(broker, fetched(), NOW + timedelta(hours=1))
    assert out == "resolved" and broker.entities[aid]["status"]["value"] == "resolved"
    # La misma noche vuelve dentro de la hora: reabre el mismo id.
    out, _, _ = _roundtrip_run(broker, fetched({3: 1.0}), NOW + timedelta(hours=2))
    assert out == "published" and broker.entities[aid]["status"]["value"] == "active"
    assert broker.entities[aid]["urgency"]["value"] == "vibrate"
