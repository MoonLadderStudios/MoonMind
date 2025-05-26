import logging
from fastapi import APIRouter, HTTPException
from moonmind.models_cache import model_cache # Import the model cache

router = APIRouter(tags=["models"])
logger = logging.getLogger(__name__)

@router.get("/health")
@router.head("/health")
async def health_check():
    return {"status": "healthy"}

@router.get("/")
async def models():
    try:
        # Get all models from the cache
        # The data is already formatted by the cache's _fetch_all_models method
        all_cached_models = model_cache.get_all_models()
        
        if not all_cached_models:
            logger.warning("Model cache returned no models. This might be due to API key issues or errors during cache refresh.")
            # Depending on desired behavior, you might raise an error or return empty list
            # For now, return empty list as per current behavior if both providers fail.

        return {
            "object": "list",
            "data": all_cached_models
        }
    except Exception as e:
        logger.exception(f"Error retrieving models from cache: {e}")
        # This is a more general error, perhaps the cache itself failed unexpectedly.
        raise HTTPException(status_code=500, detail=f"An error occurred while retrieving models: {str(e)}")
