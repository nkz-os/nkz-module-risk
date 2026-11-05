"""Aviso de helada de punta a punta, hora a hora, contra un oráculo independiente.

Corre lo real (`process_frost_parcel`, `load_existing`, `publish_alert._build`,
`sweep_expired`) sobre un broker falso en forma normalizada con ida y vuelta JSON, y
simula notifications: solo `urgency=vibrate` es inmediato, deduplica por (id, severidad),
anota solo lo enviado y libera al cerrarse el aviso.

El oráculo NO lee el estado de la racha ni la decisión. Saca las noches en riesgo de sus
propios umbrales y de lo que el worker publicó de verdad. Solo usa el id de la Alert,
que es lo que ve notifications.
"""
import functools
import json
import logging
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Set
from unittest.mock import MagicMock, patch

import pytest

from app.alerts import contract
from app.worker import alert_lifecycle as lc
from app.worker import alert_publisher as ap
from app.worker import frost_alerts as fa
from app.worker import sources
from app.worker.frost_spell import Night

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
T = "acme"
PARCEL = "urn:ngsi-ld:AgriParcel:p1"
SETTINGS = MagicMock(weather_api_url="http://w", orion_ld_url="http://o", context_url="http://c")
START = datetime(2026, 12, 1, 0, 15, tzinfo=timezone.utc)  # hora local = UTC en la simulación
DAY0 = START.date()
GAP = 2
NEAR = 4


# ── reglas del oráculo, escritas aparte del código ───────────────────


def oracle_threshold(lead: int) -> float:
    if lead <= 4:
        return 3.5
    return 4.0 if lead == 5 else 4.5


def oracle_trigger_leads(hour: int) -> range:
    return range(0 if hour < 10 else 1, 8)


def oracle_at_risk(values: Dict[int, Optional[float]], hour: int) -> Dict[int, float]:
    """{lead: tmin} de las noches en riesgo según el oráculo."""
    return {
        lead: values[lead] for lead in oracle_trigger_leads(hour)
        if values.get(lead) is not None and values[lead] <= oracle_threshold(lead)
    }


def oracle_complete(values: Dict[int, Optional[float]], hour: int) -> bool:
    first = 0 if hour < 10 else 1
    near_ok = all(values.get(lead) is not None for lead in range(first, NEAR + 1))
    if not near_ok:
        return False
    if oracle_at_risk(values, hour):
        return True
    return all(values.get(lead) is not None for lead in oracle_trigger_leads(hour))


def imminent_window(lead: int, hour: int) -> bool:
    return (lead == 1 and hour >= 18) or (lead == 0 and hour < 10)


# ── broker y notifications falsos ────────────────────────────────────


@dataclass
class Delivery:
    at: datetime
    alert_id: str
    severity: str
    body: str


class FakeNotifications:
    """Lo que hace notifications con cada Alert que Orion le notifica."""

    def __init__(self, fail: Callable[[], bool]):
        self.fail = fail
        self.sent: Set[tuple] = set()
        self.deliveries: List[Delivery] = []
        self.failed_attempts: List[tuple] = []
        self.closes = 0

    def on_upsert(self, entity: dict, at: datetime) -> None:
        if entity["urgency"]["value"] != "vibrate":
            return  # silent: va a la app y al resumen
        key = (entity["id"], entity["severity"]["value"])
        if key in self.sent:
            return
        if self.fail():
            self.failed_attempts.append((at, key))
            return
        self.sent.add(key)
        self.deliveries.append(
            Delivery(at, key[0], key[1], entity["plain"]["value"]["es"]["body"]))

    def on_close(self, alert_id: str) -> None:
        self.sent = {k for k in self.sent if k[0] != alert_id}
        self.closes += 1


