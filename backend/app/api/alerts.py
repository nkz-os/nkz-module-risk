"""Lectura de Alert activos desde Orion-LD (bus canónico, no Postgres)."""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from nkz_platform_sdk import SyncOrionClient

from app.config import get_settings
from app.middleware import AuthContext, get_current_user, get_tenant_id

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


def _build_query(category: Optional[str], severity: Optional[str], status: str) -> Optional[str]:
    """Filtro NGSI-LD; por defecto solo las `active` (`status=all` = historial)."""
    q_parts = []
    if category:
        q_parts.append(f'category=="{category}"')
    if severity:
        q_parts.append(f'severity=="{severity}"')
    if status != "all":
        q_parts.append(f'status=="{status}"')
    return ";".join(q_parts) or None


@router.get("/alerts")
def get_alerts(
    tenant_id: str = Depends(get_tenant_id),
    category: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    status: str = Query("active", pattern="^(active|resolved|expired|dismissed|all)$"),
    limit: int = Query(200, ge=1, le=1000),
):
    """Avisos Alert del tenant (por defecto `active`), filtrables por category/severity/status.

    Añade `refEntityName` (el nombre de la parcela referenciada) para que el
    frontend no muestre el UUID crudo.
    """
    s = get_settings()
    q = _build_query(category, severity, status)

    with SyncOrionClient(tenant_id, base_url=s.orion_ld_url, context_url=s.context_url) as client:
        entities = client.query_entities(type="Alert", q=q, limit=limit)
        parcel_names = _parcel_names(client)

    for e in entities:
        e["refEntityName"] = parcel_names.get(_unwrap_ref(e), "")
    return {"alerts": entities, "count": len(entities)}


def _norm_tenant(t: str) -> str:
    return t.strip().lower().replace("-", "_")


def dismiss(client, tenant_id: str, alert_id: str, user_id: str, now: datetime) -> dict:
    """Marca la Alert como `dismissed`. Siempre permitido para el usuario.

    El worker no la reabre mientras la condición siga; cuando termine pasa a
    `resolved` y una nueva aparición es un aviso nuevo.
    """
    parts = alert_id.split(":")
    # urn:ngsi-ld:Alert:{tenant}:{alert_type}:{entity}
    if len(parts) < 6 or parts[2] != "Alert" or _norm_tenant(parts[3]) != _norm_tenant(tenant_id):
        raise HTTPException(status_code=404, detail="Alert not found")
    if not client.get_entity(alert_id):
        raise HTTPException(status_code=404, detail="Alert not found")
    client.append_entity_attrs(alert_id, {
        "status": {"type": "Property", "value": "dismissed"},
        "dismissedAt": {"type": "Property", "value": now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")},
        "dismissedBy": {"type": "Property", "value": user_id},
    })
    return {"id": alert_id, "status": "dismissed"}


@router.post("/alerts/{alert_id}/dismiss")
def dismiss_alert(alert_id: str, auth: AuthContext = Depends(get_current_user)):
    s = get_settings()
    with SyncOrionClient(auth.tenant_id, base_url=s.orion_ld_url, context_url=s.context_url) as client:
        return dismiss(client, auth.tenant_id, alert_id, auth.user_id or "", datetime.now(timezone.utc))
