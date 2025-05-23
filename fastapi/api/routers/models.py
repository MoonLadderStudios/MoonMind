import logging
import time

from fastapi import APIRouter, HTTPException
from moonmind.config.settings import settings
from moonmind.factories.google_factory import list_google_models
from moonmind.factories.openai_factory import list_openai_models

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

        try:
            openai_models_list = list_openai_models()
            for model in openai_models_list:
                # Assuming a default context window for OpenAI models for now
                # and default capabilities for chat models
                context_window = 4096 # Default, adjust if model info provides it
                if "gpt-4" in model.id: # Example of adjusting for a specific model
                    context_window = 8192
                
                capabilities = {
                    "chat_completion": True, # Assuming chat models support this
                    "text_completion": True, # Assuming chat models support this
                    "embedding": False, # Adjust if embedding models are listed/handled
                }

                model_data = {
                    "id": model.id,
                    "object": "model",
                    "created": int(time.time()), # Using current time as created time
                    "owned_by": "OpenAI",
                    "permission": [],
                    "root": model.id,
                    "parent": None,
                    "context_window": context_window,
                    "capabilities": capabilities,
                }
                data.append(model_data)
        except Exception as e:
            logger.error(f"Error listing OpenAI models: {e}")
            # Optionally, decide if this error should prevent Google models from being returned
            # For now, it logs the error and continues

        return {
            "object": "list",
            "data": data
        }
    except Exception as e:
        logger.exception(f"Error getting models: {e}")
        raise HTTPException(status_code=500, detail=str(e))