class FakeBroker:
    def __init__(self, notifications: FakeNotifications):
        self.e: Dict[str, dict] = {}
        self.notifications = notifications
        self.clock = START
        self.upserts = 0

    # Lectura y cierre (lo que usa el worker con su cliente).
    def query_entities(self, type=None, limit=100, offset=0, q=None):
        return [json.loads(json.dumps(v)) for v in self.e.values()][offset:offset + limit]

    def append_entity_attrs(self, eid, attrs):
        self.e[eid].update(json.loads(json.dumps(attrs)))
        if attrs.get("status", {}).get("value") != "active":
            self.notifications.on_close(eid)

    def delete_entity(self, eid):
        self.e.pop(eid, None)

    # Cliente de publish_alert.
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_entity(self, eid, options=None):
        return {"name": "Soto"}

    def upsert_entities_batch(self, entities):
        for raw in entities:
            ent = dict(raw)
            ent.pop("@context", None)
            # Orion devuelve observedAt como Property con cadena.
            ent["observedAt"] = {"type": "Property", "value": raw["observedAt"]}
            cur = self.e.setdefault(ent["id"], {"id": ent["id"], "type": "Alert"})
            cur.update(json.loads(json.dumps(ent)))
            self.upserts += 1
            self.notifications.on_upsert(cur, self.clock)
        return {"upserted": len(entities), "errors": []}


def conn_with(muted: bool):
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    cur.fetchone.return_value = {"vibrate": False} if muted else None
    return conn


# ── ejecución hora a hora ────────────────────────────────────────────


@dataclass
class Trace:
    broker: FakeBroker
    notifications: FakeNotifications
    outcomes: List[tuple] = field(default_factory=list)  # (hora, resultado)


def _fetched(values: Optional[Dict[int, Optional[float]]], now: datetime):
    if values is None:
        return None
    today = now.date()
    nights = [
        Night(today + timedelta(days=lead), v, "running" if lead == 0 else "forecast")
        for lead, v in sorted(values.items())
    ]
    return sources.FrostNights(nights, today, now.isoformat(), now.hour)


def drive(
    forecast: Callable[[datetime], Optional[Dict[int, Optional[float]]]],
    hours: int,
    fail: Callable[[], bool] = lambda: False,
    muted: bool = False,
    start: datetime = START,
) -> Trace:
    """`forecast(now)` da {lead: tmin} (ausente = noche sin dato), o None si no hay dato."""
    notifications = FakeNotifications(fail)
    broker = FakeBroker(notifications)
    trace = Trace(broker, notifications)
    conn = conn_with(muted)
    clock = {"now": start}
    with patch.object(contract, "_now",
                      lambda: clock["now"].isoformat().replace("+00:00", "Z")), \
            patch.object(ap, "SyncOrionClient", lambda *a, **k: broker), \
            patch.object(fa.sources, "fetch_frost_nights",
                         lambda *a, **k: _fetched(forecast(clock["now"]), clock["now"])):
        logging.disable(logging.CRITICAL)
        try:
            for h in range(hours):
                now = start + timedelta(hours=h)
                clock["now"] = now
                broker.clock = now
                existing = lc.load_existing(broker)
                touched: Set[str] = set()
                outcome = fa.process_frost_parcel(
                    broker, conn, SETTINGS, T, RISK, {"id": PARCEL, "type": "AgriParcel"},
                    existing, touched, now)
                lc.sweep_expired(broker, existing, touched, {"frost": MODEL_CONFIG}, now)
                trace.outcomes.append((now, outcome))
        finally:
            logging.disable(logging.NOTSET)
    return trace


def stable(cold: Dict[date, float], warm: float = 10.0):
    def f(now: datetime):
        return {lead: cold.get(now.date() + timedelta(days=lead), warm) for lead in range(0, 8)}
    return f


def active(broker: FakeBroker) -> List[dict]:
    return [v for v in broker.e.values() if v["status"]["value"] == "active"]


# ── escenarios de la revisión ────────────────────────────────────────


FROST_DAY = DAY0 + timedelta(days=6)


def test_s1_alert_stays_through_the_dawn_and_closes_at_ten():
    t = drive(stable({FROST_DAY: 1.0}), 24 * 7)
    by_hour = dict(t.outcomes)
    midnight = datetime.combine(FROST_DAY, datetime.min.time(), timezone.utc) + timedelta(minutes=15)
    assert by_hour[midnight] != "resolved"
    assert by_hour[midnight + timedelta(hours=7)] != "resolved"
    assert by_hour[midnight + timedelta(hours=10)] == "resolved"
    assert len(t.notifications.deliveries) == 1


