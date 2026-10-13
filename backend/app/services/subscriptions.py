"""Registro idempotente de las suscripciones Orion-LD del módulo risk.

Registra (y reconcilia periódicamente) las suscripciones que alimentan
`/internal/notify`:
- `Alert` (watchedAttributes=["status"]): entrega de TODAS las Alert del tenant
  por NotificationDispatcher — el único camino de entrega tras PR #1000.
- `AgriParcel` (watchedAttributes=["hasAgriCrop"]): disparo de evaluación de
  riesgo al asignar cultivo.

Dónde está instalado lo dice la plataforma (entity-manager, SDK
`reconcile_installations`), no una consulta propia a la BD: crea donde está
habilitado, quita las suyas donde no, y no toca nada si la plataforma no responde.
Corre al arrancar, cada hora y con el aviso de instalación (`/internal/lifecycle`).
"""

import asyncio
import logging

from nkz_platform_sdk.notify_auth import derived_tokens_from_env
from nkz_platform_sdk.subscriptions import SubscriptionRegistrar

from app.config import get_settings

logger = logging.getLogger(__name__)

MODULE_NAME = "risk"
ALERT_THROTTLING = 15
PARCEL_THROTTLING = 30


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
        # The registrar owns the receiver auth header: the master secret (legacy) or,
        # with NKZ_NOTIFY_DERIVED_TOKENS on, a token bound to the tenant and this
        # receiver so the broker never stores the master.
        internal_secret=secret,
        derived_tokens=derived_tokens_from_env(),
    )


_registrar: SubscriptionRegistrar | None = None


def get_registrar() -> SubscriptionRegistrar | None:
    """One registrar per process: its lock serializes heal and lifecycle hints."""
    global _registrar
    if _registrar is None:
        _registrar = build_registrar()
    return _registrar


async def reconcile_once(registrar: SubscriptionRegistrar | None = None) -> dict | None:
    """Una pasada de reconciliación. Nunca lanza; None sin registrador."""
    registrar = registrar or get_registrar()
    if registrar is None:
        return None
    result = await registrar.reconcile_installations(get_settings().module_id)
    if "skipped" in result:
        logger.warning("Subscription reconcile skipped: %s", result.get("reason"))
        return result
    ensured, removed = result["ensured"], result["removed"]
    logger.info(
        "Subscriptions reconciled: created=%d converged=%d skipped=%d removed=%d errors=%d",
        ensured["created"],
        ensured.get("converged", 0),
        ensured.get("skipped", 0),
        removed["removed"],
        len(ensured["errors"]) + len(removed["errors"]),
    )
    for error in ensured["errors"] + removed["errors"]:
        logger.warning("Subscription reconcile error: %s", error)
    return result


async def run_subscription_reconciler(interval_minutes: int = 60) -> None:
    """Reconcilia ahora y luego cada `interval_minutes`. Nunca lanza."""
    registrar = get_registrar()
    if registrar is None:
        return
    try:
        await reconcile_once(registrar)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("Initial subscription reconcile failed: %s", exc)
    await registrar.periodic_reconcile(get_settings().module_id, interval_minutes)
