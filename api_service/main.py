# main.py
# Configure logging at the very beginning
import logging

from moonmind.config.logging import configure_logging

configure_logging()
logger = logging.getLogger(__name__) # Get logger after configuration

import os  # For path operations
# Now proceed with other imports
from uuid import uuid4

import requests
from fastapi import APIRouter  # Added for healthz
from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from llama_index.core import VectorStoreIndex, load_index_from_storage

from api_service.api.routers import \
    summarization as \
    summarization_router  # Added import for summarization router
from api_service.api.routers.chat import router as chat_router
from api_service.api.routers.context_protocol import \
    router as context_protocol_router
from api_service.api.routers.documents import router as documents_router
from api_service.api.routers.models import router as models_router
from api_service.api.routers.profile import router as profile_router
from api_service.api.schemas import UserProfileUpdate
# Auth imports
from api_service.auth import (UserCreate, UserRead, UserUpdate, auth_backend,
                              fastapi_users)
from api_service.db.models import \
    User  # Ensure User model is imported if needed for routers
from moonmind.config.settings import settings
from moonmind.factories.embed_model_factory import build_embed_model
# Removed unused import: build_indexers
from moonmind.factories.service_context_factory import build_service_context
from moonmind.factories.storage_context_factory import build_storage_context
from moonmind.factories.vector_store_factory import build_vector_store

logger.info("Starting FastAPI...")


# Helper functions for setup
def _initialize_embedding_model(app_state, app_settings):
    """Initializes and sets the embedding model and its dimensions on app_state."""
    logger.info("Initializing embedding model...")
    app_state.embed_model, configured_dims = build_embed_model(app_settings)
    actual_dims = configured_dims
    try:
        # Probe the embedding model to determine the real output dimensionality
        test_vec = app_state.embed_model.embed_query("dim_check")
        actual_dims = len(test_vec)
        if configured_dims != -1 and configured_dims != actual_dims:
            logger.warning(
                "Configured embedding dimension %s does not match the model's "
                "actual dimension %s. Using the detected dimension instead.",
                configured_dims,
                actual_dims,
            )
    except Exception as e:  # pragma: no cover - best effort check
        logger.error("Failed to detect embedding dimensions: %s", e)
        # Fallback to configured dimensions if probing fails

    if actual_dims <= 0:
        if configured_dims > 0:
            actual_dims = configured_dims
            logger.warning(
                "Using configured embedding dimension %s due to detection failure",
                configured_dims,
            )
        else:
            raise RuntimeError(
                "Embedding dimension could not be determined. Configure a valid value."
            )

    app_state.embed_dimensions = actual_dims
    logger.info(
        "Embedding model initialized with dimensions: %s", app_state.embed_dimensions
    )


def _initialize_vector_store(app_state, app_settings):
    """Initializes and sets the vector store on app_state."""
    logger.info("Initializing vector store...")
    try:
        app_state.vector_store = build_vector_store(
            app_settings, app_state.embed_model, app_state.embed_dimensions
        )
        logger.info("Vector store initialized successfully.")
    except ValueError as e:
        logger.error(f"Failed to build vector store: {e}. This is a critical error.")
        raise


def _initialize_contexts(app_state, app_settings):
    """Initializes and sets storage and service contexts on app_state."""
    logger.info("Initializing storage and service contexts...")
    app_state.storage_context = build_storage_context(
        app_settings, app_state.vector_store
    )
    app_state.settings = build_service_context(
        app_settings, app_state.embed_model
    )  # settings is used as service_context
    logger.info("Storage and service contexts initialized successfully.")


