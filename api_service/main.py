# main.py
import logging
from uuid import uuid4

# Configure logging before any other imports that may use logging.
from moonmind.config.logging import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

from api.routers.chat import router as chat_router
from api.routers.context_protocol import router as context_protocol_router
from api.routers.documents import router as documents_router
from api.routers.models import router as models_router
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
app.include_router(documents_router, prefix="/documents")
app.include_router(chat_router, prefix="/chat")
app.include_router(models_router, prefix="/models")

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
    try:
        # Setup providers
        app.state.embed_model, app.state.embed_dimensions = build_embed_model(settings)
        app.state.vector_store = build_vector_store(settings, app.state.embed_model, app.state.embed_dimensions)
        app.state.storage_context = build_storage_context(settings, app.state.vector_store)
        # Configure global Settings instead of using ServiceContext
        app.state.settings = build_service_context(settings, app.state.embed_model)

        # Initialize or load the VectorStoreIndex
        try:
            logger.info("Attempting to load VectorStoreIndex from storage_context...")
            # load_index_from_storage uses the global LlamaIndex Settings (Settings.embed_model)
            app.state.vector_index = load_index_from_storage(
                storage_context=app.state.storage_context,
            )
            # Check if index is empty after loading.
            if not app.state.vector_index.docstore.docs:
                logger.warning("Loaded index appears to be empty (no documents in docstore).")
            else:
                logger.info("Successfully loaded VectorStoreIndex from storage.")
        except ValueError as e:
            logger.warning(f"Could not load VectorStoreIndex from storage (it might be new or empty): {e}. "
                          "Creating a new empty VectorStoreIndex.")
            app.state.vector_index = VectorStoreIndex.from_documents(
                [], # Empty list of documents
                storage_context=app.state.storage_context,
                service_context=app.state.settings # Pass LlamaIndex Settings
            )
            logger.info("Created a new empty VectorStoreIndex.")

        # Setup routers
        app.include_router(chat_router)
        app.include_router(documents_router)
        app.include_router(models_router)
        app.include_router(context_protocol_router)

    except Exception as e:
        logger.error(f"Failed to initialize providers or VectorStoreIndex: {str(e)}")
        raise

@app.on_event("shutdown")
def teardown_providers():
    """
    Optional: If your providers need explicit cleanup, do it here.
    """
    pass
