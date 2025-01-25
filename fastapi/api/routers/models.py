import time

from fastapi import APIRouter, HTTPException

from moonmind.config.settings import settings

router = APIRouter(tags=["models"])

@router.get("/health")
@router.head("/health")
async def health_check():
    return {"status": "healthy"}

@router.get("/models")
@router.get("/v1/models")
async def models():
    try:
        return {
            "object": "list",
            "data": [
                {
                    "id": settings.app_name,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": settings.app_name,
                    "permission": [],
                    "root": settings.app_name,
                    "parent": None,
                    "context_window": 4096, # TODO: get from somewhere
                    "capabilities": {
                        "chat_completion": True,
                        "text_completion": True
                    }
                }
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
