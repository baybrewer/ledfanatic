"""Transport status route."""

from fastapi import APIRouter


def create_router(deps) -> APIRouter:
    router = APIRouter(prefix="/api/transport", tags=["transport"])

    @router.get("/status")
    async def transport_status():
        return deps.transport.get_status()

    return router