def test_s1_stable_frost_does_not_wake_the_farmer_at_midnight():
    t = drive(stable({FROST_DAY: 1.0}), 24 * 7)
    d = t.notifications.deliveries[0]
    assert d.at.hour == 8 and (FROST_DAY - d.at.date()).days == 4


def test_s2_cooling_after_midnight_warns_at_once_in_the_quiet_hours():
    def f(now):
        v = {lead: 10.0 for lead in range(0, 8)}
        if now.date() == FROST_DAY - timedelta(days=1):
            v[1] = 4.0
        if now.date() == FROST_DAY:
            v[0] = -1.0 if now.hour >= 1 else 4.0
        return v
    start = datetime.combine(FROST_DAY - timedelta(days=1), datetime.min.time(), timezone.utc) \
        + timedelta(hours=12, minutes=15)
    t = drive(f, 20, start=start)
    assert len(t.notifications.deliveries) == 1
    d = t.notifications.deliveries[0]
    assert d.at.date() == FROST_DAY and d.at.hour == 1
    assert d.body == "Esta madrugada puede bajar a -1 °C."


def test_s3_far_nulls_do_not_resolve():
    def f(now):
        v = {lead: 10.0 for lead in range(0, 8)}
        v[6] = 2.0
        if now.hour >= 3:
            v.update({5: None, 6: None, 7: None})
        return v
    t = drive(f, 6)
    assert {o for _, o in t.outcomes[3:]} == {"no_data"}
    assert len(active(t.broker)) == 1


def test_s5_outage_with_the_frost_ahead_does_not_expire():
    outage_from = START + timedelta(days=1)
    frost = stable({FROST_DAY: 1.0})

    def f(now):
        return None if now >= outage_from else frost(now)
    t = drive(f, 24 * 5)
    assert len(active(t.broker)) == 1, "no debe caducar con la helada por delante"
    assert not [v for v in t.broker.e.values() if v["status"]["value"] == "expired"]


def test_s5_outage_past_the_night_expires_by_ttl():
    night = DAY0 + timedelta(days=1)
    frost = stable({night: 1.0})

    def f(now):
        return None if now >= START + timedelta(hours=12) else frost(now)
    t = drive(f, 24 * 4)
    assert not active(t.broker)
    assert [v for v in t.broker.e.values() if v["status"]["value"] == "expired"]


def test_lost_delivery_is_retried_and_does_not_vibrate_again_after_success():
    fails = iter([True, True, True])
    t = drive(stable({FROST_DAY: 1.0}), 24 * 7, fail=lambda: next(fails, False))
    assert len(t.notifications.failed_attempts) == 3
    assert len(t.notifications.deliveries) == 1


def test_every_delivery_failing_until_the_last_hours_still_arrives_at_dawn():
    night_start = datetime.combine(FROST_DAY, datetime.min.time(), timezone.utc)
    clock = {"last": None}

    def f(now):
        clock["last"] = now
        return stable({FROST_DAY: 1.0})(now)
    t = drive(f, 24 * 7, fail=lambda: clock["last"] < night_start + timedelta(hours=5))
    assert len(t.notifications.deliveries) == 1
    assert t.notifications.deliveries[0].at == night_start + timedelta(hours=5, minutes=15)


def test_severe_arrival_vibrates_once_more_and_only_once():
    def f(now):
        v = stable({FROST_DAY: 1.0})(now)
        if now >= START + timedelta(days=4, hours=10):
            v = stable({FROST_DAY: -6.0})(now)
        return v
    t = drive(f, 24 * 6)
    assert [d.severity for d in t.notifications.deliveries] == ["high", "critical"]


def test_muted_parcel_never_vibrates_but_the_alert_stays():
    t = drive(stable({FROST_DAY: 1.0}), 24 * 6, muted=True)
    assert not t.notifications.deliveries and not t.notifications.failed_attempts
    assert len(active(t.broker)) == 1


