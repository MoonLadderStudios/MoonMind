from uuid import uuid4

from api.routers.chat import router as chat_router
from api.routers.models import router as models_router

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from moonmind.config.logging import logger
from moonmind.config.settings import settings
from moonmind.factories.chat_factory import build_chat_provider
from moonmind.factories.embeddings_factory import build_embeddings_provider
from moonmind.factories.vector_store_factory import build_vector_store_provider

logger.info("Starting FastAPI...")

app = FastAPI(
    title="MoonMind",
    version="0.1.0",
    docs_url="/",
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]
)

@app.middleware("http")
async def add_debug_headers(request, call_next):
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
        app.state.chat_provider = build_chat_provider(settings)
        app.state.embeddings_provider = build_embeddings_provider(settings)
        app.state.vector_store_provider = build_vector_store_provider(settings, app.state.embeddings_provider)

        # Setup routers
        app.include_router(chat_router)
        app.include_router(models_router)

    except Exception as e:
        logger.error(f"Failed to initialize providers: {str(e)}")
        raise

@app.on_event("shutdown")
def teardown_providers():
    """
    Optional: If your providers need explicit cleanup, do it here.
    """
    pass

