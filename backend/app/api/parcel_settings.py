"""Interruptor de vibración por parcela (esquema risk.parcel_alert_settings).

Solo apaga la vibración del móvil para el aviso de helada: el aviso sigue en la
app. Sin fila, vibra. `parcel_id` se guarda siempre como URN completo, la misma
forma que lee el worker de helada.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, StrictBool

from app.db import get_conn
from app.middleware import get_tenant_id

router = APIRouter()

PARCEL_URN_PREFIX = "urn:ngsi-ld:AgriParcel:"
SETTING_ALERT_TYPES = {"frost"}


class VibrateIn(BaseModel):
    vibrate: StrictBool


def normalize_parcel_id(parcel_id: str) -> str:
    """Id corto -> URN de AgriParcel. Un URN de otro tipo no es una parcela."""
    pid = parcel_id.strip()
    if not pid:
        raise HTTPException(status_code=404, detail="Parcel not found")
    if pid.startswith("urn:"):
        if not pid.startswith(PARCEL_URN_PREFIX) or len(pid) == len(PARCEL_URN_PREFIX):
            raise HTTPException(status_code=404, detail="Parcel not found")
        return pid
    return PARCEL_URN_PREFIX + pid


def _check_type(alert_type: str) -> None:
    if alert_type not in SETTING_ALERT_TYPES:
        raise HTTPException(status_code=404, detail="Unknown alert type")


@router.get("/parcels/{parcel_id}/alert-settings/{alert_type}")
def get_parcel_alert_setting(
    parcel_id: str, alert_type: str, tenant_id: str = Depends(get_tenant_id)
):
    _check_type(alert_type)
    urn = normalize_parcel_id(parcel_id)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT vibrate FROM risk.parcel_alert_settings "
                "WHERE tenant_id = %s AND parcel_id = %s AND alert_type = %s",
                (tenant_id, urn, alert_type),
            )
            row = cur.fetchone()
        return {"vibrate": True if row is None else bool(row["vibrate"])}
    finally:
        conn.close()


@router.put("/parcels/{parcel_id}/alert-settings/{alert_type}")
def put_parcel_alert_setting(
    parcel_id: str,
    alert_type: str,
    body: VibrateIn,
    tenant_id: str = Depends(get_tenant_id),
):
    _check_type(alert_type)
    urn = normalize_parcel_id(parcel_id)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO risk.parcel_alert_settings
                    (tenant_id, parcel_id, alert_type, vibrate)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (tenant_id, parcel_id, alert_type) DO UPDATE SET
                    vibrate = EXCLUDED.vibrate, updated_at = now()
                RETURNING vibrate
                """,
                (tenant_id, urn, alert_type, body.vibrate),
            )
            row = cur.fetchone()
        conn.commit()
        return {"vibrate": bool(row["vibrate"])}
    finally:
        conn.close()