def test_flapping_with_a_far_night_holding_the_stage_gives_one_vibrate():
    def f(now):
        v = {lead: 10.0 for lead in range(0, 8)}
        v[6] = 2.0  # sostiene el escalón: sin resolver no se libera la deduplicación
        if now.hour % 2 == 0:
            v[2] = 1.0
        return v
    t = drive(f, 48)
    assert len(t.notifications.deliveries) == 1


def test_flapping_through_no_data_gives_one_vibrate():
    def f(now):
        v = stable({FROST_DAY: 1.0})(now)
        if now.hour % 3 == 0:
            v[1] = None
        return v
    t = drive(f, 24 * 6)
    assert len(t.notifications.deliveries) == 1


def test_frost_returning_across_a_resolve_vibrates_again_accepted():
    # Regla aceptada: resolver libera la deduplicación y peca de avisar de más.
    def f(now):
        v = {lead: 10.0 for lead in range(0, 8)}
        target = (DAY0 + timedelta(days=4) - now.date()).days
        if 0 < target <= 7 and not (START + timedelta(hours=12) <= now < START + timedelta(hours=14)):
            v[target] = 1.0
        return v
    t = drive(f, 24)
    assert "resolved" in {o for _, o in t.outcomes}
    assert len(t.notifications.deliveries) == 2
    assert len({d.alert_id for d in t.notifications.deliveries}) == 1


def test_stable_active_alert_rarely_writes():
    t = drive(stable({DAY0 + timedelta(days=4): 1.0}), 24)
    # Lead 3-4 durante un día entero: sin cambios se escribe cada 6 h, más los
    # cambios de urgencia a la entrada y salida de las horas de silencio.
    assert t.broker.upserts <= 8


# ── propiedad con oráculo independiente ──────────────────────────────


class Oracle:
    """Comprueba (a), (b) y (c) con lo devuelto por el worker."""

    def __init__(self):
        self.seen: Set[date] = set()            # noches vistas en riesgo, para el encadenado
        self.credit: Dict[str, Set[date]] = {}  # severidad -> noches avisadas desde el último cierre
        self.warned: Set[date] = set()
        self.window_seen: Dict[date, int] = {}
        self.window_attempts: Dict[date, int] = {}
        self.violations: List[tuple] = []
        self.delivered_keys: Set[tuple] = set()

    def _linked_to_warned(self, day: date) -> bool:
        """¿Hay una cadena de noches vistas, con saltos <= GAP, hasta una noche avisada?"""
        pending, visited = [day], {day}
        while pending:
            cur = pending.pop()
            if cur in self.warned:
                return True
            for other in self.seen:
                if other not in visited and abs((other - cur).days) <= GAP:
                    visited.add(other)
                    pending.append(other)
        return False

    def observe(self, now, values, deliveries, failed, closed):
        hour, today = now.hour, now.date()
        if values is None or not oracle_complete(values, hour):
            at_risk = {}
        else:
            at_risk = oracle_at_risk(values, hour)
        days = {today + timedelta(days=lead): lead for lead in at_risk}
        self.seen |= set(days)

        near_days = {d for d, lead in days.items() if lead <= NEAR}
        for d in deliveries:
            key = (d.alert_id, d.severity)
            if key in self.delivered_keys:
                self.violations.append(("double_id", now.isoformat(), d.alert_id, d.severity))
            self.delivered_keys.add(key)
            new = near_days - self.credit.get(d.severity, set())
            if not new:
                # Más estricto que (b): una entrega que no añade ninguna noche a esa
                # severidad no le dice nada nuevo al agricultor, sea cual sea el id.
                self.violations.append(("double", now.isoformat(), d.alert_id, d.severity))
            self.credit.setdefault(d.severity, set()).update(near_days)
            self.warned |= near_days

        for day, lead in days.items():
            if imminent_window(lead, hour):
                self.window_seen[day] = self.window_seen.get(day, 0) + 1
                if deliveries or failed:
                    self.window_attempts[day] = self.window_attempts.get(day, 0) + 1

        # Fin de noche: pasadas las 10:00 del día D ya no hay más oportunidades.
        for day in list(self.window_seen):
            if today > day or (today == day and hour >= 10):
                if not self._linked_to_warned(day) and self.window_attempts.get(day, 0) == 0:
                    # (a) ni aviso entregado ni un solo intento en toda la ventana. Si tampoco
                    # hubo otro push en este episodio, el agricultor no se enteró de nada.
                    kind = "missed" if self.warned else "missed_no_push"
                    self.violations.append((kind, day.isoformat(), now.isoformat()))
                del self.window_seen[day]

        if closed:
            # Sin ningún aviso abierto, lo avisado antes deja de contar. Cerrar solo la
            # racha vieja al abrir otra no cuenta: el agricultor sigue avisado. Una noche ya
            # avisada y retirada queda cumplida; si vuelve, debe avisarse otra vez.
            for day in list(self.window_seen):
                if self._linked_to_warned(day):
                    del self.window_seen[day]
            self.credit.clear()
            self.delivered_keys.clear()
            self.warned.clear()
            self.seen.clear()


