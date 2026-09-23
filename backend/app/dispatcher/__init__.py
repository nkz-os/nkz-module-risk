"""NotificationDispatcher — entrega de avisos por los canales configurados.

Un único punto de salida multicanal (email, push, zulip, webhook/n8n, telegram).
Lee la config de canales del tenant (`risk.tenant_alert_channels`) y persiste el
resultado transaccional de entrega en `risk.alert_deliveries` (metadata, no
telemetría). El historial temporal del aviso vive en Orion/TimescaleDB.
"""
import logging

from app.config import get_settings
from app.db import get_conn
from app.dispatcher import adapters

logger = logging.getLogger(__name__)

_SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


class NotificationDispatcher:
    def __init__(self, settings=None):
        self.settings = settings or get_settings()

    def dispatch(self, tenant_id: str, alert_type: str, severity: str, payload: dict) -> list[dict]:
        s = self.settings
        alert = {
            **payload,
            "tenant_id": tenant_id,
            "alert_type": alert_type,
            "severity": severity,
        }
        channels_cfg = self._get_channels(tenant_id)
        results = []

        plan = [
            ("email", adapters.email_adapter, {"email_service_url": s.email_service_url}),
            ("push", adapters.push_adapter, {"push_service_url": s.push_service_url, "internal_secret": s.internal_secret}),
            ("zulip", adapters.zulip_adapter, {"zulip_service_url": s.zulip_service_url, "internal_secret": s.internal_secret}),
            ("webhook", adapters.webhook_adapter, {}),
            ("telegram", adapters.telegram_adapter, {}),
        ]
        for channel, adapter, kwargs in plan:
            cfg = channels_cfg.get(channel) or {}
            if not cfg.get("enabled"):
                results.append(adapters.DeliveryResult(channel, "skipped"))
                continue
            if not self._meets_min_severity(severity, cfg):
                results.append(adapters.DeliveryResult(channel, "skipped", "below min severity"))
                continue
            try:
                res = adapter(alert, cfg, **kwargs)
            except Exception as e:
                res = adapters.DeliveryResult(channel, "failed", str(e))
            results.append(res)
            self._record_delivery(tenant_id, payload.get("id"), channel, res)

        return [r.to_dict() for r in results]

    def _meets_min_severity(self, severity: str, cfg: dict) -> bool:
        min_sev = cfg.get("min_severity")
        if not min_sev:
            return True
        return _SEVERITY_ORDER.get(severity, 0) >= _SEVERITY_ORDER.get(min_sev, 0)

    def _get_channels(self, tenant_id: str) -> dict:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT email, push, zulip, webhook, telegram "
                    "FROM risk.tenant_alert_channels WHERE tenant_id = %s",
                    (tenant_id,),
                )
                row = cur.fetchone()
                return dict(row) if row else {}
        finally:
            conn.close()

    def _record_delivery(self, tenant_id: str, alert_id: str | None, channel: str, res) -> None:
        if not alert_id:
            return
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO risk.alert_deliveries
                        (alert_id, tenant_id, channel, status, attempts, last_error)
                    VALUES (%s, %s, %s, %s, 1, %s)
                    ON CONFLICT (alert_id, channel) DO UPDATE SET
                        status = EXCLUDED.status,
                        attempts = risk.alert_deliveries.attempts + 1,
                        last_error = EXCLUDED.last_error,
                        updated_at = now()
                    """,
                    (alert_id, tenant_id, channel, res.status, res.error),
                )
            conn.commit()
        except Exception as e:
            logger.warning("record_delivery failed for %s/%s: %s", tenant_id, channel, e)
        finally:
            conn.close()
