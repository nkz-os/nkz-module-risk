"""Lógica pura del aviso de helada: noches, escalones y racha."""
from datetime import date, timedelta

from app.worker import frost_spell as fs
from app.worker.frost_spell import FrostConfig, Night, SpellState

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


def _nights(overrides=None, upto=7, running=WARM):
    """Noches lead 0..upto templadas, con tmin sobreescrita por lead."""
    overrides = overrides or {}
    out = [Night(TODAY, running, "running")]
    for lead in range(1, upto + 1):
        out.append(Night(TODAY + timedelta(days=lead), overrides.get(lead, WARM), "forecast"))
    return out


def _decide(overrides=None, prev=None, muted=False, today=TODAY, **kw):
    return fs.decide_frost(_nights(overrides, **kw), prev, CFG, today, muted, NOON)


def test_cold_night_at_lead_1_is_near_and_vibrates_once():
    d = _decide({1: 1.0})
    assert d.stage == "near" and d.severity == "high"
    assert d.vibrate and d.new_spell and not d.no_data
    assert d.next_night.day == TODAY + timedelta(days=1)
    assert d.spell.vibrated and not d.spell.severe_vibrated and d.spell.stage == "near"


def test_second_evaluation_same_spell_does_not_vibrate():
    first = _decide({1: 1.0})
    second = _decide({1: 1.0}, prev=first.spell)
    assert second.stage == "near" and not second.vibrate and not second.new_spell
    assert second.spell.start == first.spell.start


def test_future_spell_does_not_revibrate_hourly():
    prev = None
    results = []
    for _ in range(4):
        d = _decide({3: 1.0}, prev=prev)
        results.append(d.vibrate)
        prev = d.spell
    assert results == [True, False, False, False]


def test_far_night_is_silent_low():
    d = _decide({6: 3.0})
    assert d.stage == "far" and d.severity == "low" and not d.vibrate
    assert d.spell.vibrated is False


def test_far_at_or_below_zero_is_medium():
    d = _decide({6: -1.0})
    assert d.stage == "far" and d.severity == "medium" and not d.vibrate


def test_far_becoming_near_vibrates():
    far = _decide({5: 1.0})
    assert far.stage == "far" and not far.vibrate
    near = _decide({4: 1.0}, prev=far.spell, today=TODAY)
    assert near.stage == "near" and near.vibrate and not near.new_spell


def test_gap_of_three_nights_opens_new_spell_and_vibrates():
    last = TODAY - timedelta(days=1)  # ya pasó
    prev = SpellState(last, last, True, True, "severe")
    d = _decide({2: 1.0}, prev=prev)  # primera en riesgo = last + 3
    assert d.new_spell and d.vibrate
    assert d.spell.start == TODAY + timedelta(days=2)
    assert not d.spell.severe_vibrated


def test_gap_of_two_nights_keeps_spell():
    last = TODAY - timedelta(days=1)
    prev = SpellState(last, last, True, True, "severe")
    d = _decide({1: 1.0}, prev=prev)  # last + 2
    assert not d.new_spell and not d.vibrate
    assert d.spell.start == last and d.spell.last_night == TODAY + timedelta(days=1)


def test_withdrawn_forecast_keeps_state_and_no_risk_is_none():
    prev = SpellState(TODAY, TODAY, True, False, "near")
    d = _decide(prev=prev)
    assert d.stage == "none" and d.severity is None and not d.vibrate
    assert d.spell == prev and not d.new_spell and not d.no_data
    assert d.at_risk == () and d.next_night is None and d.coldest is None


def test_severe_within_spell_vibrates_once_more():
    near = _decide({2: 1.0})
    assert near.vibrate
    severe = _decide({2: -6.0}, prev=near.spell)
    assert severe.stage == "severe" and severe.severity == "critical" and severe.vibrate
    assert severe.spell.severe_vibrated
    again = _decide({2: -6.0}, prev=severe.spell)
    assert not again.vibrate


