"""Tests del dispatcher y adaptadores de canal (mecanismo Fase 2)."""
from types import SimpleNamespace
from unittest.mock import patch

from app.dispatcher import adapters, NotificationDispatcher


def test_email_adapter_sends():
    with patch("app.dispatcher.adapters.requests.post") as post:
        post.return_value.status_code = 200
        r = adapters.email_adapter(
            {"tenant_id": "t1", "summary": "Frost risk"},
            {"enabled": True, "to": "a@b.com"},
            "http://email",
        )
        assert r.status == "sent"
        args, kwargs = post.call_args
        assert args[0] == "http://email/send/notification"
        assert kwargs["json"]["email"] == "a@b.com"


def test_email_adapter_skips_without_recipient():
    r = adapters.email_adapter({}, {"enabled": True}, "http://email")
    assert r.status == "skipped"


def test_webhook_adapter_signs():
    with patch("app.dispatcher.adapters.requests.post") as post:
        post.return_value.status_code = 200
        r = adapters.webhook_adapter(
            {"id": "a1"},
            {"enabled": True, "targets": [{"url": "http://hook", "secret": "s3cret"}]},
        )
        assert r.status == "sent"
        _, kwargs = post.call_args
        assert kwargs["headers"]["X-Nekazari-Signature"].startswith("sha256=")


def test_zulip_adapter_uses_internal_secret():
    with patch("app.dispatcher.adapters.requests.post") as post:
        post.return_value.status_code = 200
        r = adapters.zulip_adapter(
            {"summary": "hi"}, {"enabled": True}, "http://zulip", "secret123"
        )
        assert r.status == "sent"
        _, kwargs = post.call_args
        assert kwargs["headers"]["X-Internal-Service-Secret"] == "secret123"
        assert kwargs["json"]["stream"] == "alerts"


def test_telegram_adapter_noop():
    assert adapters.telegram_adapter({}, {"enabled": True}).status == "skipped"


def test_dispatcher_routes_to_enabled_channels():
    d = NotificationDispatcher.__new__(NotificationDispatcher)
    d.settings = SimpleNamespace(
        email_service_url="e", push_service_url="p",
        zulip_service_url="z", internal_service_secret="s",
    )
    d._get_channels = lambda tid: {
        "email": {"enabled": True, "to": "a@b.com"},
        "push": {"enabled": False},
        "zulip": {"enabled": True},
        "webhook": {"enabled": False},
        "telegram": {"enabled": False},
    }
    d._record_delivery = lambda *a, **k: None

    with patch("app.dispatcher.adapters.email_adapter", return_value=adapters.DeliveryResult("email", "sent")) as email, \
         patch("app.dispatcher.adapters.zulip_adapter", return_value=adapters.DeliveryResult("zulip", "sent")) as zulip:
        results = d.dispatch("t1", "frost", "high", {"id": "a1", "summary": "x"})
        email.assert_called_once()
        zulip.assert_called_once()
        sent = {r["channel"] for r in results if r["status"] == "sent"}
        assert sent == {"email", "zulip"}
