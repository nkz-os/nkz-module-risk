"""Tests de la Fase 2: handler de notificaciones + evaluación por parcela."""
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

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


def test_notify_invalid_payload():
    app.dependency_overrides[verify_internal_secret] = lambda: None
    try:
        with TestClient(app) as c:
            r = c.post("/api/risk/internal/notify", json={"data": "not-a-list"})
            assert r.status_code == 400
    finally:
        app.dependency_overrides.pop(verify_internal_secret, None)


def test_notify_processes_parcel():
    app.dependency_overrides[verify_internal_secret] = lambda: None
    conn = MagicMock()
    with patch("app.api.internal.get_conn", return_value=conn), patch(
        "app.worker.processor.evaluate_risks_for_parcel",
        return_value={"evaluated": 1, "errors": 0},
    ) as eval_mock:
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

    assert eval_mock.call_count == 1
    args = eval_mock.call_args[0]
    assert args[1] == "montiko"
    assert args[2] == "urn:ngsi-ld:AgriParcel:p1"
