"""
Risk Backend - Internal Routes

Called by entity-manager and other in-cluster services — NEVER by the
browser, so these routes bypass api-gateway and carry no X-Tenant-ID /
X-User-ID headers. They authenticate via X-Internal-Service-Secret instead
(see app.middleware.verify_internal_secret).

`/internal/notify` is the NGSI-LD subscription receiver: Orion-LD notifies
it when `AgriParcel.hasAgriCrop` changes (crop assignment), so the module
evaluates that parcel's risks immediately instead of waiting for the hourly
batch. The subscription is created by SubscriptionRegistrar at startup with
`notification_headers={"X-Internal-Service-Secret": ...}`, which Orion sends
back on each notification.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response

from app.db import get_conn
from app.middleware import verify_internal_secret

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal", tags=["internal"], dependencies=[Depends(verify_internal_secret)])


@router.post("/ping")
async def ping() -> dict:
    """Minimal example of an internal-secret-authenticated route."""
    return {"status": "ok"}


def _resolve_tenant_for_parcel(conn, parcel_id: str) -> str | None:
    """Fallback: busca en qué tenant vive la parcela (si Orion no envió el header)."""
    from nkz_platform_sdk import SyncOrionClient

    from app.config import get_settings

    settings = get_settings()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT tenant_id FROM tenants "
                "WHERE status = 'active' AND tenant_id IS NOT NULL"
            )
            tenants = [r["tenant_id"] for r in cur.fetchall()]
    except Exception as exc:  # noqa: BLE001
        logger.warning("tenant list failed: %s", exc)
        return None

    for tenant in tenants:
        try:
            with SyncOrionClient(
                tenant, base_url=settings.orion_ld_url, context_url=settings.context_url
            ) as orion:
                if orion.get_entity(parcel_id):
                    return tenant
        except Exception:  # noqa: BLE001
            continue
    return None


def _eval_parcel_background(tenant_hint: str | None, parcel_id: str) -> None:
    """Evalúa una parcela en segundo plano (hilo) con su propia conexión.

    Corre fuera del handler para que /notify responda 204 al momento y no
    supere el timeout de Orion (que pausa la suscripción tras 3 fallos).
    """
    from app.worker.processor import evaluate_risks_for_parcel

    conn = get_conn()
    try:
        tenant_id = tenant_hint or _resolve_tenant_for_parcel(conn, parcel_id)
        if not tenant_id:
            logger.warning("notify: cannot resolve tenant for %s", parcel_id)
            return
        result = evaluate_risks_for_parcel(conn, tenant_id, parcel_id)
        logger.info("notify: parcel risk eval %s/%s -> %s", tenant_id, parcel_id, result)
    except Exception as exc:  # noqa: BLE001
        logger.warning("notify: parcel risk eval failed %s: %s", parcel_id, exc)
    finally:
        conn.close()


@router.post("/notify", status_code=204)
async def ngsi_ld_notify(
    request: Request,
    x_ngsild_tenant: str | None = Header(None, alias="NGSILD-Tenant"),
):
    """Recibe notificaciones NGSI-LD (AgriParcel.hasAgriCrop) y evalúa la parcela.

    Contrato Orion-LD: responder 204 SIN esperar la evaluación. La evaluación se
    lanza fire-and-forget en un hilo (con conexión propia) para no bloquear la
    respuesta ni superar el timeout de Orion.
    """
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="invalid JSON")

    data = payload.get("data")
    if not isinstance(data, list):
        raise HTTPException(status_code=400, detail="invalid payload")

    loop = asyncio.get_running_loop()
    for entity in data:
        if not isinstance(entity, dict) or entity.get("type") != "AgriParcel":
            continue
        parcel_id = entity.get("id")
        if not parcel_id:
            continue
        loop.run_in_executor(None, _eval_parcel_background, x_ngsild_tenant, parcel_id)

    return Response(status_code=204)
