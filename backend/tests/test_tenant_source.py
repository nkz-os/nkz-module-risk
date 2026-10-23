"""The module reads tenants through the platform view, never the core table."""

from unittest.mock import MagicMock, patch

from app.api import internal
from app.worker import processor


def _fake_conn(rows):
    cur = MagicMock()
    cur.fetchall.return_value = rows
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    return conn, cur


def _sql(cur) -> str:
    return " ".join(str(c.args[0]) for c in cur.execute.call_args_list)


def test_worker_lists_tenants_from_the_plan_view():
    conn, cur = _fake_conn([])
    with patch("app.db.get_conn", return_value=conn):
        processor.main()
    assert "FROM module_tenant_plan" in _sql(cur)


def test_parcel_tenant_fallback_reads_the_plan_view():
    conn, cur = _fake_conn([])
    internal._resolve_tenant_for_parcel(conn, "urn:ngsi-ld:AgriParcel:p1")
    assert "FROM module_tenant_plan" in _sql(cur)
