"""Lógica pura del aviso de helada: noches en riesgo, cadenas y una Alert por cadena."""
from datetime import date, timedelta

from app.worker import frost_spell as fs
from app.worker.frost_spell import ExistingSpell, FrostConfig, Night

TODAY = date(2026, 12, 10)
APPROVED = {
    "thresholds_by_lead": [
        {"max_lead": 4, "tmin_c": 3.5},
        {"max_lead": 5, "tmin_c": 4.0},
        {"max_lead": 7, "tmin_c": 4.5},
    ],
    "near_nights": 4,
    "horizon_nights": 7,
}
# Misma forma que el catálogo: así un cambio de formato no queda oculto por el fixture.
CFG = fs.config_from_model_config(APPROVED)
WARM = 10.0
NOON = 12  # hora local: la noche de hoy ya no dispara


def D(lead: int) -> date:
    return TODAY + timedelta(days=lead)


def _nights(overrides=None, upto=7, running=WARM):
    """Noches lead 0..upto templadas, con tmin sobreescrita por lead."""
    overrides = overrides or {}
    out = [Night(TODAY, running, "running")]
    for lead in range(1, upto + 1):
        out.append(Night(D(lead), overrides.get(lead, WARM), "forecast"))
    return out


def _spell(start, last=None, status="active", stage="near", aid=None, peak=None):
    last = start if last is None else last
    return ExistingSpell(aid or f"a{start:%m%d}", status, start, last, stage, peak)


def _decide(overrides=None, existing=(), muted=False, hour=NOON, running=WARM, upto=7):
    return fs.decide_chains(_nights(overrides, upto, running), list(existing), CFG, TODAY, muted, hour)


def _one(ev):
    assert len(ev.chains) == 1, ev.chains
    return ev.chains[0]


# ── umbrales, escalón y severidad ─────────────────────────────────────


def test_threshold_follows_lead():
    assert _one(_decide({1: 3.0})).stage == "near"
    assert _one(_decide({6: 4.4})).stage == "far"
    assert _decide({6: 4.6}).chains == ()
    assert _one(_decide({5: 3.9})).stage == "far"
    assert _decide({5: 4.1}).chains == ()
    assert _decide({8: 3.0}, upto=8).chains == ()  # sin fila de umbral


def test_far_night_is_low_and_silent():
    c = _one(_decide({6: 1.0}))
    assert c.stage == "far" and c.severity == "low" and c.urgency == "silent"


def test_far_at_or_below_zero_is_medium():
    assert _one(_decide({6: 0.0})).severity == "medium"


def test_near_is_high_and_vibrates_in_the_day():
    c = _one(_decide({2: 1.0}))
    assert c.stage == "near" and c.severity == "high" and c.urgency == "vibrate"


def test_severe_needs_a_near_night_at_severe_threshold():
    assert _one(_decide({2: -5.0})).severity == "critical"
    assert _one(_decide({6: -8.0})).stage == "far"  # severa pero lejana


def test_muted_keeps_stage_and_severity_but_never_vibrates():
    c = _one(_decide({1: -6.0}, muted=True, hour=3, running=1.0))
    assert c.stage == "severe" and c.severity == "critical" and c.urgency == "silent"


def test_reopen_only_when_stage_rises():
    assert fs.should_reopen_dismissed("far", "near")
    assert fs.should_reopen_dismissed("near", "severe")
    assert fs.should_reopen_dismissed("far", "severe")
    assert not fs.should_reopen_dismissed("near", "near")
    assert not fs.should_reopen_dismissed("near", "far")
    assert not fs.should_reopen_dismissed("severe", "near")


# ── configuración ─────────────────────────────────────────────────────


def test_config_missing_thresholds_returns_none():
    assert fs.config_from_model_config({}) is None
    assert fs.config_from_model_config({"near_nights": 4, "horizon_nights": 7}) is None
    assert fs.config_from_model_config({"thresholds_by_lead": [], "near_nights": 4, "horizon_nights": 7}) is None
    assert fs.config_from_model_config({"thresholds_by_lead": [[4, 3.5]], "horizon_nights": 7}) is None


