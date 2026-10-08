"""Season GDD aggregation from the WeatherObserved `gddAccumulated` series.

`gddAccumulated` is the GDD estimate for the current day, re-published every
~2h and refined as forecast hours become observed. Summing every row counts each
day many times over (a parcel reached 15,000 GDD by October).
"""
from datetime import date, datetime, timezone

from app.worker import weather_source
from app.worker.weather_source import aggregate_season_gdd


def _days(start: date, n: int, value: float = 10.0):
    return [(date.fromordinal(start.toordinal() + i), value) for i in range(n)]


def test_one_value_per_day_is_summed_once():
    start = date(2026, 1, 1)
    result = aggregate_season_gdd(_days(start, 10, 5.0), start, date(2026, 1, 10))
    assert result["gdd_season_total"] == 50.0
    assert result["days_accumulated"] == 10


def test_series_starting_after_season_start_is_not_a_season_total():
    # Weather feed only exists since June: a partial sum is not "season to date".
    start = date(2026, 1, 1)
    result = aggregate_season_gdd(_days(date(2026, 6, 24), 100), start, date(2026, 10, 1))
    assert result is None


def test_series_with_gaps_is_rejected():
    start = date(2026, 1, 1)
    series = _days(start, 20)[::2]  # every other day
    assert aggregate_season_gdd(series, start, date(2026, 1, 20)) is None


def test_small_tolerance_at_season_start_and_end():
    start = date(2026, 3, 1)
    series = _days(date(2026, 3, 2), 29)  # starts 1 day late, today not yet in
    result = aggregate_season_gdd(series, start, date(2026, 3, 31))
    assert result is not None
    assert result["gdd_season_total"] == 290.0


def test_empty_series_returns_none():
    assert aggregate_season_gdd([], date(2026, 1, 1), date(2026, 1, 5)) is None


class _Cursor:
    def __init__(self, rows):
        self.rows, self.sql, self.params = rows, None, None

    def execute(self, sql, params):
        self.sql, self.params = sql, params

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class _Conn:
    def __init__(self, rows):
        self.cur = _Cursor(rows)

    def cursor(self):
        return self.cur


def test_fetch_takes_last_value_per_day_scoped_to_parcel():
    today = datetime.now(timezone.utc).date()
    start = date(today.year, 1, 1)
    rows = [{"day": d, "value": v} for d, v in _days(start, (today - start).days + 1, 4.0)]
    conn = _Conn(rows)

    result = weather_source.fetch_season_gdd(conn, "t1", 1, "urn:ngsi-ld:AgriParcel:p1")

    assert result["gdd_season_total"] == 4.0 * len(rows)
    assert "DISTINCT ON" in conn.cur.sql
    assert "observed_at DESC" in conn.cur.sql
    assert "urn:ngsi-ld:WeatherObserved:t1:parcel-p1" in conn.cur.params


def test_fetch_without_parcel_returns_none():
    # Without a parcel the series would mix every parcel of the tenant.
    assert weather_source.fetch_season_gdd(_Conn([]), "t1", 1, None) is None
