"""Media routes — list, upload, play, delete."""

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File

ALLOWED_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.bmp', '.gif', '.mp4', '.mov', '.avi', '.webm', '.mkv'}


def create_router(deps, require_auth, broadcast_state) -> APIRouter:
    router = APIRouter(prefix="/api/media", tags=["media"])

    @router.get("/list")
    async def list_media():
        return {"items": deps.media_manager.list_items()}

    @router.post("/upload", dependencies=[Depends(require_auth)])
    async def upload_media(file: UploadFile = File(...)):
        suffix = Path(file.filename or "upload").suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            raise HTTPException(400, f"Unsupported file type: {suffix}")

        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp_path = Path(tmp.name)
            total_bytes = 0
            while True:
                chunk = await file.read(64 * 1024)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > deps.max_upload_bytes:
                    tmp_path.unlink(missing_ok=True)
                    raise HTTPException(
                        413,
                        f"File exceeds maximum upload size "
                        f"({deps.max_upload_bytes // (1024*1024)}MB)",
                    )
                tmp.write(chunk)

        try:
            item = await deps.media_manager.import_file(
                tmp_path, file.filename or "upload",
            )
            if item:
                return {"status": "ok", "item": item.to_dict()}
            raise HTTPException(400, "Failed to import media")
        finally:
            tmp_path.unlink(missing_ok=True)

    @router.post("/play/{item_id}", dependencies=[Depends(require_auth)])
    async def play_media(item_id: str, loop: bool = True, speed: float = 1.0):
        if item_id not in deps.media_manager.items:
            raise HTTPException(404, f"Media not found: {item_id}")
        params = {'loop': loop, 'speed': speed}
        success = deps.renderer.activate_scene(
            f"media:{item_id}", params, media_manager=deps.media_manager,
        )
        if not success:
            raise HTTPException(500, f"Failed to activate media: {item_id}")
        await broadcast_state()
        return {"status": "playing", "item_id": item_id}

    @router.delete("/{item_id}", dependencies=[Depends(require_auth)])
    async def delete_media(item_id: str):
        if deps.media_manager.delete_item(item_id):
            return {"status": "deleted"}
        raise HTTPException(404, f"Media not found: {item_id}")

    return router
