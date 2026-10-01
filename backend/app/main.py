"""
Risk Backend - FastAPI Application

Main entry point for the backend service.
"""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.logging_setup import configure_logging
from app.api import router as api_router
from app.api.internal import router as internal_router

logger = logging.getLogger(__name__)


async def _ensure_crop_assignment_subscription() -> None:
    """Registra la suscripción Orion a AgriParcel.hasAgriCrop (idempotente).

    Al asignar (o reasignar) cultivo, Orion notifica /api/risk/internal/notify,
    que evalúa los riesgos de esa parcela inmediatamente. El batch horario
    sigue siendo el fallback si Orion no entrega la notificación.
    """
    from nkz_platform_sdk.subscriptions import SubscriptionRegistrar

    from app.db import get_conn

    settings = get_settings()
    secret = settings.internal_service_secret
    if not secret:
        logger.warning(
            "INTERNAL_SERVICE_SECRET not set — subscription notifications "
            "would fail auth; skipping crop-assignment subscription"
        )
        return

    try:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT DISTINCT tenant_id FROM tenants "
                    "WHERE status = 'active' AND tenant_id IS NOT NULL"
                )
                tenants = [r["tenant_id"] for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("tenant list for subscription failed: %s", exc)
        return

    registrar = SubscriptionRegistrar(
        orion_url=settings.orion_ld_url,
        notification_url=(
            f"http://risk-module-api-service:8000"
            f"{settings.api_prefix}/internal/notify"
        ),
        subscriptions=[
            {"type": "AgriParcel", "watched_attributes": ["hasAgriCrop"], "throttling": 30}
        ],
        module_name="risk",
        context_url=settings.context_url,
        notification_headers={"X-Internal-Service-Secret": secret},
    )
    try:
        result = await registrar.ensure_all(tenants)
        logger.info("crop-assignment subscription ensured: %s", result)
    except Exception as exc:  # noqa: BLE001
        logger.warning("crop-assignment subscription setup failed: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup/shutdown events."""
    settings = get_settings()
    logger.info("%s v%s starting — prefix=%s debug=%s",
                settings.app_name, settings.app_version,
                settings.api_prefix, settings.debug)
    await _ensure_crop_assignment_subscription()
    yield
    logger.info("%s shutting down", settings.app_name)


def create_app() -> FastAPI:
    """Application factory."""
    settings = get_settings()
    configure_logging(settings.log_level)
    
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Risk - Backend API for Nekazari Platform",
        docs_url=f"{settings.api_prefix}/docs",
        redoc_url=f"{settings.api_prefix}/redoc",
        openapi_url=f"{settings.api_prefix}/openapi.json",
        lifespan=lifespan,
    )
    
    # CORS Middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Health check (at root for k8s probes)
    @app.get("/health")
    async def health_check():
        """Health check endpoint for Kubernetes probes."""
        return {
            "status": "healthy",
            "service": settings.app_name,
            "version": settings.app_version,
        }
    
    # Include API routes
    app.include_router(api_router, prefix=settings.api_prefix)

    # Internal routes (entity-manager, other in-cluster callers) — authenticated
    # by X-Internal-Service-Secret, NOT gateway headers. See app/api/internal.py.
    app.include_router(internal_router, prefix=settings.api_prefix)

    return app


# Create application instance
app = create_app()
