"""Notification API routes — list, count, mark-read, clear.

Endpoints
─────────
    GET  /api/notifications        — list recent notifications
    GET  /api/notifications/count  — unread count
    POST /api/notifications/read   — mark one or all as read
    POST /api/notifications/clear  — clear all notifications
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query

from app.services.notification_service import get_notification_service

logger = logging.getLogger("bentrade.routes_notifications")

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("")
async def get_notifications(
    limit: int = Query(50, ge=1, le=200),
    unread_only: bool = Query(False),
) -> dict[str, Any]:
    """Return recent notifications with unread count."""
    svc = get_notification_service()
    return {
        "notifications": svc.get_notifications(limit=limit, unread_only=unread_only),
        "unread_count": svc.get_unread_count(),
    }


@router.get("/count")
async def get_notification_count() -> dict[str, int]:
    """Return just the unread count (lightweight poll)."""
    return {"unread_count": get_notification_service().get_unread_count()}


@router.post("/read")
async def mark_notifications_read(
    notification_id: str = Query(None),
) -> dict[str, bool]:
    """Mark one notification (by id) or all as read."""
    get_notification_service().mark_read(notification_id)
    return {"ok": True}


@router.post("/clear")
async def clear_notifications() -> dict[str, bool]:
    """Clear all notifications."""
    get_notification_service().clear()
    return {"ok": True}