def test_severe_far_beyond_near_does_not_vibrate():
    d = _decide({6: -8.0})
    assert d.stage == "far" and d.severity == "medium" and not d.vibrate
    assert d.spell.severe_vibrated is False


def test_muted_never_vibrates_but_stage_and_severity_stay():
    d = _decide({1: -6.0}, muted=True)
    assert d.stage == "severe" and d.severity == "critical" and not d.vibrate
    assert d.spell.vibrated and d.spell.severe_vibrated
    unmuted = _decide({1: -6.0}, prev=d.spell, muted=False)
    assert not unmuted.vibrate


def test_todays_running_night_never_triggers():
    d = fs.decide_frost(_nights(running=-8.0), None, CFG, TODAY, False, NOON)
    assert d.stage == "none" and not d.vibrate and not d.no_data and d.spell is None


def test_null_tmin_in_near_window_is_no_data():
    prev = SpellState(TODAY, TODAY, True, False, "near")
    d = _decide({2: None}, prev=prev)
    assert d.no_data and d.stage == "none" and not d.vibrate and d.spell == prev


def test_partial_forecast_never_reads_as_no_risk_with_cold_night():
    d = _decide({1: -3.0, 3: None})
    assert d.no_data and not d.vibrate


def test_missing_near_night_is_no_data():
    nights = [n for n in _nights({1: 1.0}) if (n.day - TODAY).days != 3]
    d = fs.decide_frost(nights, None, CFG, TODAY, False, NOON)
    assert d.no_data and not d.vibrate
    assert fs.decide_frost([], None, CFG, TODAY, False, NOON).no_data


def test_null_far_night_with_nothing_at_risk_is_partial_no_data():
    prev = SpellState(TODAY + timedelta(days=6), TODAY + timedelta(days=6), False, False, "far")
    d = _decide({5: None, 6: None, 7: None}, prev=prev)
    assert d.no_data and d.no_data_reason == "partial" and d.stage == "none"
    assert d.spell == prev and not d.new_spell


def test_missing_far_night_with_nothing_at_risk_is_partial_no_data():
    nights = [n for n in _nights() if (n.day - TODAY).days != 7]
    d = fs.decide_frost(nights, None, CFG, TODAY, False, NOON)
    assert d.no_data and d.no_data_reason == "partial"


def test_near_window_gap_keeps_its_own_reason():
    assert _decide({2: None}).no_data_reason == "incomplete_nights"


def test_far_at_risk_with_other_far_nulls_stays_far():
    d = _decide({5: None, 6: 2.0, 7: None})
    assert not d.no_data and d.stage == "far" and d.next_night.day == TODAY + timedelta(days=6)


def test_near_at_risk_with_far_nulls_stays_near():
    d = _decide({1: 1.0, 6: None})
    assert not d.no_data and d.stage == "near"


def test_complete_evaluation_with_no_risk_is_none():
    d = _decide()
    assert not d.no_data and d.stage == "none" and d.no_data_reason is None


def test_threshold_follows_lead():
    # 3 C en riesgo a lead 1; la misma mínima a lead 6 (umbral 4,5) también, a lead 8 no hay fila
    assert _decide({1: 3.0}).stage == "near"
    assert _decide({6: 4.4}).stage == "far"
    assert _decide({6: 4.6}).stage == "none"
    assert _decide({5: 3.9}).stage == "far"
    assert _decide({5: 4.1}).stage == "none"
    d = fs.decide_frost(_nights({8: 3.0}, upto=8), None, CFG, TODAY, False, NOON)
    assert d.stage == "none"


