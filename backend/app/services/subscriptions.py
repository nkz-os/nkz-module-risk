"""Registro idempotente de las suscripciones Orion-LD del módulo risk.

Registra (y reconcilia periódicamente) las suscripciones que alimentan
`/internal/notify`:
- `Alert` (watchedAttributes=["status"]): entrega de TODAS las Alert del tenant
  por NotificationDispatcher — el único camino de entrega tras PR #1000.
- `AgriParcel` (watchedAttributes=["hasAgriCrop"]): disparo de evaluación de
  riesgo al asignar cultivo.

Solo en tenants con el módulo instalado y habilitado (`tenant_installed_modules`).
El SDK >=0.8.5 hace PATCH sobre 409 y reactiva suscripciones pausadas.
"""

import asyncio
import logging

from nkz_platform_sdk.subscriptions import SubscriptionRegistrar

from app.config import get_settings
from app.db import get_conn

logger = logging.getLogger(__name__)

MODULE_NAME = "risk"
ALERT_THROTTLING = 15
PARCEL_THROTTLING = 30


def installed_tenants() -> list[str]:
    """Tenants con este módulo instalado y habilitado (tenant_installed_modules)."""
    settings = get_settings()
    if not settings.postgres_url:
        logger.error("POSTGRES_URL not set — cannot resolve tenants for subscriptions")
        return []
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT tenant_id FROM tenant_installed_modules "
                "WHERE module_id = %s AND is_enabled "
                "AND tenant_id IS NOT NULL AND tenant_id <> ''",
                (settings.module_id,),
            )
            return sorted(r["tenant_id"] for r in cur.fetchall())
    except Exception as exc:  # noqa: BLE001
        logger.warning("installed tenants query failed: %s", exc)
        return []
    finally:
        conn.close()


def build_registrar() -> SubscriptionRegistrar | None:
    """Las suscripciones declaradas, o None si no se pueden autenticar.

    Sin el secreto, Orion entregaría notificaciones que /internal/notify
    rechaza y pausaría las suscripciones tras tres fallos.
    """
    settings = get_settings()
    secret = settings.internal_service_secret
    if not secret:
        logger.error(
            "INTERNAL_SERVICE_SECRET not set — subscriptions NOT registered; "
            "/internal/notify receives nothing"
        )
        return None
    return SubscriptionRegistrar(
        orion_url=settings.orion_ld_url,
        notification_url=(
            f"http://risk-module-api-service:8000{settings.api_prefix}/internal/notify"
        ),
        subscriptions=[
            {
                "type": "AgriParcel",
                "watched_attributes": ["hasAgriCrop"],
                "throttling": PARCEL_THROTTLING,
            },
        ],
        module_name=MODULE_NAME,
        context_url=settings.context_url,
        notification_headers={"X-Internal-Service-Secret": secret},
    )


async def _installed_tenants_async() -> list[str]:
    return await asyncio.to_thread(installed_tenants)


async def reconcile_once(registrar: SubscriptionRegistrar) -> dict | None:
    """Una pasada de reconciliación. Nunca lanza; None si falló la lectura de tenants."""
    try:
        tenants = await _installed_tenants_async()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Subscription reconcile skipped, tenant lookup failed: %s", exc)
        return None
    result = await registrar.ensure_all(tenants)
    logger.info(
        "Subscriptions reconciled for %s: created=%d converged=%d skipped=%d errors=%d",
        tenants,
        result["created"],
        result.get("converged", 0),
        result.get("skipped", 0),
        len(result["errors"]),
    )
    for error in result["errors"]:
        logger.warning("Subscription reconcile error: %s", error)
    return result


async def run_subscription_reconciler(interval_minutes: int = 60) -> None:
    """Reconcilia ahora y luego cada `interval_minutes`. Nunca lanza."""
    registrar = build_registrar()
    if registrar is None:
        return
    try:
        await reconcile_once(registrar)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("Initial subscription reconcile failed: %s", exc)
    await registrar.periodic_heal(_installed_tenants_async, interval_minutes=interval_minutes)
