"""Catálogo de alertTypes (esquema risk.alert_catalog)."""
from fastapi import APIRouter, Depends

from app.db import get_conn
from app.middleware import get_tenant_id

router = APIRouter()


@router.get("/catalog")
def get_catalog(tenant_id: str = Depends(get_tenant_id)):
    """AlertTypes activos: globales (tenant_id NULL) + custom del tenant (fix B2)."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT alert_type, name, description, category, model_type,
                       severity_levels, is_active
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