def test_config_malformed_rows_return_none_not_raise():
    base = {"near_nights": 4, "horizon_nights": 7}
    for bad in (
        [{"max_lead": 4}],
        [{"tmin_c": 3.5}],
        [{"max_lead": "x", "tmin_c": 3.5}],
        [{"max_lead": 4, "tmin_c": None}],
        [{"max_lead": 4, "tmin_c": float("nan")}],
        [5],
        "abc",
        [None],
    ):
        assert fs.config_from_model_config({**base, "thresholds_by_lead": bad}) is None


def test_config_parses_approved_catalog_json():
    assert CFG == FrostConfig(((4, 3.5), (5, 4.0), (7, 4.5)), near_nights=4, horizon_nights=7)
    c = _one(fs.decide_chains(_nights({2: 1.0}), [], CFG, TODAY, False, NOON))
    assert c.stage == "near" and c.urgency == "vibrate" and c.severity == "high"


def test_config_accepts_pairs():
    cfg = fs.config_from_model_config(
        {"thresholds_by_lead": [[7, 4.5], [4, 3.5], [5, 4.0]], "near_nights": 4, "horizon_nights": 7}
    )
    assert cfg == CFG


# ── sin dato ──────────────────────────────────────────────────────────


def test_null_or_missing_near_night_is_no_data_and_keeps_alerts_ahead():
    ahead, past = _spell(D(2)), _spell(D(-3), aid="old")
    ev = _decide({1: -3.0, 2: None}, existing=[ahead, past])
    assert ev.no_data and ev.reason == "incomplete_nights" and not ev.chains
    assert ev.keep_ids == (ahead.alert_id,) and not ev.resolve_ids
    nights = [n for n in _nights({1: 1.0}) if n.day != D(3)]
    assert fs.decide_chains(nights, [], CFG, TODAY, False, NOON).no_data
    assert fs.decide_chains([], [], CFG, TODAY, False, NOON).no_data


def test_nan_tmin_in_near_window_is_no_data():
    assert _decide({2: float("nan")}).no_data


def test_null_far_night_with_nothing_at_risk_is_partial_and_keeps_its_alert():
    far = _spell(D(6), stage="far")
    ev = _decide({5: None, 6: None, 7: None}, existing=[far])
    assert not ev.no_data and ev.reason == "partial" and not ev.chains
    assert ev.keep_ids == (far.alert_id,) and not ev.resolve_ids


def test_far_null_only_blocks_alerts_whose_range_includes_it():
    near, far = _spell(D(2), aid="near"), _spell(D(6), aid="far", stage="far")
    ev = _decide({6: None}, existing=[near, far])
    assert ev.resolve_ids == ("near",) and ev.keep_ids == ("far",)


def test_far_at_risk_with_other_far_nulls_stays_far():
    c = _one(_decide({5: None, 6: 2.0, 7: None}))
    assert c.stage == "far" and c.next_night.day == D(6)
    assert _decide({5: None, 6: 2.0, 7: None}).reason is None


def test_complete_evaluation_with_no_risk_has_no_chain_and_no_reason():
    ev = _decide()
    assert not ev.no_data and ev.reason is None and ev.chains == ()


def test_duplicate_nights_colder_wins_and_null_never_overrides():
    cold, warm, nul = (Night(D(1), v, "forecast") for v in (1.0, WARM, None))
    rest = [n for n in _nights() if n.day != D(1)]
    for order in ([cold, warm], [warm, cold], [cold, nul], [nul, cold]):
        ev = fs.decide_chains(rest + order, [], CFG, TODAY, False, NOON)
        assert not ev.no_data and _one(ev).stage == "near"


# ── cadenas ───────────────────────────────────────────────────────────


def test_build_chains_by_gap():
    ns = [Night(D(d), 1.0, "forecast") for d in (1, 3, 6, 7)]
    assert [[n.day for n in c] for c in fs.build_chains(ns, 2)] == [[D(1), D(3)], [D(6), D(7)]]


