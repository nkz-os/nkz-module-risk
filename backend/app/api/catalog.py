"""Catálogo de alertTypes (esquema risk.alert_catalog)."""
import json
import uuid
from typing import List, Union

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db import get_conn
from app.middleware import get_tenant_id, require_roles
from app.worker.sources import SOURCE_CATALOG

router = APIRouter()

_OPERATORS = {"<", "<=", ">", ">=", "==", "!=", "in", "not_in", "between"}
_AGGREGATES = {"sum", "avg", "min", "max", "delta"}
_SEVERITIES = {"low", "medium", "high", "critical"}


class Condition(BaseModel):
    source: str
    attribute: str
    operator: str
    value: Union[float, int, str, List[Union[float, int, str]]]
    unit: str | None = None
    duration_minutes: int = 0
    aggregate: str | None = None
    severity: str = "medium"


class ConditionGroup(BaseModel):
    logical_operator: str = "AND"
    min_conditions: int | None = None
    conditions: List[Union[Condition, "ConditionGroup"]]


ConditionGroup.model_rebuild()


class CustomRiskIn(BaseModel):
    name: str
    description: str = ""
    category: str = "agronomic"
    target_sdm_type: str = "AgriParcel"
    data_sources: List[str]
    conditions: ConditionGroup  # el árbol; en BD se guarda como model_config


def _validate_tree(group: ConditionGroup, sources: List[str]) -> None:
    op = (group.logical_operator or "AND").upper()
    if op not in ("AND", "OR") and not op.startswith("COUNT"):
        raise HTTPException(400, f"unknown logical_operator: {group.logical_operator}")
    for cond in group.conditions:
        if isinstance(cond, ConditionGroup):
            _validate_tree(cond, sources)
            continue
        if cond.source not in SOURCE_CATALOG:
            raise HTTPException(400, f"unknown source: {cond.source}")
        if cond.source not in sources:
            raise HTTPException(400, f"source {cond.source} not declared in data_sources")
        if cond.operator not in _OPERATORS:
            raise HTTPException(400, f"unknown operator: {cond.operator}")
        if cond.operator == "between" and not isinstance(cond.value, list):
            raise HTTPException(400, "operator 'between' requires a [lo, hi] list value")
        if cond.aggregate is not None and cond.aggregate not in _AGGREGATES:
            raise HTTPException(400, f"unknown aggregate: {cond.aggregate}")
        if cond.severity not in _SEVERITIES:
            raise HTTPException(400, f"invalid severity: {cond.severity}")


@router.get("/catalog")
def get_catalog(tenant_id: str = Depends(get_tenant_id)):
    """AlertTypes activos: globales (tenant_id NULL) + custom del tenant (fix B2)."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT alert_type, name, description, category, model_type,
                       severity_levels, is_active, tenant_id,
                       applicable_crop_groups, documentation
                FROM risk.alert_catalog
                WHERE is_active = true
                  AND (tenant_id IS NULL OR tenant_id = %s)
                ORDER BY category, alert_type
                """,
                (tenant_id,),
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


@router.get("/catalog/sources")
def get_catalog_sources():
    """Fuentes + atributos disponibles para construir riesgos a medida (UI)."""
    return SOURCE_CATALOG


@router.post("/catalog/custom", status_code=201)
def create_custom_risk(
    body: CustomRiskIn,
    tenant_id: str = Depends(get_tenant_id),
    _auth=Depends(require_roles("TenantAdmin", "PlatformAdmin")),
):
    """Crea un riesgo a medida (threshold) — tenant-scoped, corrige la fuga B2."""
    _validate_tree(body.conditions, body.data_sources)

    risk_code = f"custom_{uuid.uuid4().hex[:12]}"
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO risk.alert_catalog
                    (alert_type, name, description, category, model_type,
                     target_sdm_type, data_sources, model_config, is_active, tenant_id)
                VALUES (%s,%s,%s,%s,'threshold',%s,%s,%s,true,%s)
                RETURNING alert_type
                """,
                (
                    risk_code,
                    body.name,
                    body.description,
                    body.category,
                    body.target_sdm_type,
                    json.dumps(body.data_sources),
                    body.conditions.model_dump_json(),
                    tenant_id,
                ),
            )
            conn.commit()
            return {"alert_type": risk_code, "tenant_id": tenant_id}
    finally:
        conn.close()


@router.patch("/catalog/{alert_type}")
def update_catalog_entry(
    alert_type: str,
    body: dict,
    tenant_id: str = Depends(get_tenant_id),
    _auth=Depends(require_roles("TenantAdmin", "PlatformAdmin")),
):
    """Actualiza campos editables de una ficha (soft, sin tocar model_type)."""
    allowed = {"is_active", "applicable_crop_groups", "documentation", "description"}
    fields = {k: v for k, v in body.items() if k in allowed}
    if not fields:
        raise HTTPException(400, "no editable fields provided")

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tenant_id FROM risk.alert_catalog WHERE alert_type = %s",
                (alert_type,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "alert_type not found")
            if row["tenant_id"] is not None and row["tenant_id"] != tenant_id:
                raise HTTPException(403, "cannot edit another tenant's risk")

            set_clauses = []
            params = []
            for k, v in fields.items():
                if k == "applicable_crop_groups":
                    set_clauses.append("applicable_crop_groups = %s::text[]")
                    params.append(v if isinstance(v, list) else [v])
                elif k == "documentation":
                    set_clauses.append("documentation = %s::jsonb")
                    params.append(json.dumps(v))
                else:
                    set_clauses.append(f"{k} = %s")
                    params.append(v)
            params.append(alert_type)
            cur.execute(
                f"UPDATE risk.alert_catalog SET {', '.join(set_clauses)} WHERE alert_type = %s",
                params,
            )
            conn.commit()
    finally:
        conn.close()
    return {"alert_type": alert_type, "updated": sorted(fields)}


@router.delete("/catalog/{alert_type}", status_code=204)
def delete_catalog_entry(
    alert_type: str,
    tenant_id: str = Depends(get_tenant_id),
    _auth=Depends(require_roles("TenantAdmin", "PlatformAdmin")),
):
    """Soft-delete: is_active=false (no borrar la fila, preserva historial)."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tenant_id FROM risk.alert_catalog WHERE alert_type = %s",
                (alert_type,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "alert_type not found")
            if row["tenant_id"] is not None and row["tenant_id"] != tenant_id:
                raise HTTPException(403, "cannot delete another tenant's risk")
            cur.execute(
                "UPDATE risk.alert_catalog SET is_active = false WHERE alert_type = %s",
                (alert_type,),
            )
            conn.commit()
    finally:
        conn.close()