def _initialize_oidc_provider(app: FastAPI):
    """Initializes the OIDC provider by fetching discovery documents if needed."""
    provider = settings.oidc.AUTH_PROVIDER
    if provider == "google":
        logger.info("Initializing Google OIDC provider...")
        try:
            discovery_url = f"{settings.oidc.OIDC_ISSUER_URL}/.well-known/openid-configuration"
            response = requests.get(discovery_url)
            response.raise_for_status()
            discovery_doc = response.json()
            jwks_uri = discovery_doc.get("jwks_uri")
            if not jwks_uri:
                logger.error("JWKS URI not found in Google OIDC discovery document.")
                raise RuntimeError("JWKS URI not found in Google OIDC discovery document.")
            app.state.jwks_uri = jwks_uri
            logger.info("Successfully fetched and stored Google JWKS URI.")
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch Google OIDC discovery document: {e}")
            raise RuntimeError(f"Failed to fetch Google OIDC discovery document: {e}")
        except Exception as e:
            logger.error(f"Error processing Google OIDC discovery document: {e}")
            raise RuntimeError(f"Error processing Google OIDC discovery document: {e}")


def _load_or_create_vector_index(app_state):
    """Loads an existing vector index or creates a new one if loading fails."""
    logger.info("Attempting to load VectorStoreIndex from storage_context...")
    try:
        app_state.vector_index = load_index_from_storage(
            storage_context=app_state.storage_context,
        )
        if not app_state.vector_index.docstore.docs:
            logger.warning(
                "Loaded index appears to be empty (no documents in docstore)."
            )
        else:
            logger.info("Successfully loaded VectorStoreIndex from storage.")
    except ValueError as e_load_idx:
        logger.warning(
            f"Could not load VectorStoreIndex from storage (it might be new or empty): {e_load_idx}. "
            "Creating a new empty VectorStoreIndex."
        )
        if app_state.storage_context and app_state.settings:
            app_state.vector_index = VectorStoreIndex.from_documents(
                [],
                storage_context=app_state.storage_context,
                service_context=app_state.settings,
            )
            logger.info("Created a new empty VectorStoreIndex.")
        else:
            logger.error(
                "Cannot create new VectorStoreIndex because storage_context or service_context is not available."
            )
            raise RuntimeError(
                "Failed to initialize critical components (storage_context or service_context) for VectorStoreIndex."
            )


app = FastAPI(
    title="MoonMind API",
    description="API for MoonMind - LLM-powered documentation search and chat interface",
    version="0.1.0",
    docs_url="/docs",
    openapi_url="/openapi.json",
)

# Setup templates
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")

templates = Jinja2Templates(directory=TEMPLATES_DIR)

# Mount static files directory (optional, if you have CSS/JS files)
# Create the static directory if it doesn't exist, or ensure your deployment process does.
if not os.path.exists(STATIC_DIR):
    os.makedirs(STATIC_DIR)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# Healthz router
health_router = APIRouter()


@health_router.get("/healthz")
async def health_check():
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
async def docs_redirect() -> RedirectResponse:
    """Redirect root path to Swagger UI."""
    return RedirectResponse(url=app.docs_url)


app.include_router(health_router, tags=["health"])

# Include all routers
app.include_router(chat_router, prefix="/v1/chat", tags=["Chat"])
app.include_router(models_router, prefix="/v1/models", tags=["Models"])
app.include_router(documents_router, prefix="/v1/documents", tags=["Documents"])
app.include_router(
    summarization_router.router,
    prefix="/summarization",
    tags=["Summarization"],
)  # Added summarization router
app.include_router(context_protocol_router, tags=["Context Protocol"])  # Removed prefix="/context"
app.include_router(profile_router, prefix="", tags=["Profile"])  # Include profile router

# Auth routers
API_AUTH_PREFIX = "/api/v1/auth"  # Defined a constant for clarity

if settings.oidc.AUTH_PROVIDER != "keycloak":
    logger.info(
        f"AUTH_PROVIDER is '{settings.oidc.AUTH_PROVIDER}'. Including fastapi-users auth routers."
    )
    app.include_router(
        fastapi_users.get_auth_router(auth_backend),
        prefix=API_AUTH_PREFIX,
        tags=["auth"],
    )
    app.include_router(
        fastapi_users.get_register_router(UserRead, UserCreate),
        prefix=API_AUTH_PREFIX,
        tags=["auth"],
    )
    app.include_router(
        fastapi_users.get_reset_password_router(),
        prefix=API_AUTH_PREFIX,
        tags=["auth"],
    )
    app.include_router(
        fastapi_users.get_verify_router(UserRead),
        prefix=API_AUTH_PREFIX,
        tags=["auth"],
    )
    app.include_router(
        fastapi_users.get_users_router(UserRead, UserUpdate),
        prefix=f"{API_AUTH_PREFIX}/users",
        tags=["users"],
    )
