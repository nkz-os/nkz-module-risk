from unittest.mock import MagicMock, patch

from app.dispatcher import adapters


def test_email_adapter_sends_internal_secret():
    with patch.object(adapters.requests, "post", return_value=MagicMock(status_code=200)) as m:
        adapters.email_adapter({"tenant_id": "t", "summary": "s"}, {"enabled": True, "to": "a@b.c"}, "http://email", "sec")
    assert m.call_args.kwargs["headers"]["X-Internal-Service-Secret"] == "sec"
