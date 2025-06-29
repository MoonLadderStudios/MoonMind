# main.py
# Configure logging at the very beginning
import logging
from moonmind.config.logging import configure_logging
configure_logging()
logger = logging.getLogger(__name__) # Get logger after configuration

# Now proceed with other imports
from uuid import uuid4

from api_service.api.routers.chat import router as chat_router
from api_service.api.routers.context_protocol import router as context_protocol_router
from api_service.api.routers.documents import router as documents_router
from api_service.api.routers.models import router as models_router
from api_service.api.routers.profile import router as profile_router
from api_service.api.routers import summarization as summarization_router # Added import for summarization router
from llama_index.core import VectorStoreIndex, load_index_from_storage

from fastapi import FastAPI, Request, Depends
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import os # For path operations

# Auth imports
from api_service.auth import auth_backend, fastapi_users, UserRead, UserCreate, UserUpdate
from api_service.db.models import User # Ensure User model is imported if needed for routers
from fastapi.middleware.cors import CORSMiddleware
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
    app_state.embed_model, app_state.embed_dimensions = build_embed_model(app_settings)
    logger.info(
        f"Embedding model initialized with dimensions: {app_state.embed_dimensions}"
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


# Include all routers
app.include_router(chat_router, prefix="/v1/chat")
app.include_router(models_router, prefix="/v1/models")
app.include_router(documents_router, prefix="/v1/documents")
app.include_router(summarization_router.router, prefix="/summarization", tags=["Summarization"]) # Added summarization router
app.include_router(context_protocol_router)  # Removed prefix="/context"
app.include_router(
    profile_router, prefix="/api/v1/profile", tags=["profile"]
)  # Include profile router

# Auth routers
API_AUTH_PREFIX = "/api/v1/auth"  # Defined a constant for clarity
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
    fastapi_users.get_reset_password_router(),  # Added reset password router
    prefix=API_AUTH_PREFIX,
    tags=["auth"],
)
app.include_router(
    fastapi_users.get_verify_router(UserRead),  # Added verify router
    prefix=API_AUTH_PREFIX,
    tags=["auth"],
)
app.include_router(
    fastapi_users.get_users_router(UserRead, UserUpdate),
    prefix=f"{API_AUTH_PREFIX}/users",  # Users router typically prefixed further
    tags=["users"],
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
async def setup():
    # Initialize state attributes to None
    app.state.embed_model = None
    app.state.embed_dimensions = None
    app.state.vector_store = None
    app.state.storage_context = None
    app.state.vector_index = None
    app.state.settings = None  # This is used as service_context

    try:
        # Call helper functions in sequence
        _initialize_embedding_model(app.state, settings)
        _initialize_vector_store(app.state, settings)
        _initialize_contexts(app.state, settings)
        _load_or_create_vector_index(app.state)

        logger.info("Application setup completed successfully.")

    except Exception as e_startup:  # Outer catch-all for any critical startup failure
        logger.error(
            f"A critical error occurred during application startup: {e_startup}",
            exc_info=True,
        )
        # Re-raise to make startup failures explicit and prevent app from running in a broken state.
        raise


@app.on_event("shutdown")
def teardown_providers():
    """
    Optional: If your providers need explicit cleanup, do it here.
    """
    pass
