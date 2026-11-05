"""Interruptor de vibración por parcela: API y lectura del worker coinciden."""
import pytest
from fastapi.testclient import TestClient

from app.api import parcel_settings
from app.main import app
from app.middleware import get_tenant_id
from app.worker import frost_alerts

BASE = "/api/risk/parcels"


class FakeStore:
    """Tabla en memoria con las dos consultas SQL que tocan la tabla."""

    def __init__(self):
        self.rows = {}


class FakeCursor:
    def __init__(self, store):
        self.store, self._row = store, None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params):
        if sql.lstrip().startswith("SELECT"):
            tenant, parcel, atype = params
            v = self.store.rows.get((tenant, parcel, atype))
            self._row = None if v is None else {"vibrate": v}
        else:
            tenant, parcel, atype, vibrate = params
            self.store.rows[(tenant, parcel, atype)] = vibrate
            self._row = {"vibrate": vibrate}

    def fetchone(self):
        return self._row


class FakeConn:
    def __init__(self, store):
        self.store = store

    def cursor(self):
        return FakeCursor(self.store)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.fixture
def store(monkeypatch):
    s = FakeStore()
    monkeypatch.setattr(parcel_settings, "get_conn", lambda: FakeConn(s))
    return s


@pytest.fixture
def api():
    app.dependency_overrides[get_tenant_id] = lambda: "tenant-a"
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_default_is_vibrate_true(store, api):
    r = api.get(f"{BASE}/p1/alert-settings/frost")
    assert r.status_code == 200
    assert r.json() == {"vibrate": True}


def test_put_then_get_roundtrip(store, api):
    assert api.put(f"{BASE}/p1/alert-settings/frost", json={"vibrate": False}).json() == {"vibrate": False}
    assert api.get(f"{BASE}/p1/alert-settings/frost").json() == {"vibrate": False}
    api.put(f"{BASE}/p1/alert-settings/frost", json={"vibrate": True})
    assert api.get(f"{BASE}/p1/alert-settings/frost").json() == {"vibrate": True}


def test_settings_are_isolated_by_tenant(store, api):
    api.put(f"{BASE}/p1/alert-settings/frost", json={"vibrate": False})
    app.dependency_overrides[get_tenant_id] = lambda: "tenant-b"
    assert api.get(f"{BASE}/p1/alert-settings/frost").json() == {"vibrate": True}


@pytest.mark.parametrize("bad", ["false", "no", 0, 1, None, {}])
def test_put_rejects_non_boolean(store, api, bad):
    r = api.put(f"{BASE}/p1/alert-settings/frost", json={"vibrate": bad})
    assert r.status_code == 422
    assert store.rows == {}


def test_unknown_alert_type_is_404(store, api):
    assert api.get(f"{BASE}/p1/alert-settings/hail").status_code == 404
    assert api.put(f"{BASE}/p1/alert-settings/hail", json={"vibrate": False}).status_code == 404
    assert store.rows == {}


def test_short_id_is_stored_as_urn(store, api):
    api.put(f"{BASE}/p1/alert-settings/frost", json={"vibrate": False})
    assert list(store.rows) == [("tenant-a", "urn:ngsi-ld:AgriParcel:p1", "frost")]


def test_urn_and_short_id_are_the_same_parcel(store, api):
    api.put(f"{BASE}/urn:ngsi-ld:AgriParcel:p1/alert-settings/frost", json={"vibrate": False})
    assert api.get(f"{BASE}/p1/alert-settings/frost").json() == {"vibrate": False}


def test_non_parcel_urn_is_404(store, api):
    r = api.put(f"{BASE}/urn:ngsi-ld:Device:d1/alert-settings/frost", json={"vibrate": False})
    assert r.status_code == 404
    assert store.rows == {}


def test_worker_read_finds_what_api_wrote(store, api):
    urn = "urn:ngsi-ld:AgriParcel:p1"
    conn = FakeConn(store)
    assert frost_alerts._is_muted(conn, "tenant-a", urn) is False
    api.put(f"{BASE}/p1/alert-settings/frost", json={"vibrate": False})
    assert frost_alerts._is_muted(conn, "tenant-a", urn) is True
    assert frost_alerts._is_muted(conn, "tenant-b", urn) is False
    api.put(f"{BASE}/p1/alert-settings/frost", json={"vibrate": True})
    assert frost_alerts._is_muted(conn, "tenant-a", urn) is False
