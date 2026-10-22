"""Webhooks salientes (targets) — viven dentro de tenant_alert_channels.webhook.

No hay tabla separada: el webhook es un canal más (§5 spec). Esta API gestiona
la lista de targets (url + secret + severidad mínima) de ese canal.
"""
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db import get_conn
from app.middleware import get_tenant_id

router = APIRouter()


class WebhookIn(BaseModel):
    name: str
    url: str
    secret: str | None = None
    min_severity: str = "medium"  # low|medium|high|critical


def _load(conn, tenant_id: str) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT webhook FROM risk.tenant_alert_channels WHERE tenant_id=%s",
            (tenant_id,),
        )
        row = cur.fetchone()
        return (row["webhook"] if row else {"enabled": False, "targets": []})


def _save(conn, tenant_id: str, webhook: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO risk.tenant_alert_channels (tenant_id, webhook)
            VALUES (%s,%s)
            ON CONFLICT (tenant_id) DO UPDATE SET webhook=EXCLUDED.webhook, updated_at=now()
            """,
            (tenant_id, json.dumps(webhook)),
        )
    conn.commit()


@router.get("/webhooks")
def list_webhooks(tenant_id: str = Depends(get_tenant_id)):
    conn = get_conn()
    try:
        return _load(conn, tenant_id).get("targets", [])
    finally:
        conn.close()


@router.post("/webhooks", status_code=201)
def create_webhook(body: WebhookIn, tenant_id: str = Depends(get_tenant_id)):
    if body.min_severity not in ("low", "medium", "high", "critical"):
        raise HTTPException(400, "min_severity must be low|medium|high|critical")
    conn = get_conn()
    try:
        cfg = _load(conn, tenant_id)
        target = {"id": uuid.uuid4().hex[:12], **body.model_dump()}
        cfg.setdefault("targets", []).append(target)
        _save(conn, tenant_id, cfg)
        return target
    finally:
        conn.close()


@router.delete("/webhooks/{target_id}", status_code=204)
def delete_webhook(target_id: str, tenant_id: str = Depends(get_tenant_id)):
    conn = get_conn()
    try:
        cfg = _load(conn, tenant_id)
        before = len(cfg.get("targets", []))
        cfg["targets"] = [t for t in cfg.get("targets", []) if t.get("id") != target_id]
        if len(cfg["targets"]) == before:
            raise HTTPException(404, "webhook not found")
        _save(conn, tenant_id, cfg)
    finally:
        conn.close()
