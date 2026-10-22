"""Suscripciones por tenant+alertType (esquema risk.tenant_alert_subscriptions)."""
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db import get_conn
from app.middleware import get_tenant_id

router = APIRouter()


class SubIn(BaseModel):
    alert_type: str
    is_active: bool = True
    user_threshold: float = 50
    channels: dict = {"email": True, "push": False}
    entity_filters: dict = {}


class SubPatch(BaseModel):
    is_active: bool | None = None
    user_threshold: float | None = None
    channels: dict | None = None
    entity_filters: dict | None = None


@router.get("/subscriptions")
def list_subs(tenant_id: str = Depends(get_tenant_id)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM risk.tenant_alert_subscriptions "
                "WHERE tenant_id=%s ORDER BY alert_type",
                (tenant_id,),
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


@router.post("/subscriptions", status_code=201)
def create_sub(body: SubIn, tenant_id: str = Depends(get_tenant_id)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO risk.tenant_alert_subscriptions
                    (tenant_id, alert_type, is_active, user_threshold, channels, entity_filters)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (tenant_id, alert_type) DO UPDATE SET
                    is_active=EXCLUDED.is_active,
                    user_threshold=EXCLUDED.user_threshold,
                    channels=EXCLUDED.channels,
                    entity_filters=EXCLUDED.entity_filters,
                    updated_at=now()
                RETURNING *
                """,
                (tenant_id, body.alert_type, body.is_active, body.user_threshold,
                 json.dumps(body.channels), json.dumps(body.entity_filters)),
            )
            conn.commit()
            return dict(cur.fetchone())
    finally:
        conn.close()


@router.patch("/subscriptions/{sub_id}")
def patch_sub(sub_id: int, body: SubPatch, tenant_id: str = Depends(get_tenant_id)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            updates, params = [], []
            if body.is_active is not None:
                updates.append("is_active=%s"); params.append(body.is_active)
            if body.user_threshold is not None:
                updates.append("user_threshold=%s"); params.append(body.user_threshold)
            if body.channels is not None:
                updates.append("channels=%s"); params.append(json.dumps(body.channels))
            if body.entity_filters is not None:
                updates.append("entity_filters=%s"); params.append(json.dumps(body.entity_filters))
            if not updates:
                raise HTTPException(400, "no fields to update")
            updates.append("updated_at=now()")
            params.extend([sub_id, tenant_id])
            cur.execute(
                f"UPDATE risk.tenant_alert_subscriptions SET {', '.join(updates)} "
                "WHERE id=%s AND tenant_id=%s RETURNING *",
                params,
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "subscription not found")
            conn.commit()
            return dict(row)
    finally:
        conn.close()


@router.delete("/subscriptions/{sub_id}", status_code=204)
def delete_sub(sub_id: int, tenant_id: str = Depends(get_tenant_id)):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM risk.tenant_alert_subscriptions WHERE id=%s AND tenant_id=%s RETURNING id",
                (sub_id, tenant_id),
            )
            if not cur.fetchone():
                raise HTTPException(404, "subscription not found")
            conn.commit()
    finally:
        conn.close()
