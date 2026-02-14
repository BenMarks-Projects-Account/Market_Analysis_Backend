from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

router = APIRouter(tags=["frontend"])


def _no_cache_file_response(path: Path) -> FileResponse:
    return FileResponse(
        path,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@router.get("/")
async def dashboard(request: Request):
    frontend_dir: Path = request.app.state.frontend_dir
    index_path = frontend_dir / "index.html"
    if index_path.exists():
        return _no_cache_file_response(index_path)
    raise HTTPException(status_code=404, detail="Dashboard not found")


@router.get("/{filename:path}")
async def frontend_files(filename: str, request: Request):
    if filename.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not found")

    frontend_dir: Path = request.app.state.frontend_dir
    backend_dir: Path = request.app.state.backend_dir

    target = frontend_dir / filename
    if target.exists() and target.is_file():
        return _no_cache_file_response(target)

    alt = backend_dir / filename
    if alt.exists() and alt.is_file():
        return _no_cache_file_response(alt)

    raise HTTPException(status_code=404, detail="Not found")
