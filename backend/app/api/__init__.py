"""Risk Backend - API Routes (agregación)."""
from fastapi import APIRouter

from app.api import catalog, alerts, subscriptions, channels, webhooks, parcel_settings

router = APIRouter(tags=["risk"])
router.include_router(catalog.router)
router.include_router(alerts.router)
router.include_router(subscriptions.router)
router.include_router(channels.router)
router.include_router(webhooks.router)
router.include_router(parcel_settings.router)