def _random_forecast(rng: random.Random, days: int):
    truth = {d: (rng.uniform(-8.0, 3.5) if rng.random() < 0.3 else rng.uniform(5.0, 12.0))
             for d in range(0, days + 9)}
    runs: Dict[tuple, float] = {}
    flappy = {d for d in truth if rng.random() < 0.15}
    outage = set()
    if rng.random() < 0.3:
        a = rng.randrange(0, days * 24)
        outage = set(range(a, a + rng.randint(1, 40)))

    def f(now: datetime):
        elapsed = int((now - START).total_seconds() // 3600)
        if elapsed in outage:
            return None
        base = (now.date() - DAY0).days
        run = elapsed // 6
        values: Dict[int, Optional[float]] = {}
        for lead in range(0, 8):
            d = base + lead
            key = (run, d)
            if key not in runs:
                runs[key] = rng.uniform(-1.5, 1.5)
            v = truth[d] + runs[key]
            if d in flappy:
                v += rng.uniform(-3.0, 3.0)
            if rng.random() < 0.01:
                continue  # noche sin dato
            if rng.random() < 0.01:
                v = None
            values[lead] = v
        return values
    return f


def _simulate(seed: int, days: int = 10, failure_rate: float = 0.3) -> List[tuple]:
    rng = random.Random(seed)
    forecast = _random_forecast(rng, days)
    fail_rng = random.Random(seed + 1)
    oracle = Oracle()
    notifications = FakeNotifications(lambda: fail_rng.random() < failure_rate)
    broker = FakeBroker(notifications)
    conn = conn_with(False)
    clock = {"now": START, "values": None}

    def fetch(*a, **k):
        clock["values"] = forecast(clock["now"])
        return _fetched(clock["values"], clock["now"])

    bad: List[tuple] = []
    with patch.object(contract, "_now",
                      lambda: clock["now"].isoformat().replace("+00:00", "Z")), \
            patch.object(ap, "SyncOrionClient", lambda *a, **k: broker), \
            patch.object(fa.sources, "fetch_frost_nights", fetch):
        logging.disable(logging.CRITICAL)
        try:
            for h in range(days * 24):
                now = START + timedelta(hours=h)
                clock["now"] = now
                broker.clock = now
                existing = lc.load_existing(broker)
                before_status = {k: v["status"]["value"] for k, v in broker.e.items()}
                n_del, n_fail, n_rel = (len(notifications.deliveries),
                                        len(notifications.failed_attempts), notifications.closes)
                touched: Set[str] = set()
                outcome = fa.process_frost_parcel(
                    broker, conn, SETTINGS, T, RISK, {"id": PARCEL, "type": "AgriParcel"},
                    existing, touched, now)
                lc.sweep_expired(broker, existing, touched, {"frost": MODEL_CONFIG}, now)
                values = clock["values"]
                # Coherencia: sin dato completo nunca hay publicación ni resolución.
                complete = values is not None and oracle_complete(values, now.hour)
                if complete != (outcome != "no_data"):
                    bad.append(("no_data_mismatch", now.isoformat(), outcome, values))
                if complete and oracle_at_risk(values, now.hour) and outcome in ("resolved", "none"):
                    bad.append(("resolved_at_risk", now.isoformat(), outcome, values))
                if complete and not oracle_at_risk(values, now.hour) and outcome not in ("resolved", "none"):
                    bad.append(("alert_without_risk", now.isoformat(), outcome, values))
                if outcome == "no_data":
                    closed_now = [
                        k for k, v in broker.e.items()
                        if before_status.get(k) == "active" and v["status"]["value"] == "resolved"]
                    if closed_now:
                        bad.append(("no_data_resolved", now.isoformat(), closed_now))
                oracle.observe(
                    now, values,
                    notifications.deliveries[n_del:],
                    notifications.failed_attempts[n_fail:],
                    notifications.closes != n_rel and not active(broker),
                )
        finally:
            logging.disable(logging.NOTSET)
    return bad + oracle.violations


SEEDS = 130


@functools.lru_cache(maxsize=None)
def _all_violations() -> tuple:
    return tuple((20261105 + seed,) + v for seed in range(SEEDS) for v in _simulate(20261105 + seed))


def _violations_of(*kinds: str) -> List[tuple]:
    return [v for v in _all_violations() if v[1] in kinds]


def test_property_outcome_agrees_with_the_oracle():
    assert not (bad := _violations_of(
        "no_data_mismatch", "resolved_at_risk", "alert_without_risk", "no_data_resolved"
    )), bad[:3]


def test_property_b_at_most_one_delivery_per_spell_and_severity_between_closes():
    assert not (bad := _violations_of("double_id")), bad[:5]


def test_property_a_no_frost_episode_goes_without_any_push():
    assert not (bad := _violations_of("missed_no_push")), bad[:5]


COUNTEREXAMPLE = (
    "Racha y deduplicación: una noche fuera de la cadena comparte (id, severidad) con otra; "
    "pendiente de decisión del owner"
)


@pytest.mark.xfail(strict=True, reason=COUNTEREXAMPLE)
def test_property_a_every_frost_night_gets_a_vibrate():
    assert not (bad := _violations_of("missed")), bad[:5]


@pytest.mark.xfail(strict=True, reason=COUNTEREXAMPLE)
def test_property_b_no_delivery_repeats_only_known_nights():
    assert not (bad := _violations_of("double")), bad[:5]


# ── contraejemplos mínimos, sin fallos de entrega ni previsión que cambie ────


@pytest.mark.xfail(strict=True, reason=COUNTEREXAMPLE)
def test_counterexample_two_separated_nights_both_near_only_the_first_is_pushed():
    # Noches a lead 1 y lead 4, estables, todas las entregas bien. Sale una sola racha
    # (la de la noche tardía), con su push nombrando la temprana; la tardía no vibra nunca.
    start = START + timedelta(hours=8)
    early, late = DAY0 + timedelta(days=1), DAY0 + timedelta(days=4)
    t = drive(stable({early: 1.0, late: 1.0}), 24 * 5, start=start)
    assert len(t.notifications.deliveries) == 2


@pytest.mark.xfail(strict=True, reason=COUNTEREXAMPLE)
def test_counterexample_earlier_night_under_a_delivered_spell_never_vibrates():
    # Avisada la noche de lead 4; unas horas después aparece, estable, otra 3 noches antes
    # (fuera de la cadena: el hueco admitido es de 2).
    late = DAY0 + timedelta(days=4)
    early = DAY0 + timedelta(days=1)

    def f(now):
        cold = {late: 1.0}
        if now >= START + timedelta(hours=16):
            cold[early] = 1.0
        return stable(cold)(now)
    t = drive(f, 24 * 3, start=START + timedelta(hours=8))
    assert len(t.notifications.deliveries) == 2