def test_gap_of_two_days_keeps_one_chain_three_opens_another():
    assert len(_decide({1: 1.0, 3: 1.0}).chains) == 1
    ev = _decide({1: 1.0, 4: 1.0})
    assert [c.start for c in ev.chains] == [D(1), D(4)]
    assert all(c.alert_id is None and c.stage == "near" for c in ev.chains)


def test_each_chain_has_its_own_stage_and_text():
    ev = _decide({2: 1.0, 6: -8.0})
    assert [(c.stage, c.text_night.day) for c in ev.chains] == [("near", D(2)), ("far", D(6))]


# ── emparejado con las Alert existentes ───────────────────────────────


def test_chain_within_gap_of_an_alert_keeps_its_id_and_start():
    a = _spell(D(3))
    c = _one(_decide({1: 1.0}, existing=[a]))  # aparece una noche 2 días antes
    assert c.alert_id == a.alert_id and c.start == D(3) and c.last_night == D(3)
    c = _one(_decide({5: 1.0, 3: 1.0}, existing=[a]))
    assert c.alert_id == a.alert_id and c.last_night == D(5)


def test_chain_beyond_gap_is_a_new_alert_and_the_old_one_resolves():
    a = _spell(D(1))
    ev = _decide({4: 1.0}, existing=[a])
    assert _one(ev).alert_id is None and _one(ev).start == D(4)
    assert ev.resolve_ids == (a.alert_id,)


def test_earlier_separated_night_is_a_new_alert_and_the_later_one_stays():
    late = _spell(D(4))
    ev = _decide({1: 1.0, 4: 1.0}, existing=[late])
    assert [(c.alert_id, c.start) for c in ev.chains] == [(None, D(1)), (late.alert_id, D(4))]
    assert not ev.resolve_ids


def test_two_chains_matching_one_alert_the_earliest_takes_it():
    a = _spell(D(2), last=D(5))
    ev = _decide({1: 1.0, 7: 1.0}, existing=[a])  # 1 casa por la izquierda, 7 por la derecha
    assert [c.alert_id for c in ev.chains] == [a.alert_id, None]


def test_one_chain_matching_two_alerts_the_earliest_start_takes_it_others_resolve():
    x, y = _spell(D(1), aid="x"), _spell(D(5), aid="y")
    ev = _decide({1: 1.0, 3: 1.0, 5: 1.0}, existing=[y, x])
    assert _one(ev).alert_id == "x" and _one(ev).last_night == D(5)
    assert ev.resolve_ids == ("y",)


def test_resolved_alert_matches_only_while_its_last_night_plus_gap_is_ahead():
    recent = _spell(D(-2), status="resolved")
    assert _one(_decide({0: 1.0}, existing=[recent], hour=3, running=1.0)).alert_id == recent.alert_id
    old = _spell(D(-3), status="resolved")
    assert _one(_decide({0: 1.0}, existing=[old], hour=3, running=1.0)).alert_id is None


def test_resolved_alert_is_never_resolved_again():
    assert _decide(existing=[_spell(D(2), status="resolved")]).resolve_ids == ()


def test_last_night_never_goes_before_start():
    a = _spell(D(3))
    c = _one(_decide({1: 1.0}, existing=[a]))
    assert c.start == D(3) and c.last_night == D(3)


def test_expired_alert_does_not_match():
    assert _one(_decide({2: 1.0}, existing=[_spell(D(2), status="expired")])).alert_id is None


# ── descartes ─────────────────────────────────────────────────────────


def test_dismissed_alert_stays_skipped_until_the_stage_rises():
    d = _spell(D(2), status="dismissed", stage="near")
    c = _one(_decide({2: 1.0}, existing=[d]))
    assert c.alert_id == d.alert_id and c.skip
    c = _one(_decide({2: -6.0}, existing=[d]))
    assert not c.skip and c.stage == "severe"


def test_dismissed_far_reopens_when_near():
    d = _spell(D(4), status="dismissed", stage="far")
    assert not _one(_decide({4: 1.0}, existing=[d])).skip


