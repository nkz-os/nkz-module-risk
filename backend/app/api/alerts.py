"""Lectura de Alert activos desde Orion-LD (bus canónico, no Postgres)."""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from nkz_platform_sdk import SyncOrionClient

from app.config import get_settings
from app.middleware import get_tenant_id

router = APIRouter()


def _parcel_names(client: SyncOrionClient) -> dict[str, str]:
    """Mapa parcela URN -> name, en una sola consulta Orion."""
    names: dict[str, str] = {}
    try:
        parcels = client.query_entities(type="AgriParcel", limit=1000)
    except Exception:  # noqa: BLE001
        return names
    for p in parcels:
        pid = p.get("id")
        name = p.get("name")
        if isinstance(name, dict):
            name = name.get("value")
        if pid and name:
            names[pid] = name
    return names


def _unwrap_ref(entity: dict) -> str:
    """El URN de la parcela referenciada por una Alert (refEntity)."""
    ref = entity.get("refEntity")
    if isinstance(ref, dict):
        return str(ref.get("object") or "")
    return str(ref or "")


@router.get("/alerts")
def get_alerts(
    tenant_id: str = Depends(get_tenant_id),
    category: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
):
    """Avisos Alert activos del tenant, filtrables por category/severity.

    Añade `refEntityName` (el nombre de la parcela referenciada) para que el
    frontend no muestre el UUID crudo.
    """
    s = get_settings()
    q_parts = []
    if category:
        q_parts.append(f'category=="{category}"')
    if severity:
        q_parts.append(f'severity=="{severity}"')
    q = ";".join(q_parts) or None

    with SyncOrionClient(tenant_id, base_url=s.orion_ld_url, context_url=s.context_url) as client:
        entities = client.query_entities(type="Alert", q=q, limit=limit)
        parcel_names = _parcel_names(client)

    for e in entities:
        e["refEntityName"] = parcel_names.get(_unwrap_ref(e), "")
    return {"alerts": entities, "count": len(entities)}
