"""Aviso de helada de punta a punta, hora a hora, contra un oráculo independiente.

Corre lo real (`process_frost_parcel`, `load_existing`, `publish_alert._build`,
`sweep_expired`) sobre un broker falso en forma normalizada con ida y vuelta JSON, y
simula notifications: solo `urgency=vibrate` es inmediato, deduplica por (id, severidad),
anota solo lo enviado y libera al cerrarse el aviso.

El oráculo no conoce cadenas, rangos ni el estado de las Alert: saca las noches en riesgo
de sus propios umbrales y solo mira lo que ve notifications y la app, es decir el id, la
urgencia y la lista de noches publicada.
"""
import functools
import json
import logging
import random
from collections import Counter
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
NEAR = 4
MARGIN = 0.5


# ── reglas del oráculo, escritas aparte del código ───────────────────


def oracle_threshold(lead: int) -> float:
    if lead <= 4:
        return 3.5
    return 4.0 if lead == 5 else 4.5


def oracle_trigger_leads(hour: int) -> range:
    return range(0 if hour < 10 else 1, 8)


def oracle_at_risk(values: Dict[int, Optional[float]], hour: int, margin: float = 0.0) -> Dict[int, float]:
    """{lead: tmin} de las noches en riesgo según el oráculo."""
    return {
        lead: values[lead] for lead in oracle_trigger_leads(hour)
        if values.get(lead) is not None and values[lead] <= oracle_threshold(lead) + margin
    }


def oracle_near_complete(values: Optional[Dict[int, Optional[float]]], hour: int) -> bool:
    if values is None:
        return False
    first = 0 if hour < 10 else 1
    return all(values.get(lead) is not None for lead in range(first, NEAR + 1))


def oracle_horizon_complete(values: Dict[int, Optional[float]], hour: int) -> bool:
    return all(values.get(lead) is not None for lead in oracle_trigger_leads(hour))


def imminent_window(lead: int, hour: int) -> bool:
    return (lead == 1 and hour >= 18) or (lead == 0 and hour < 10)


def quiet(hour: int) -> bool:
    return hour >= 22 or hour < 8


# ── broker y notifications falsos ────────────────────────────────────


@dataclass
class Delivery:
    at: datetime
    alert_id: str
    severity: str
    body: str
    nights: Set[date]


def listed_nights(entity: dict) -> Set[date]:
    return {date.fromisoformat(n["day"]) for n in entity["evaluationData"]["value"].get("nights", [])}


class FakeNotifications:
    """Lo que hace notifications con cada Alert que Orion le notifica.

    `one_row_per_channel=False` es el modelo pedido: se recuerda cada (id, severidad).
    `True` es la tabla real, `alert_deliveries`, con clave (id, tenant, canal): cada envío
    o fallo pisa la severidad de la fila, y `has_delivered` solo acierta con la última.
    """

    def __init__(self, fail: Callable[[], bool], one_row_per_channel: bool = False):
        self.fail = fail
        self.one_row = one_row_per_channel
        self.sent: Set[tuple] = set()
        self.rows: Dict[str, tuple] = {}
        self.deliveries: List[Delivery] = []
        self.failed_attempts: List[tuple] = []
        self.closes = 0

    def _has_delivered(self, aid: str, severity: str) -> bool:
        if self.one_row:
            return self.rows.get(aid) == (severity, "sent")
        return (aid, severity) in self.sent

    def on_upsert(self, entity: dict, at: datetime) -> None:
        if entity["urgency"]["value"] != "vibrate":
            return  # silent: va a la app y al resumen
        key = (entity["id"], entity["severity"]["value"])
        if self._has_delivered(*key):
            return
        if self.fail():
            self.rows[key[0]] = (key[1], "failed")
            self.failed_attempts.append((at, key))
            return
        self.rows[key[0]] = (key[1], "sent")
        self.sent.add(key)
        self.deliveries.append(Delivery(
            at, key[0], key[1], entity["plain"]["value"]["es"]["body"], listed_nights(entity)))

    def on_close(self, alert_id: str) -> None:
        self.sent = {k for k in self.sent if k[0] != alert_id}
        self.rows.pop(alert_id, None)
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
    one_row_per_channel: bool = False,
    after_run: Optional[Callable[[datetime, "FakeBroker", "FakeNotifications", str], None]] = None,
) -> Trace:
    """`forecast(now)` da {lead: tmin} (ausente = noche sin dato), o None si no hay dato."""
    notifications = FakeNotifications(fail, one_row_per_channel)
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
                if after_run is not None:
                    after_run(now, broker, notifications, outcome)
        finally:
            logging.disable(logging.NOTSET)
    return trace