def test_reopen_only_when_stage_rises():
    assert fs.should_reopen_dismissed("far", "near")
    assert fs.should_reopen_dismissed("near", "severe")
    assert fs.should_reopen_dismissed("far", "severe")
    assert not fs.should_reopen_dismissed("near", "near")
    assert not fs.should_reopen_dismissed("near", "far")
    assert not fs.should_reopen_dismissed("severe", "near")


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
    cfg = fs.config_from_model_config(
        {
            "thresholds_by_lead": [
                {"max_lead": 4, "tmin_c": 3.5},
                {"max_lead": 5, "tmin_c": 4.0},
                {"max_lead": 7, "tmin_c": 4.5},
            ],
            "near_nights": 4,
            "horizon_nights": 7,
        }
    )
    assert cfg == FrostConfig(((4, 3.5), (5, 4.0), (7, 4.5)), near_nights=4, horizon_nights=7)
    # De punta a punta con esa configuración.
    d = fs.decide_frost(_nights({2: 1.0}), None, cfg, TODAY, False, NOON)
    assert d.stage == "near" and d.vibrate and d.severity == "high"


def test_config_accepts_pairs():
    cfg = fs.config_from_model_config(
        {"thresholds_by_lead": [[7, 4.5], [4, 3.5], [5, 4.0]], "near_nights": 4, "horizon_nights": 7}
    )
    assert cfg == CFG


def test_flapping_near_night_does_not_vibrate_twice():
    prev, out = None, []
    for ov in ({1: 1.0, 6: 3.0}, {6: 3.0}, {1: 1.0, 6: 3.0}):
        d = _decide(ov, prev=prev)
        out.append(d.vibrate)
        prev = d.spell
    assert out == [True, False, False]


def test_new_spell_vibrates_once_previous_last_night_has_passed():
    first = _decide({1: 1.0})
    assert first.vibrate
    later = TODAY + timedelta(days=2)  # la noche de la racha ya pasó
    nights = [Night(later + timedelta(days=i), WARM if i != 2 else 1.0, "forecast") for i in range(0, 8)]
    d = fs.decide_frost(nights, first.spell, CFG, later, False, NOON)
    assert d.new_spell and d.vibrate and d.stage == "near"
    again = fs.decide_frost(nights, d.spell, CFG, later, False, NOON)
    assert not again.vibrate
    # Helada retirada y otra posterior más cercana después: sigue avisando.
    withdrawn = fs.decide_frost(_nights(), first.spell, CFG, later, False, NOON)
    assert withdrawn.stage == "none" and withdrawn.spell == first.spell
    much_later = TODAY + timedelta(days=9)
    nights2 = [Night(much_later + timedelta(days=i), 1.0 if i == 1 else WARM, "forecast") for i in range(0, 8)]
    d2 = fs.decide_frost(nights2, withdrawn.spell, CFG, much_later, False, NOON)
    assert d2.new_spell and d2.vibrate


def test_running_night_counts_for_chain_but_does_not_trigger():
    # Racha previa terminó en D-2; D-1 limpia; D (en curso) fría; D+1 fría: misma racha.
    prev = SpellState(TODAY - timedelta(days=4), TODAY - timedelta(days=2), True, False, "near")
    d = fs.decide_frost(_nights({1: 1.0}, running=1.0), prev, CFG, TODAY, False, NOON)
    assert not d.new_spell and not d.vibrate and d.stage == "near"
    # Solo la noche en curso fría: ni escalón ni vibración.
    only = fs.decide_frost(_nights(running=-8.0), prev, CFG, TODAY, False, NOON)
    assert only.stage == "none" and not only.vibrate and only.spell == prev


def test_duplicate_nights_colder_wins_in_any_order():
    cold = Night(TODAY + timedelta(days=1), 1.0, "forecast")
    warm = Night(TODAY + timedelta(days=1), WARM, "forecast")
    rest = [n for n in _nights() if (n.day - TODAY).days != 1]
    for order in ([cold, warm], [warm, cold]):
        d = fs.decide_frost(rest + order, None, CFG, TODAY, False, NOON)
        assert d.stage == "near" and d.vibrate


def test_duplicate_null_never_overrides_value():
    val = Night(TODAY + timedelta(days=1), 1.0, "forecast")
    nul = Night(TODAY + timedelta(days=1), None, "forecast")
    rest = [n for n in _nights() if (n.day - TODAY).days != 1]
    for order in ([val, nul], [nul, val]):
        d = fs.decide_frost(rest + order, None, CFG, TODAY, False, NOON)
        assert not d.no_data and d.stage == "near"


