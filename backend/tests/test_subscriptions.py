"""Subscriptions follow where the platform says risk is installed — no DB read."""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.services import subscriptions


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("INTERNAL_SERVICE_SECRET", "test-internal-secret")
    get_settings.cache_clear()  # settings are cached by earlier tests
    monkeypatch.setattr(subscriptions, "_registrar", None)
    yield
    get_settings.cache_clear()


def test_registrar_is_one_per_process():
    assert subscriptions.get_registrar() is subscriptions.get_registrar()


@pytest.mark.asyncio
async def test_reconcile_asks_the_platform_not_the_database(monkeypatch):
    def no_db(*a, **k):
        raise AssertionError("must not read tenant_installed_modules")

    monkeypatch.setattr("app.db.get_conn", no_db, raising=False)
    registrar = subscriptions.get_registrar()
    registrar.reconcile_installations = AsyncMock(
        return_value={"ensured": {"created": 1, "converged": 0, "skipped": 0, "errors": []},
                      "removed": {"removed": 0, "absent": 2, "errors": []}})
    result = await subscriptions.reconcile_once()
    registrar.reconcile_installations.assert_awaited_once_with("risk")
    assert result["ensured"]["created"] == 1


@pytest.mark.asyncio
async def test_reconcile_unavailable_is_logged_not_raised():
    registrar = subscriptions.get_registrar()
    registrar.reconcile_installations = AsyncMock(
        return_value={"skipped": "unavailable", "reason": "503"})
    assert (await subscriptions.reconcile_once())["skipped"] == "unavailable"


def test_lifecycle_hint_route_requires_auth():
    from app.main import app

    client = TestClient(app)
    assert client.post("/api/risk/internal/lifecycle").status_code == 401
    r = client.post("/api/risk/internal/lifecycle",
                    headers={"X-Internal-Service-Secret": "test-internal-secret"})
    assert r.status_code == 202
