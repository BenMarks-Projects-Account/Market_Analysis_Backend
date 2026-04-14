"""Routes for home dashboard refresh state (pause/resume, market hours)."""

from fastapi import APIRouter

from app.services.refresh_state_manager import get_state_manager

router = APIRouter(tags=["refresh"])


@router.get("/api/refresh/state")
async def get_refresh_state():
    """Returns current refresh state for the home dashboard."""
    return get_state_manager().get_state()


@router.post("/api/refresh/pause")
async def pause_refresh():
    """Pauses home dashboard refresh timers."""
    return get_state_manager().pause()


@router.post("/api/refresh/resume")
async def resume_refresh():
    """Resumes home dashboard refresh timers."""
    return get_state_manager().resume()
