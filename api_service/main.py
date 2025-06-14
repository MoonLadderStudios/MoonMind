# main.py
import logging
from uuid import uuid4

# Configure logging before any other imports that may use logging.
from moonmind.config.logging import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

from api_service.api.routers.chat import router as chat_router
from api_service.api.routers.context_protocol import router as context_protocol_router
from api_service.api.routers.documents import router as documents_router
from api_service.api.routers.models import router as models_router
from llama_index.core import VectorStoreIndex, load_index_from_storage

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from moonmind.config.settings import settings
from moonmind.factories.embed_model_factory import build_embed_model
from moonmind.factories.indexers_factory import build_indexers
from moonmind.factories.service_context_factory import build_service_context
from moonmind.factories.storage_context_factory import build_storage_context
from moonmind.factories.vector_store_factory import build_vector_store

logger.info("Starting FastAPI...")

app = FastAPI(
    title="MoonMind API",
    description="API for MoonMind - LLM-powered documentation search and chat interface",
    version="0.1.0"
)

# Include all routers
app.include_router(chat_router, prefix="/v1/chat")
app.include_router(models_router, prefix="/v1/models")
app.include_router(documents_router, prefix="/v1/documents")
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
    app.state.storage_context = None # Initialize to None
    app.state.vector_store = None    # Initialize to None
    app.state.vector_index = None    # Initialize to None
    app.state.settings = None        # Initialize to None (for service_context)

    try:
        app.state.embed_model, app.state.embed_dimensions = build_embed_model(settings)

        # Attempt to build vector_store
        try:
            app.state.vector_store = build_vector_store(settings, app.state.embed_model, app.state.embed_dimensions)
        except ValueError as e_vs: # Catch specific error from build_vector_store (e.g., dimension mismatch)
            logger.error(f"Failed to build vector store: {e_vs}. This is a critical error.")
            raise # Re-raise to be caught by the outer Exception handler, preventing app startup

        # Proceed only if vector_store is successfully built
        app.state.storage_context = build_storage_context(settings, app.state.vector_store)
        app.state.settings = build_service_context(settings, app.state.embed_model) # Assuming this is service_context

        logger.info("Attempting to load VectorStoreIndex from storage_context...")
        try:
            app.state.vector_index = load_index_from_storage(
                storage_context=app.state.storage_context,
            )
            if not app.state.vector_index.docstore.docs:
                logger.warning("Loaded index appears to be empty (no documents in docstore).")
            else:
                logger.info("Successfully loaded VectorStoreIndex from storage.")
        except ValueError as e_load_idx: # Catch error if index is new/empty or other load issues
            logger.warning(f"Could not load VectorStoreIndex from storage (it might be new or empty): {e_load_idx}. "
                            "Creating a new empty VectorStoreIndex.")
            # Ensure storage_context and service_context (app.state.settings) are valid before this
            if app.state.storage_context and app.state.settings:
                app.state.vector_index = VectorStoreIndex.from_documents(
                    [],
                    storage_context=app.state.storage_context,
                    service_context=app.state.settings
                )
                logger.info("Created a new empty VectorStoreIndex.")
            else:
                logger.error("Cannot create new VectorStoreIndex because storage_context or service_context is not available.")
                # This case should ideally be prevented by earlier checks/raises if vector_store failed.
                raise RuntimeError("Failed to initialize critical components (storage_context or service_context) for VectorStoreIndex.")

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