def test_nan_tmin_in_near_window_is_no_data():
    assert _decide({2: float("nan")}).no_data


def _on(day_offset, overrides, prev, today_offset=0):
    """Evalúa en TODAY+today_offset con tmin absolutas por offset de día desde TODAY."""
    today = TODAY + timedelta(days=today_offset)
    ns = [
        Night(today + timedelta(days=i), overrides.get(today_offset + i, WARM), "forecast")
        for i in range(0, 8)
    ]
    return fs.decide_frost(ns, prev, CFG, today, False, NOON)


def test_frost_beyond_gap_vibrates_when_near_even_if_prev_is_current():
    for gap in (3, 4, 5, 6):
        for prev_off in (0, 1, 2):
            prev = SpellState(TODAY, TODAY + timedelta(days=prev_off), True, False, "near")
            frost = prev_off + gap
            vibes, cur = [], prev
            for k in range(0, frost):
                d = _on(0, {frost: 1.0}, cur, today_offset=k)
                if d.no_data:
                    continue
                vibes.append(d.vibrate)
                cur = d.spell
                if (frost - k) <= CFG.near_nights:
                    assert cur.vibrated and cur.last_night >= TODAY + timedelta(days=frost)
            assert vibes.count(True) == 1, (gap, prev_off, vibes)


def test_far_chain_does_not_replace_current_spell_until_near():
    prev = SpellState(TODAY, TODAY + timedelta(days=1), True, False, "near")
    d = _on(0, {6: 1.0}, prev)
    assert d.stage == "far" and not d.vibrate and not d.new_spell
    assert d.spell.start == prev.start and d.spell.last_night == prev.last_night
    assert d.next_night.day == TODAY + timedelta(days=6)
    near = _on(0, {6: 1.0}, d.spell, today_offset=2)  # lead 4
    assert near.new_spell and near.vibrate and near.spell.start == TODAY + timedelta(days=6)


def test_returned_earlier_night_does_not_open_new_spell():
    prev = SpellState(TODAY + timedelta(days=3), TODAY + timedelta(days=3), True, False, "near")
    d = _on(0, {1: 1.0, 3: 1.0}, prev)
    assert not d.new_spell and not d.vibrate
    assert d.spell.start == prev.start


def test_last_night_does_not_cross_a_break():
    prev = SpellState(TODAY, TODAY + timedelta(days=1), True, False, "near")
    d = _on(0, {3: 1.0, 7: 1.0}, prev)
    assert d.spell.last_night == TODAY + timedelta(days=3)
    assert not d.new_spell and d.stage == "near"


# ── noche en curso (lead 0) hasta las 10:00 locales ───────────────────


def _dawn(running, prev=None, hour=3, overrides=None, muted=False):
    return fs.decide_frost(_nights(overrides, running=running), prev, CFG, TODAY, muted, hour)


def test_running_night_triggers_before_ten():
    d = _dawn(1.0)
    assert d.stage == "near" and d.severity == "high" and d.vibrate and not d.no_data
    assert d.next_night.day == TODAY and d.spell.start == TODAY
    assert d.text_night.day == TODAY and d.text_lead == 0


def test_running_night_uses_first_row_threshold():
    assert _dawn(3.5).stage == "near"
    assert _dawn(3.6).stage == "none"


def test_running_severe_before_ten_is_severe():
    d = _dawn(-6.0)
    assert d.stage == "severe" and d.severity == "critical" and d.vibrate
    assert d.spell.severe_vibrated


def test_ten_o_clock_is_the_boundary():
    assert _dawn(1.0, hour=9).stage == "near"
    after = _dawn(1.0, hour=10)
    assert after.stage == "none" and not after.no_data


