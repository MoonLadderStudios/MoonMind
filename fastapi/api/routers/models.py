import logging
import time

from fastapi import APIRouter, HTTPException
from moonmind.config.settings import settings
from moonmind.factories.google_factory import list_google_models

router = APIRouter(tags=["models"])
logger = logging.getLogger(__name__)

@router.get("/health")
@router.head("/health")
async def health_check():
    return {"status": "healthy"}

@router.get("/models")
@router.get("/v1/models")
async def models():
    try:
        data = []

        google_models = list_google_models()
        for model in google_models:
            context_window = model.input_token_limit
            if context_window is None:
                if 'embedContent' in model.supported_generation_methods:
                    context_window = 1024
                else:
                    context_window = 8192

            capabilities = {
                "chat_completion": False,
                "text_completion": False,
                "embedding": False,
            }
            if 'generateContent' in model.supported_generation_methods:
                capabilities["chat_completion"] = True
                capabilities["text_completion"] = True
            if 'embedContent' in model.supported_generation_methods:
                capabilities["embedding"] = True

            model_data = {
                "id": model.name,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "Google",
                "permission": [],
                "root": model.name,
                "parent": None,
                "context_window": context_window,
                "capabilities": capabilities,
            }
            data.append(model_data)

        return {
            "object": "list",
            "data": data
        }
    except Exception as e:
        logger.exception(f"Error getting models: {e}")
        raise HTTPException(status_code=500, detail=str(e))