def test_dismissed_alert_does_not_silence_a_separate_chain():
    d = _spell(D(1), status="dismissed")
    ev = _decide({1: 1.0, 4: 1.0}, existing=[d])
    assert [c.skip for c in ev.chains] == [True, False]
    assert ev.chains[1].alert_id is None and ev.chains[1].urgency == "vibrate"


def test_dismissed_skips_only_while_every_trigger_night_is_inside_its_range():
    d = _spell(D(1), last=D(2), status="dismissed")
    assert _one(_decide({1: 1.0, 2: 1.0}, existing=[d])).skip
    c = _one(_decide({1: 1.0, 2: 1.0, 4: 1.0}, existing=[d]))  # 4 une la cadena, fuera del rango
    assert not c.skip and c.alert_id == d.alert_id and c.status == "dismissed"
    assert c.text_night.day == D(4) and c.urgency == "vibrate"


def test_dismissed_reopened_by_outside_nights_waits_for_them_in_quiet_hours():
    # La noche descartada (lead 1) sería inminente por la tarde; la de fuera (lead 3), no.
    d = _spell(D(1), status="dismissed")
    c = _one(_decide({1: 1.0, 3: 1.0}, existing=[d], hour=23))
    assert not c.skip and c.urgency == "silent" and c.text_night.day == D(3)
    assert _one(_decide({1: 1.0, 3: 1.0}, existing=[d], hour=9)).urgency == "vibrate"


def test_dismissed_reopened_by_a_stage_rise_keeps_the_normal_text():
    d = _spell(D(1), status="dismissed", stage="near")
    c = _one(_decide({1: -6.0}, existing=[d]))
    assert not c.skip and c.text_night.day == D(1) and c.severity == "critical"


def test_unmatched_dismissed_alert_is_left_alone():
    assert _decide(existing=[_spell(D(2), status="dismissed")]).resolve_ids == ()


# ── severidad máxima de la Alert ──────────────────────────────────


def test_published_severity_never_drops_below_the_alert_peak():
    c = _one(_decide({2: 1.0}, existing=[_spell(D(2), peak="critical")]))
    assert c.stage == "near" and c.severity == "critical" and c.text_night.day == D(2)
    c = _one(_decide({6: 1.0}, existing=[_spell(D(6), stage="far", peak="high")]))
    assert c.stage == "far" and c.severity == "high" and c.urgency == "silent"


def test_peak_never_lowers_a_higher_current_severity():
    assert _one(_decide({2: -6.0}, existing=[_spell(D(2), peak="high")])).severity == "critical"


def test_new_alert_or_no_stored_peak_uses_the_current_severity():
    assert _one(_decide({2: 1.0})).severity == "high"
    assert _one(_decide({2: 1.0}, existing=[_spell(D(2))])).severity == "high"
    assert _one(_decide({6: 1.0}, existing=[_spell(D(1), peak="critical")])).severity == "low"


# ── histéresis ────────────────────────────────────────────────────────


def test_night_inside_an_alert_is_released_only_above_threshold_plus_margin():
    a = _spell(D(2))
    assert fs.RELEASE_MARGIN_C == 0.5
    assert _one(_decide({2: 3.9}, existing=[a])).alert_id == a.alert_id
    assert _one(_decide({2: 4.0}, existing=[a])).stage == "near"
    ev = _decide({2: 4.1}, existing=[a])
    assert ev.chains == () and ev.resolve_ids == (a.alert_id,)


def test_new_nights_use_the_plain_threshold():
    a = _spell(D(2))
    ev = _decide({2: 1.0, 3: 3.9}, existing=[a])
    assert [n.day for n in _one(ev).nights] == [D(2)]
    assert _decide({2: 3.9}).chains == ()


def test_resolved_alert_does_not_hold_its_nights():
    assert _decide({2: 3.9}, existing=[_spell(D(2), status="resolved")]).chains == ()


# ── noche en curso (lead 0) hasta las 10:00 locales ───────────────────