def test_running_null_before_ten_is_no_data_after_ten_is_not():
    assert _dawn(None, hour=3).no_data
    assert _dawn(None, hour=3).no_data_reason == "incomplete_nights"
    assert not _dawn(None, hour=12).no_data
    missing = [n for n in _nights() if n.day != TODAY]
    assert fs.decide_frost(missing, None, CFG, TODAY, False, 3).no_data
    assert not fs.decide_frost(missing, None, CFG, TODAY, False, 12).no_data


def test_running_night_keeps_the_spell_of_the_night_before():
    # Misma noche vista antes de medianoche (lead 1) y después (lead 0).
    prev = fs.decide_frost(
        _nights({1: 1.0}), None, CFG, TODAY, False, 23).spell
    tomorrow = TODAY + timedelta(days=1)
    nights = [Night(tomorrow, 1.0, "running")] + [
        Night(tomorrow + timedelta(days=i), WARM, "forecast") for i in range(1, 8)]
    d = fs.decide_frost(nights, prev, CFG, tomorrow, False, 0)
    assert not d.new_spell and d.spell.start == prev.start and d.stage == "near"


def test_running_night_after_ten_still_chains_once():
    prev = SpellState(TODAY - timedelta(days=4), TODAY - timedelta(days=2), True, False, "near")
    d = fs.decide_frost(_nights({2: 1.0}, running=1.0), prev, CFG, TODAY, False, 12)
    assert not d.new_spell and d.spell.last_night == TODAY + timedelta(days=2)


def test_text_night_is_the_next_one_or_the_first_severe():
    d = _decide({1: 2.0, 3: -6.0})
    assert d.next_night.day == TODAY + timedelta(days=1)
    assert d.text_night.day == TODAY + timedelta(days=3) and d.text_lead == 3
    d = _decide({1: 2.0, 3: -1.0})
    assert d.text_night.day == TODAY + timedelta(days=1) and d.text_lead == 1


# ── urgencia ──────────────────────────────────────────────────────────


def test_urgency_repeats_vibrate_while_near_even_after_the_spell_flag():
    first = _decide({2: 1.0})
    again = _decide({2: 1.0}, prev=first.spell)
    assert not again.vibrate and again.spell.vibrated  # contabilidad de la racha
    assert fs.frost_urgency(first, False, 12) == "vibrate"
    assert fs.frost_urgency(again, False, 12) == "vibrate"


def test_urgency_silent_when_far_none_or_muted():
    assert fs.frost_urgency(_decide({6: 1.0}), False, 12) == "silent"
    assert fs.frost_urgency(_decide(), False, 12) == "silent"
    assert fs.frost_urgency(_decide({2: 1.0}), True, 12) == "silent"
    assert fs.frost_urgency(_decide({2: -6.0}), True, 12) == "silent"


def test_quiet_constants_are_named():
    assert fs.QUIET_START == 22 and fs.QUIET_END == 8


def test_quiet_hours_hold_a_night_that_is_not_imminent():
    d = _decide({2: 1.0})
    for hour in (22, 23, 0, 3, 7):
        assert fs.frost_urgency(d, False, hour) == "silent", hour
    for hour in (8, 12, 18, 21):
        assert fs.frost_urgency(d, False, hour) == "vibrate", hour


def test_quiet_hours_lead_one_only_imminent_from_evening():
    d = _decide({1: 1.0})
    assert fs.frost_urgency(d, False, 22) == "vibrate"
    assert fs.frost_urgency(d, False, 23) == "vibrate"
    for hour in (0, 3, 7):
        assert fs.frost_urgency(d, False, hour) == "silent", hour
    assert fs.frost_urgency(d, False, 8) == "vibrate"


def test_quiet_hours_lead_zero_is_imminent():
    d = _dawn(1.0, hour=3)
    assert fs.frost_urgency(d, False, 3) == "vibrate"
    assert fs.frost_urgency(_dawn(1.0, hour=0), False, 0) == "vibrate"
    assert fs.frost_urgency(_dawn(1.0, hour=9), False, 9) == "vibrate"


def test_no_data_decision_is_silent():
    assert fs.frost_urgency(_decide({2: None}), False, 12) == "silent"
