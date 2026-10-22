"""Tests for Risk Backend"""
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


class TestHealth:
    def test_health_check(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "healthy"
        assert "service" in data
        assert "version" in data


class TestAPI:
    def test_docs_available(self, client):
        assert client.get("/api/risk/docs").status_code in [200, 307]

    def test_openapi_schema_lists_module_paths(self, client):
        r = client.get("/api/risk/openapi.json")
        assert r.status_code == 200
        paths = r.json()["paths"]
        for p in [
            "/api/risk/catalog",
            "/api/risk/alerts",
            "/api/risk/subscriptions",
            "/api/risk/channels",
            "/api/risk/webhooks",
        ]:
            assert p in paths, f"missing path {p}"

    def test_auth_required(self, client):
        # sin cabeceras de gateway -> rechazado (401)
        assert client.get("/api/risk/catalog").status_code == 401


class TestInternal:
    @pytest.fixture(autouse=True)
    def _secret(self, monkeypatch):
        monkeypatch.setenv("INTERNAL_SERVICE_SECRET", "test-internal-secret")
        get_settings.cache_clear()
        yield
        get_settings.cache_clear()

    def test_internal_rejects_missing_secret(self, client):
        assert client.post("/api/risk/internal/ping").status_code == 401

    def test_internal_rejects_wrong_secret(self, client):
        r = client.post(
            "/api/risk/internal/ping",
            headers={"X-Internal-Service-Secret": "wrong"},
        )
        assert r.status_code == 401

    def test_internal_accepts_correct_secret(self, client):
        r = client.post(
            "/api/risk/internal/ping",
            headers={"X-Internal-Service-Secret": "test-internal-secret"},
        )
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


class TestNotifyAuth:
    @pytest.fixture(autouse=True)
    def _secret(self, monkeypatch):
        monkeypatch.setenv("INTERNAL_SERVICE_SECRET", "test-internal-secret")
        get_settings.cache_clear()
        yield
        get_settings.cache_clear()

    def test_notify_rejects_without_credentials(self, client):
        r = client.post("/api/risk/internal/notify", json={"data": []})
        assert r.status_code == 401

    def test_notify_accepts_master_and_derived_token(self, client, monkeypatch):
        from nkz_platform_sdk.notify_auth import TOKEN_HEADER, notification_token

        url = "/api/risk/internal/notify"
        body = {"data": []}
        r = client.post(
            url, json=body,
            headers={"NGSILD-Tenant": "tenant-a", "X-Internal-Service-Secret": "test-internal-secret"},
        )
        assert r.status_code == 204
        # Starlette's TestClient sends Host: testserver; the token binds to that host.
        token = notification_token("test-internal-secret", "tenant-a", "testserver")
        monkeypatch.setenv("NKZ_NOTIFY_ACCEPT_MASTER", "false")
        ok = client.post(url, json=body, headers={"NGSILD-Tenant": "tenant-a", TOKEN_HEADER: token})
        assert ok.status_code == 204
        other = client.post(url, json=body, headers={"NGSILD-Tenant": "tenant-b", TOKEN_HEADER: token})
        assert other.status_code == 401
        master = client.post(
            url, json=body,
            headers={"NGSILD-Tenant": "tenant-a", "X-Internal-Service-Secret": "test-internal-secret"},
        )
        assert master.status_code == 401
