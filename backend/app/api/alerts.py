"""Lectura de Alert activos desde Orion-LD (bus canónico, no Postgres)."""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from nkz_platform_sdk import SyncOrionClient

from app.config import get_settings
from app.middleware import get_tenant_id

router = APIRouter()


@router.get("/alerts")
def get_alerts(
    tenant_id: str = Depends(get_tenant_id),
    category: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
):
    """Avisos Alert activos del tenant, filtrables por category/severity."""
    s = get_settings()
    q_parts = []
    if category:
        q_parts.append(f'category=="{category}"')
    if severity:
        q_parts.append(f'severity=="{severity}"')
    q = ";".join(q_parts) or None

    with SyncOrionClient(tenant_id, base_url=s.orion_ld_url, context_url=s.context_url) as client:
        entities = client.query_entities(type="Alert", q=q, limit=limit)
    return {"alerts": entities, "count": len(entities)}