def stable(cold: Dict[date, float], warm: float = 10.0):
    def f(now: datetime):
        return {lead: cold.get(now.date() + timedelta(days=lead), warm) for lead in range(0, 8)}
    return f


def active(broker: FakeBroker) -> List[dict]:
    return [v for v in broker.e.values() if v["status"]["value"] == "active"]


def bodies(trace: Trace) -> List[str]:
    return [d.body for d in trace.notifications.deliveries]


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


def test_outage_keeps_every_alert_with_its_night_ahead():
    cold = stable({DAY0 + timedelta(days=2): 1.0, DAY0 + timedelta(days=6): 1.0})

    def f(now):
        return None if now >= START + timedelta(hours=12) else cold(now)
    t = drive(f, 24 + 12)
    assert len(active(t.broker)) == 2
    assert not [v for v in t.broker.e.values() if v["status"]["value"] == "expired"]


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


# ── heladas separadas: secuencias que el modelo de una racha no cumplía ──


def test_two_separated_cold_nights_stable_give_one_push_each_naming_its_night():
    # 02/12 y 05/12, estables, todas las entregas bien.
    early, late = DAY0 + timedelta(days=1), DAY0 + timedelta(days=4)
    t = drive(stable({early: 1.0, late: 1.0}), 24 * 5, start=START + timedelta(hours=8))
    assert len(t.notifications.deliveries) == 2
    assert len({d.alert_id for d in t.notifications.deliveries}) == 2
    assert sorted(bodies(t)) == ["Esta noche puede bajar a 1 °C.",
                                 "Noche del viernes 04/12: puede bajar a 1 °C."]


def test_later_night_first_then_earlier_night_appears_gives_one_push_each():
    # 05/12 avisada; unas horas después aparece, estable, 02/12, fuera de la cadena.
    late, early = DAY0 + timedelta(days=4), DAY0 + timedelta(days=1)

    def f(now):
        cold = {late: 1.0}
        if now >= START + timedelta(hours=16):
            cold[early] = 1.0
        return stable(cold)(now)
    t = drive(f, 24 * 5, start=START + timedelta(hours=8))
    assert len(t.notifications.deliveries) == 2
    assert len({d.alert_id for d in t.notifications.deliveries}) == 2
    assert bodies(t) == ["Noche del viernes 04/12: puede bajar a 1 °C.",
                         "Esta noche puede bajar a 1 °C."]


def test_a_parcel_can_hold_two_active_alerts_and_the_first_closing_leaves_the_second():
    early, late = DAY0 + timedelta(days=1), DAY0 + timedelta(days=4)
    # Desde el 01/12 08:15: la última pasada es el 02/12 a las 09:15, y luego a las 10:15.
    t = drive(stable({early: 1.0, late: 1.0}), 24 + 2, start=START + timedelta(hours=8))
    assert len(active(t.broker)) == 2
    t = drive(stable({early: 1.0, late: 1.0}), 24 + 3, start=START + timedelta(hours=8))
    assert [listed_nights(a) for a in active(t.broker)] == [{late}]


# ── (c) una noche que oscila en torno al umbral ──────────────────────


