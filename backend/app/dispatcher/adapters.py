"""Adaptadores de canal para el dispatcher de avisos.

Cada adaptador recibe el payload del aviso y la config de su canal
(`risk.tenant_alert_channels.<channel>`), y devuelve un `DeliveryResult`.
Los canales deshabilitados o sin destino devuelven `skipped` (no es error).
"""
import hashlib
import hmac
import json
import logging

import requests

logger = logging.getLogger(__name__)


class DeliveryResult:
    def __init__(self, channel: str, status: str, error: str | None = None):
        self.channel = channel
        self.status = status  # sent | failed | skipped
        self.error = error

    def to_dict(self) -> dict:
        return {"channel": self.channel, "status": self.status, "error": self.error}


def _webhook_signature(secret: str, body: str) -> dict:
    if not secret:
        return {}
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return {"X-Nekazari-Signature": f"sha256={sig}"}


def email_adapter(alert: dict, config: dict, email_service_url: str) -> DeliveryResult:
    to = config.get("to") or config.get("recipient")
    if not config.get("enabled") or not to:
        return DeliveryResult("email", "skipped")
    try:
        r = requests.post(
            f"{email_service_url}/send/notification",
            json={
                "email": to,
                "farmer_name": alert.get("tenant_id", ""),
                "notification_type": "risk_alert",
                "message": alert.get("summary", ""),
            },
            timeout=10,
        )
        if r.status_code == 200:
            return DeliveryResult("email", "sent")
        return DeliveryResult("email", "failed", r.text[:200])
    except Exception as e:
        return DeliveryResult("email", "failed", str(e))


def push_adapter(alert: dict, config: dict, push_service_url: str, internal_secret: str) -> DeliveryResult:
    if not config.get("enabled"):
        return DeliveryResult("push", "skipped")
    try:
        r = requests.post(
            f"{push_service_url}/send/push",
            json={
                "tenant_id": alert.get("tenant_id"),
                "title": alert.get("title", "Risk Alert"),
                "body": alert.get("summary", ""),
                "data": alert.get("data", {}),
            },
            headers={"X-Internal-Secret": internal_secret},
            timeout=10,
        )
        if r.status_code == 200:
            return DeliveryResult("push", "sent")
        return DeliveryResult("push", "failed", r.text[:200])
    except Exception as e:
        return DeliveryResult("push", "failed", str(e))


def zulip_adapter(alert: dict, config: dict, zulip_service_url: str, internal_secret: str) -> DeliveryResult:
    if not config.get("enabled"):
        return DeliveryResult("zulip", "skipped")
    stream = config.get("stream", "alerts")
    topic = config.get("topic", "notifications")
    try:
        r = requests.post(
            f"{zulip_service_url}/internal/message",
            json={"stream": stream, "topic": topic, "content": alert.get("summary", "")},
            headers={"X-Internal-Service-Secret": internal_secret},
            timeout=10,
        )
        if r.status_code == 200:
            return DeliveryResult("zulip", "sent")
        return DeliveryResult("zulip", "failed", r.text[:200])
    except Exception as e:
        return DeliveryResult("zulip", "failed", str(e))


def webhook_adapter(alert: dict, config: dict) -> DeliveryResult:
    """Webhook genérico (cubre N8N). config: {enabled, targets:[{url, secret, min_severity}]}"""
    if not config.get("enabled"):
        return DeliveryResult("webhook", "skipped")
    targets = config.get("targets") or []
    if not targets:
        return DeliveryResult("webhook", "skipped")
    body = json.dumps(alert)
    statuses = []
    for t in targets:
        try:
            headers = {"Content-Type": "application/json"}
            headers.update(_webhook_signature(t.get("secret"), body))
            r = requests.post(t["url"], data=body, headers=headers, timeout=5)
            statuses.append(r.status_code)
        except Exception as e:
            statuses.append(f"error:{e}")
    ok = bool(statuses) and all(isinstance(s, int) and 200 <= s < 300 for s in statuses)
    return DeliveryResult("webhook", "sent" if ok else "failed", str(statuses))


def telegram_adapter(alert: dict, config: dict) -> DeliveryResult:
    # Bot de Telegram aún no existe — adaptador opcional, no-op.
    return DeliveryResult("telegram", "skipped")
