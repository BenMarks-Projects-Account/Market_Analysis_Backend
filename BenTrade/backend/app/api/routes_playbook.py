from fastapi import APIRouter, Request

router = APIRouter(prefix="/api", tags=["playbook"])


@router.get("/playbook")
async def get_playbook(request: Request) -> dict:
    return await request.app.state.playbook_service.get_playbook()
