"""
Risk Backend - FastAPI Application

Main entry point for the backend service.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.logging_setup import configure_logging
from app.api import router as api_router
from app.api.internal import router as internal_router
from nkz_platform_sdk.lifecycle import lifecycle_hint_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup/shutdown events."""
    settings = get_settings()
    logger.info("%s v%s starting — prefix=%s debug=%s",
                settings.app_name, settings.app_version,
                settings.api_prefix, settings.debug)

    from app.services.subscriptions import run_subscription_reconciler

    reconciler_task = asyncio.create_task(run_subscription_reconciler())

    yield

    reconciler_task.cancel()
    try:
        await reconciler_task
    except asyncio.CancelledError:
        pass
    logger.info("%s shutting down", settings.app_name)


async def _hint_reconcile() -> None:
    from app.services.subscriptions import reconcile_once

    await reconcile_once()


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

    # Install/uninstall hint from entity-manager: verified by the derived
    # notification token, it only triggers a reconcile (SDK).
    app.include_router(
        lifecycle_hint_router(f"{settings.api_prefix}/internal/lifecycle", _hint_reconcile)
    )

    return app


# Create application instance
app = create_app()
