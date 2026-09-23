"""Config multicanal por tenant (esquema risk.tenant_alert_channels).

Una sola fuente de config para email/push/zulip/webhook/telegram. Cada canal
es un JSONB libre (el dispatcher de Fase 2 lo interpreta). El webhook gestiona
además una lista de targets (ver webhooks.py).
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db import get_conn
from app.middleware import get_tenant_id

router = APIRouter()

_DEFAULTS = {
    "email": {"enabled": False},
    "push": {"enabled": False},
    "zulip": {"enabled": False},
    "webhook": {"enabled": False, "targets": []},
    "telegram": {"enabled": False},
}


class ChannelsIn(BaseModel):
    email: dict | None = None
    push: dict | None = None
    zulip: dict | None = None
    webhook: dict | None = None
    telegram: dict | None = None


@router.get("/channels")
def get_channels(tenant_id: str = Depends(get_tenant_id)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT email, push, zulip, webhook, telegram "
                "FROM risk.tenant_alert_channels WHERE tenant_id=%s",
                (tenant_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else _DEFAULTS
    finally:
        conn.close()


@router.put("/channels")
def put_channels(body: ChannelsIn, tenant_id: str = Depends(get_tenant_id)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT email, push, zulip, webhook, telegram "
                "FROM risk.tenant_alert_channels WHERE tenant_id=%s",
                (tenant_id,),
            )
            row = cur.fetchone()
            current = dict(row) if row else {**_DEFAULTS}
            merged = {k: body.model_dump().get(k) or current[k] for k in _DEFAULTS}
            cur.execute(
                """
                INSERT INTO risk.tenant_alert_channels
                    (tenant_id, email, push, zulip, webhook, telegram)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (tenant_id) DO UPDATE SET
                    email=EXCLUDED.email, push=EXCLUDED.push, zulip=EXCLUDED.zulip,
                    webhook=EXCLUDED.webhook, telegram=EXCLUDED.telegram, updated_at=now()
                RETURNING email, push, zulip, webhook, telegram
                """,
                (tenant_id, merged["email"], merged["push"], merged["zulip"],
                 merged["webhook"], merged["telegram"]),
            )
            conn.commit()
            return dict(cur.fetchone())
    finally:
        conn.close()
