import time

from fastapi import APIRouter, HTTPException

from .common import get_qdrant

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
                    "id": "Local",
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "moonquery",
                    "permission": [],
                    "root": "Local",
                    "parent": None,
                    "context_window": 4096,
                    "capabilities": {
                        "chat_completion": True,
                        "text_completion": True
                    }
                }
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/models/load")
@router.get("/v1/models/load")
async def load_models():
    try:
        get_qdrant().load_models()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))