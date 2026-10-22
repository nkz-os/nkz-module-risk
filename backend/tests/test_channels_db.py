"""Test de integración real de PUT /channels contra PostgreSQL.

Verifica que los 5 dicts de canal se envuelven en psycopg2.extras.Json (sin
eso, psycopg2 lanza `ProgrammingError: can't adapt type 'dict'`). Se salta si
POSTGRES_URL no está definida (entorno de unit tests sin DB); en CI se levanta
un PostgreSQL de servicio.
"""
import os

import pytest

POSTGRES_URL = os.getenv("POSTGRES_URL")

pytestmark = pytest.mark.skipif(
    not POSTGRES_URL, reason="POSTGRES_URL not set (no DB in unit env)"
)


@pytest.fixture()
def _schema():
    import psycopg2

    conn = psycopg2.connect(POSTGRES_URL)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE SCHEMA IF NOT EXISTS risk")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS risk.tenant_alert_channels (
                    tenant_id TEXT PRIMARY KEY,
                    email     JSONB NOT NULL DEFAULT '{"enabled":false}'::jsonb,
                    push      JSONB NOT NULL DEFAULT '{"enabled":false}'::jsonb,
                    zulip     JSONB NOT NULL DEFAULT '{"enabled":false}'::jsonb,
                    webhook   JSONB NOT NULL DEFAULT '{"enabled":false}'::jsonb,
                    telegram  JSONB NOT NULL DEFAULT '{"enabled":false}'::jsonb,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute("DELETE FROM risk.tenant_alert_channels WHERE tenant_id = 'test-tenant'")
    finally:
        conn.close()


def test_put_channels_roundtrip(_schema):
    from app.api.channels import ChannelsIn, put_channels

    body = ChannelsIn(email={"enabled": True, "to": "x@y.z"})
    result = put_channels(body, "test-tenant")

    # JSONB vuelve como dict de Python: el INSERT+RETURNING funcionó de verdad.
    assert result["email"] == {"enabled": True, "to": "x@y.z"}
    assert result["push"] == {"enabled": False}
