"""Risk Backend - API Routes (agregación)."""
from fastapi import APIRouter

from app.api import catalog, alerts, subscriptions, channels, webhooks

router = APIRouter(tags=["risk"])
router.include_router(catalog.router)
router.include_router(alerts.router)
router.include_router(subscriptions.router)
router.include_router(channels.router)
router.include_router(webhooks.router)
