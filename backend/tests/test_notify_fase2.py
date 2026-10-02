"""Tests de la Fase 2: handler de notificaciones + evaluación por parcela."""
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.api import internal
from app.main import app
from app.middleware import verify_internal_secret
from app.worker import processor


def test_evaluate_risks_for_parcel_delegates(monkeypatch):
    calls = {}

    def fake(conn, tenant_id, parcel_filter=None, include_crop_stress=True):
        calls["parcel_filter"] = parcel_filter
        calls["include_crop_stress"] = include_crop_stress
        return {"evaluated": 0, "errors": 0}

    monkeypatch.setattr(processor, "_evaluate_risks", fake)
    r = processor.evaluate_risks_for_parcel(None, "t", "urn:ngsi-ld:AgriParcel:p1")
    assert calls == {
        "parcel_filter": "urn:ngsi-ld:AgriParcel:p1",
        "include_crop_stress": False,
    }
    assert r == {"evaluated": 0, "errors": 0}


def test_eval_parcel_background(monkeypatch):
    conn = MagicMock()
    monkeypatch.setattr(internal, "get_conn", lambda: conn)
    eval_mock = MagicMock(return_value={"evaluated": 1, "errors": 0})
    monkeypatch.setattr("app.worker.processor.evaluate_risks_for_parcel", eval_mock)

    internal._eval_parcel_background("montiko", "urn:ngsi-ld:AgriParcel:p1")

    eval_mock.assert_called_once_with(conn, "montiko", "urn:ngsi-ld:AgriParcel:p1")
    conn.close.assert_called_once()


def test_notify_invalid_payload():
    app.dependency_overrides[verify_internal_secret] = lambda: None
    try:
        with TestClient(app) as c:
            r = c.post("/api/risk/internal/notify", json={"data": "not-a-list"})
            assert r.status_code == 400
    finally:
        app.dependency_overrides.pop(verify_internal_secret, None)


def test_notify_returns_204_without_evaluating_sync():
    app.dependency_overrides[verify_internal_secret] = lambda: None
    with patch("app.api.internal._eval_parcel_background") as bg_mock:
        try:
            with TestClient(app) as c:
                r = c.post(
                    "/api/risk/internal/notify",
                    json={"data": [{"id": "urn:ngsi-ld:AgriParcel:p1", "type": "AgriParcel"}]},
                    headers={"NGSILD-Tenant": "montiko"},
                )
                assert r.status_code == 204
        finally:
            app.dependency_overrides.pop(verify_internal_secret, None)
    # _eval_parcel_background se programa vía run_in_executor (no se espera);
    # el handler devuelve 204 sin ejecutar la evaluación en línea.


def test_notify_dispatches_alert_in_background():
    app.dependency_overrides[verify_internal_secret] = lambda: None
    with patch("app.api.internal._dispatch_alert_background") as bg_mock, patch(
        "app.api.internal._eval_parcel_background"
    ) as eval_mock:
        try:
            with TestClient(app) as c:
                r = c.post(
                    "/api/risk/internal/notify",
                    json={"data": [{"id": "urn:ngsi-ld:Alert:x", "type": "Alert"}]},
                    headers={"NGSILD-Tenant": "montiko"},
                )
                assert r.status_code == 204
        finally:
            app.dependency_overrides.pop(verify_internal_secret, None)
    # se programa el dispatch de Alert (no la evaluación de parcela)
    assert eval_mock.call_count == 0
