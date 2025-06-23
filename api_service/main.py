# main.py
import logging
from uuid import uuid4

# Configure logging before any other imports that may use logging.
from moonmind.config.logging import configure_logging

configure_logging()

# Initialize settings immediately after logging configuration and before other imports
# that might depend on settings.
from moonmind.config.config import initialize_settings, get_settings as get_app_settings
# This call will load the settings based on ACTIVE_PROFILE or create a default one.
# The initialized 'settings' object will be available via get_app_settings()
initialize_settings()

logger = logging.getLogger(__name__)

from api_service.api.routers.chat import router as chat_router
from api_service.api.routers.context_protocol import router as context_protocol_router
from api_service.api.routers.documents import router as documents_router
from api_service.api.routers.models import router as models_router
from api_service.api.routers.profiles import router as profiles_router # New profiles router
from llama_index.core import VectorStoreIndex, load_index_from_storage

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
# 'settings' is no longer imported directly from moonmind.config.settings
# It will be accessed via get_app_settings() from moonmind.config.config
from moonmind.factories.embed_model_factory import build_embed_model
# from moonmind.factories.indexers_factory import build_indexers # This import seems unused in the provided snippet
from moonmind.factories.service_context_factory import build_service_context
from moonmind.factories.storage_context_factory import build_storage_context
from moonmind.factories.vector_store_factory import build_vector_store
from moonmind.config.settings import AppSettings # For type hinting if needed, or use get_app_settings()


logger.info("Starting FastAPI...")


# Helper functions for setup
def _initialize_embedding_model(app_state, current_settings: AppSettings):
    """Initializes and sets the embedding model and its dimensions on app_state."""
    logger.info("Initializing embedding model...")
    app_state.embed_model, app_state.embed_dimensions = build_embed_model(current_settings)
    logger.info(f"Embedding model initialized with dimensions: {app_state.embed_dimensions}")

def _initialize_vector_store(app_state, current_settings: AppSettings):
    """Initializes and sets the vector store on app_state."""
    logger.info("Initializing vector store...")
    try:
        app_state.vector_store = build_vector_store(current_settings, app_state.embed_model, app_state.embed_dimensions)
        logger.info("Vector store initialized successfully.")
    except ValueError as e:
        logger.error(f"Failed to build vector store: {e}. This is a critical error.")
        raise

def _initialize_contexts(app_state, current_settings: AppSettings):
    """Initializes and sets storage and service contexts on app_state."""
    logger.info("Initializing storage and service contexts...")
    app_state.storage_context = build_storage_context(current_settings, app_state.vector_store)
    app_state.settings = build_service_context(current_settings, app_state.embed_model) # app.state.settings is used as service_context
    logger.info("Storage and service contexts initialized successfully.")

def _load_or_create_vector_index(app_state):
    """Loads an existing vector index or creates a new one if loading fails."""
    logger.info("Attempting to load VectorStoreIndex from storage_context...")
    try:
        # Ensure service_context (app.state.settings) is available for loading
        if not app_state.settings: # app.state.settings is the service_context
             logger.error("Service context (app.state.settings) not initialized before loading index.")
             raise RuntimeError("Service context not initialized.")

        app_state.vector_index = load_index_from_storage(
            storage_context=app_state.storage_context,
            service_context=app_state.settings, # Pass service_context here
        )
        if not app_state.vector_index.docstore.docs:
            logger.warning("Loaded index appears to be empty (no documents in docstore).")
        else:
            logger.info("Successfully loaded VectorStoreIndex from storage.")
    except ValueError as e_load_idx:
        logger.warning(f"Could not load VectorStoreIndex from storage (it might be new or empty): {e_load_idx}. "
                        "Creating a new empty VectorStoreIndex.")
        if app_state.storage_context and app_state.settings: # app.state.settings is the service_context
            app_state.vector_index = VectorStoreIndex.from_documents(
                [],
                storage_context=app_state.storage_context,
                service_context=app_state.settings # app.state.settings is the service_context
            )
            logger.info("Created a new empty VectorStoreIndex.")
        else:
            logger.error("Cannot create new VectorStoreIndex because storage_context or service_context (app.state.settings) is not available.")
            raise RuntimeError("Failed to initialize critical components (storage_context or service_context) for VectorStoreIndex.")


app = FastAPI(
    title="MoonMind API",
    description="API for MoonMind - LLM-powered documentation search and chat interface",
    version="0.1.0"
)

# Include all routers
app.include_router(chat_router, prefix="/v1/chat")
app.include_router(models_router, prefix="/v1/models")
app.include_router(documents_router, prefix="/v1/documents")
app.include_router(profiles_router, prefix="/v1/profiles", tags=["Profiles"]) # Added profiles router
app.include_router(context_protocol_router) # Removed prefix="/context"

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]
)

@app.middleware("http")
async def add_debug_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Debug-Stream"] = str(getattr(request.state, "stream", False))
    response.headers["X-Debug-ContentType"] = response.headers.get("content-type", "none")
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
    app.state.settings = None # This will hold the service_context, initialized from global AppSettings

    # Get the globally initialized settings
    current_app_settings = get_app_settings()
    if not current_app_settings:
        # This should not happen if initialize_settings() worked correctly.
        logger.critical("Application settings not initialized. Startup cannot proceed.")
        raise RuntimeError("Application settings not initialized.")

    try:
        # Call helper functions in sequence, passing the loaded AppSettings
        _initialize_embedding_model(app.state, current_app_settings)
        _initialize_vector_store(app.state, current_app_settings)
        # _initialize_contexts will set app.state.settings to be the service_context
        _initialize_contexts(app.state, current_app_settings)
        _load_or_create_vector_index(app.state)

        logger.info("Application setup completed successfully.")

    except Exception as e_startup: # Outer catch-all for any critical startup failure
        logger.error(f"A critical error occurred during application startup: {e_startup}", exc_info=True)
        # Re-raise to make startup failures explicit and prevent app from running in a broken state.
        raise

@app.on_event("shutdown")
def teardown_providers():
    """
    Optional: If your providers need explicit cleanup, do it here.
    """
    pass