else:
    logger.info(
        "AUTH_PROVIDER is 'keycloak'. Skipping fastapi-users auth routers."
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)


@app.middleware("http")
async def add_debug_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Debug-Stream"] = str(getattr(request.state, "stream", False))
    response.headers["X-Debug-ContentType"] = response.headers.get(
        "content-type", "none"
    )
    return response


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    request_id = str(uuid4())
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.on_event("startup")
async def startup_event():
    """Defines the application's startup events."""
    logger.info("Executing application startup events...")
    # Initialize state object if it doesn't exist
    if not hasattr(app, "state"):
        # In modern FastAPI, `app.state` exists by default, but this is a safeguard.
        from starlette.datastructures import State
        app.state = State()

    _initialize_embedding_model(app.state, settings)
    _initialize_vector_store(app.state, settings)
    _initialize_contexts(app.state, settings)
    _load_or_create_vector_index(app.state)
    _initialize_oidc_provider(app) # OIDC provider init like Keycloak discovery

    # Ensure default user and profile exist if auth is disabled
    if settings.oidc.AUTH_PROVIDER == "disabled":
        logger.info("Auth provider is 'disabled'. Ensuring default user and profile exist on startup.")
        from api_service.auth import (get_or_create_default_user,
                                      get_user_manager_context)
        from api_service.db.base import get_async_session_context
        from api_service.services.profile_service import ProfileService

        async with get_async_session_context() as db_session:
            async with get_user_manager_context(db_session) as user_manager:
                try:
                    if not settings.oidc.DEFAULT_USER_ID or not settings.oidc.DEFAULT_USER_EMAIL:
                        logger.warning("DEFAULT_USER_ID or DEFAULT_USER_EMAIL not set. Skipping default user/profile creation on startup.")
                    else:
                        logger.info(
                            f"Attempting to get/create default user ID: {settings.oidc.DEFAULT_USER_ID} on startup."
                        )
                        default_user = await get_or_create_default_user(
                            db_session=db_session, user_manager=user_manager
                        )
                        if default_user:
                            logger.info(
                                f"Default user {default_user.email} (ID: {default_user.id}) ensured."
                            )
                            profile_service = ProfileService()
                            existing_profile = await profile_service.get_profile_by_user_id(
                                db_session=db_session, user_id=default_user.id
                            )
                            if existing_profile:
                                logger.info(
                                    f"Profile for default user {default_user.email} already exists (Profile ID: {existing_profile.id})."
                                )
                            else:
                                profile_update = UserProfileUpdate(
                                    google_api_key=settings.google.google_api_key,
                                    openai_api_key=settings.openai.openai_api_key,
                                )
                                profile = await profile_service.update_profile(
                                    db_session=db_session,
                                    user_id=default_user.id,
                                    profile_data=profile_update,
                                )
                                logger.info(
                                    f"Created profile for default user {default_user.email} (Profile ID: {profile.id}) from env keys."
                                )
                            from moonmind.models_cache import refresh_model_cache_for_user
                            import asyncio
                            loop = asyncio.get_running_loop()
                            await loop.run_in_executor(None, refresh_model_cache_for_user, default_user, db_session)
                        else:
                            logger.error("Failed to get or create default user on startup.")
                except ValueError as ve:
                    logger.error(
                        f"Configuration error during default user setup on startup: {ve}"
                    )
                except Exception as e:
                    logger.error(
                        f"Error ensuring default user/profile on startup: {e}",
                        exc_info=True,
                    )
    else:
        logger.info(f"Auth provider is '{settings.oidc.AUTH_PROVIDER}'. Skipping default user creation on startup.")


    logger.info("Application startup events completed.")


@app.on_event("shutdown")
def teardown_providers():
    """
    Optional: If your providers need explicit cleanup, do it here.
    """
    pass