@pytest.mark.parametrize("phase", [0, 1])
@pytest.mark.parametrize("delta_lead", [3, 6])
def test_c_night_hovering_around_its_threshold_gives_one_vibrate(phase, delta_lead):
    night = DAY0 + timedelta(days=delta_lead)

    def f(now):
        v = {lead: 10.0 for lead in range(0, 8)}
        lead = (night - now.date()).days
        if 0 <= lead <= 7:
            up = ((now - START).total_seconds() // (3 * 3600) + phase) % 2
            v[lead] = oracle_threshold(lead) + (0.3 if up else -0.3)
        return v
    t = drive(f, 24 * delta_lead + 9)
    assert len(t.notifications.deliveries) == 1, [d.at for d in t.notifications.deliveries]
    assert "resolved" not in {o for _, o in t.outcomes}


def test_c_hovering_never_closes_the_alert():
    # Noche a lead 3 oscilando ±0,3 °C: la Alert no sale de `active` al cruzar el umbral.
    # (Se reescribe cuando la mínima redondeada cambia de 3 a 4 °C: es la regla de publicación.)
    night = DAY0 + timedelta(days=3)
    statuses = []

    def f(now):
        up = ((now - START).total_seconds() // (3 * 3600)) % 2
        return stable({night: 3.5 + (0.3 if up else -0.3)})(now)
    t = drive(f, 24, after_run=lambda now, b, n, o: statuses.append(
        tuple(sorted((k, v["status"]["value"]) for k, v in b.e.items()))))
    assert len(t.broker.e) == 1
    assert {s[0][1] for s in statuses} == {"active"}


# ── propiedad con oráculo independiente ──────────────────────────────


class Oracle:
    """(a) y (b) a partir de lo publicado y entregado, sin saber nada de cadenas."""

    def __init__(self):
        self.credited: Set[str] = set()      # Alert con push entregado desde su último cierre
        self.pushed_nights: Dict[str, Set[date]] = {}
        self.violations: List[tuple] = []
        self.delivered: Counter = Counter()
        self.closed_since: Set[tuple] = set()
        self.windows_failed_out: Set[date] = set()
        self.windows_covered: Set[date] = set()
        self.covered_by_extension: Set[date] = set()
        self.repeats_across_close = 0

    def observe(self, now, values, broker, deliveries, failed) -> None:
        current: Dict[str, Set[date]] = {}
        for aid, entity in broker.e.items():
            if entity["status"]["value"] == "active":
                current[aid] = listed_nights(entity)
            else:
                self.credited.discard(aid)
                self.pushed_nights.pop(aid, None)
                self.closed_since |= {k for k in self.delivered if k[0] == aid}

        for d in deliveries:
            key = (d.alert_id, d.severity)
            if self.delivered[key]:
                if key in self.closed_since:
                    self.repeats_across_close += 1
                else:
                    self.violations.append(("double_id", now.isoformat(), d.alert_id, d.severity))
            self.delivered[key] += 1
            self.closed_since.discard(key)
            self.credited.add(d.alert_id)
            self.pushed_nights.setdefault(d.alert_id, set()).update(d.nights)

        if not oracle_near_complete(values, now.hour):
            return
        failed_ids = {key[0] for _, key in failed}
        for lead in oracle_at_risk(values, now.hour):
            if not imminent_window(lead, now.hour):
                continue
            day = now.date() + timedelta(days=lead)
            listing = [aid for aid, nights in current.items() if day in nights]
            pushed = [aid for aid in listing if aid in self.credited]
            if pushed:
                self.windows_covered.add(day)
                if not any(day in self.pushed_nights.get(aid, ()) for aid in pushed):
                    self.covered_by_extension.add(day)
            elif any(aid in failed_ids for aid in listing):
                self.windows_failed_out.add(day)  # lo intentó esta hora y falló la entrega
            else:
                self.violations.append(("missed", day.isoformat(), now.isoformat(), lead, listing))


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


@dataclass
class SimResult:
    violations: List[tuple]
    oracle: Oracle
    deliveries: int
    failed_attempts: int


def _simulate(seed: int, days: int = 10, failure_rate: float = 0.3,
              one_row_per_channel: bool = False) -> SimResult:
    rng = random.Random(seed)
    base = _random_forecast(rng, days)
    fail_rng = random.Random(seed + 1)
    oracle = Oracle()
    bad: List[tuple] = []
    seen = {"values": None, "status": {}, "deliveries": 0, "failed": 0}

    def forecast(now):
        seen["values"] = base(now)
        return seen["values"]

    def after_run(now, broker, notifications, outcome):
        values, hour = seen["values"], now.hour
        near_ok = oracle_near_complete(values, hour)
        # Coherencia: sin dato de las noches cercanas nunca hay publicación ni resolución.
        if not near_ok:
            if outcome != "no_data":
                bad.append(("no_data_mismatch", now.isoformat(), outcome, values))
            closed = [k for k, v in broker.e.items()
                      if seen["status"].get(k) == "active" and v["status"]["value"] == "resolved"]
            if closed:
                bad.append(("no_data_resolved", now.isoformat(), closed))
        else:
            if oracle_at_risk(values, hour) and outcome in ("resolved", "none", "no_data"):
                bad.append(("resolved_at_risk", now.isoformat(), outcome, values))
            if oracle_horizon_complete(values, hour) and outcome == "no_data":
                bad.append(("no_data_mismatch", now.isoformat(), outcome, values))
            if not oracle_at_risk(values, hour, margin=MARGIN) and outcome in ("published", "unchanged"):
                bad.append(("alert_without_risk", now.isoformat(), outcome, values))
        oracle.observe(
            now, values, broker,
            notifications.deliveries[seen["deliveries"]:],
            notifications.failed_attempts[seen["failed"]:],
        )
        seen["deliveries"] = len(notifications.deliveries)
        seen["failed"] = len(notifications.failed_attempts)
        seen["status"] = {k: v["status"]["value"] for k, v in broker.e.items()}

    t = drive(forecast, days * 24, fail=lambda: fail_rng.random() < failure_rate,
              one_row_per_channel=one_row_per_channel, after_run=after_run)
    return SimResult(bad + oracle.violations, oracle, len(t.notifications.deliveries),
                     len(t.notifications.failed_attempts))


SEEDS = 80


@functools.lru_cache(maxsize=None)
def _all_violations(one_row_per_channel: bool = False) -> tuple:
    return tuple(
        (20261105 + seed,) + v for seed in range(SEEDS)
        for v in _simulate(20261105 + seed, one_row_per_channel=one_row_per_channel).violations
    )


def _violations_of(*kinds: str, one_row_per_channel: bool = False) -> List[tuple]:
    return [v for v in _all_violations(one_row_per_channel) if v[1] in kinds]


def test_property_outcome_agrees_with_the_oracle():
    assert not (bad := _violations_of(
        "no_data_mismatch", "resolved_at_risk", "alert_without_risk", "no_data_resolved"
    )), bad[:3]


@pytest.mark.parametrize("one_row", [False, True])
def test_property_a_every_frost_night_gets_a_delivered_vibrate_by_its_window(one_row):
    assert not (bad := _violations_of("missed", one_row_per_channel=one_row)), bad[:5]


def test_property_b_at_most_one_delivery_per_id_and_severity_between_closes():
    assert not (bad := _violations_of("double_id")), bad[:5]


def test_property_the_oracle_is_not_vacuous():
    sims = [_simulate(20261105 + seed) for seed in range(10)]
    assert sum(len(s.oracle.windows_covered) for s in sims) > 20
    assert sum(s.failed_attempts for s in sims) > 10


# ── (d) previsión estable ────────────────────────────────────────────


def _stable_random(seed: int, days: int = 10):
    rng = random.Random(seed)
    cold = {DAY0 + timedelta(days=d): (rng.uniform(-8.0, 4.8) if rng.random() < 0.35 else 10.0)
            for d in range(0, days + 9)}
    return stable(cold)


@pytest.mark.parametrize("failure_rate", [0.0, 0.3])
def test_property_d_stable_forecast_pushes_at_most_twice_per_alert_and_respects_quiet_hours(failure_rate):
    for seed in range(60):
        fail_rng = random.Random(seed + 1)
        t = drive(_stable_random(20261105 + seed), 24 * 10,
                  fail=lambda: fail_rng.random() < failure_rate)
        per_id = Counter(d.alert_id for d in t.notifications.deliveries)
        assert max(per_id.values(), default=0) <= 2, (seed, per_id)
        for d in t.notifications.deliveries:
            if quiet(d.at.hour):
                leads = {(n - d.at.date()).days for n in d.nights}
                assert 0 in leads or (1 in leads and d.at.hour >= 18), (seed, d)
