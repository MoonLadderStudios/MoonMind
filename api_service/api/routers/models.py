import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException

from api_service.auth_providers import get_current_user  # Auth dependency
from api_service.db.base import get_async_session_context
from api_service.db.models import User  # User model for type hinting
from moonmind.models_cache import model_cache  # Import the model cache

router = APIRouter(tags=["models"])
logger = logging.getLogger(__name__)


@router.get("/health", include_in_schema=False)  # Health checks should be public
@router.head("/health", include_in_schema=False)
async def health_check():  # Public
    return {"status": "healthy"}


@router.get("/")
async def models(_user: User = Depends(get_current_user())):  # Protected
    try:
        google_api_key = None
        openai_api_key = None
        try:
            async with get_async_session_context() as db_session:
                from api_service.api.routers.chat import get_user_api_key

                google_api_key = await get_user_api_key(_user, "google", db_session)
                openai_api_key = await get_user_api_key(_user, "openai", db_session)
        except (
            Exception
        ) as exc:  # pragma: no cover - DB optional in many test/dev paths
            logger.debug(
                "Falling back to system model provider keys for /v1/models: %s", exc
            )

        # Get all models from the cache.
        # Using asyncio.to_thread to avoid blocking the event loop, as
        # get_all_models does sync I/O when refreshing.
        all_cached_models = await asyncio.to_thread(
            model_cache.get_all_models_for_user,
            google_api_key=google_api_key,
            openai_api_key=openai_api_key,
        )

        if not all_cached_models:
            logger.warning(
                "Model cache returned no models. This might be due to API key issues or errors during cache refresh."
            )
            # Depending on desired behavior, you might raise an error or return empty list
            # For now, return empty list as per current behavior if both providers fail.

        return {"object": "list", "data": all_cached_models}
    except Exception as e:
        logger.exception(f"Error retrieving models from cache: {e}")
        # This is a more general error, perhaps the cache itself failed unexpectedly.
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while retrieving models: {str(e)}",
        )
