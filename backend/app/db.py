"""Conexión PostgreSQL (sync) al esquema risk.* del módulo.

Solo metadata operativa (catálogo, suscripciones, canales, entregas).
NUNCA telemetría: el historial temporal de Alert va a TimescaleDB vía Orion
(regla ZERO DIRECT DB WRITES).
"""
import psycopg2
from psycopg2.extras import RealDictCursor

from app.config import require_postgres_url


def get_conn():
    return psycopg2.connect(require_postgres_url(), cursor_factory=RealDictCursor)


def set_tenant_context(conn, tenant_id: str) -> None:
    """Setea el contexto RLS del tenant (no-fatal si RLS no está configurado)."""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_tenant', %s, true)", (tenant_id,))
    except Exception:
        pass