def test_running_night_triggers_before_ten_with_first_row_threshold():
    c = _one(_decide(running=1.0, hour=3))
    assert c.stage == "near" and c.severity == "high" and c.urgency == "vibrate"
    assert c.start == TODAY and c.text_night.day == TODAY and c.text_lead == 0
    assert _one(_decide(running=3.5, hour=3)).stage == "near"
    assert _decide(running=3.6, hour=3).chains == ()


def test_running_severe_before_ten_is_severe():
    assert _one(_decide(running=-6.0, hour=3)).severity == "critical"


def test_ten_o_clock_is_the_boundary():
    a = _spell(TODAY)
    assert _one(_decide(running=1.0, hour=9, existing=[a])).alert_id == a.alert_id
    ev = _decide(running=1.0, hour=10, existing=[a])
    assert ev.chains == () and ev.resolve_ids == (a.alert_id,) and not ev.no_data


def test_running_night_after_ten_still_links_a_chain_but_is_not_listed():
    a = _spell(D(-2), last=D(-1))
    c = _one(_decide({2: 1.0}, existing=[a], running=1.0))
    assert c.alert_id == a.alert_id and c.last_night == D(2)
    assert [n.day for n in c.nights] == [D(2)] and c.next_night.day == D(2)


def test_running_null_before_ten_is_no_data_after_ten_is_not():
    assert _decide(running=None, hour=3).no_data
    assert not _decide(running=None, hour=12).no_data
    missing = [n for n in _nights() if n.day != TODAY]
    assert fs.decide_chains(missing, [], CFG, TODAY, False, 3).no_data
    assert not fs.decide_chains(missing, [], CFG, TODAY, False, 12).no_data


# ── alertas sin fecha ─────────────────────────────────────────────────


def test_legacy_alert_resolves_only_with_complete_data():
    legacy = ExistingSpell("legacy", "active", None, None, "none")
    assert _decide({1: 1.0}, existing=[legacy]).resolve_ids == ("legacy",)
    assert _decide(existing=[legacy]).resolve_ids == ("legacy",)
    assert _decide({7: None}, existing=[legacy]).resolve_ids == ()


# ── texto y urgencia ──────────────────────────────────────────────────


def test_text_night_is_imminent_then_first_severe_then_next():
    c = _one(_decide({1: 2.0, 3: -6.0}))
    assert c.next_night.day == D(1) and c.text_night.day == D(3) and c.text_lead == 3
    c = _one(_decide({1: 2.0, 3: -6.0}, hour=19))  # lead 1 por la tarde es inminente
    assert c.text_night.day == D(1)
    c = _one(_decide({1: 2.0, 3: -1.0}))
    assert c.text_night.day == D(1) and c.text_lead == 1


def test_quiet_constants_are_named():
    assert fs.QUIET_START == 21 and fs.QUIET_END == 7


def test_quiet_hours_hold_a_night_that_is_not_imminent():
    for hour in (21, 22, 23, 0, 3, 6):
        assert _one(_decide({2: 1.0}, hour=hour)).urgency == "silent", hour
    for hour in (7, 12, 18, 20):
        assert _one(_decide({2: 1.0}, hour=hour)).urgency == "vibrate", hour


def test_quiet_hours_lead_one_only_imminent_from_evening():
    for hour in (21, 22, 23):
        assert _one(_decide({1: 1.0}, hour=hour)).urgency == "vibrate", hour
    assert _one(_decide({1: 1.0}, hour=7)).urgency == "vibrate"
    for hour in (0, 3, 6):
        assert _one(_decide({1: 1.0}, hour=hour)).urgency == "silent", hour


def test_quiet_hours_any_imminent_night_of_the_chain_vibrates():
    c = _one(_decide({1: 1.0, 3: 1.0}, hour=23))
    assert c.urgency == "vibrate" and c.text_night.day == D(1)
    assert _one(_decide(running=1.0, hour=3)).urgency == "vibrate"


def test_close_flag_follows_lead_one():
    assert _one(_decide({1: 1.0})).close
    assert not _one(_decide({2: 1.0})).close
