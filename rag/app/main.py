import asyncio
import json
import logging
import os
import time
from logging.config import dictConfig
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from routers.chat import router as chat_router
from routers.common import init_router
from routers.index import router as index_router
from routers.models import router as models_router
from routers.multimodal import router as multimodal_router

from moonai.config.logging import logger
from moonai.config.settings import settings
from moonai.connectors.qdrant_connector import QdrantConnector

logger.info("Starting FastAPI...")

app = FastAPI(
    title="FastAPI",
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

qdrantClient = QdrantConnector(
    logger=logger,
    qdrant_host=settings.QDRANT_HOST,
    qdrant_port=settings.QDRANT_PORT,
    collection_name_prefix='kobi'
)

# Initialize multimodal router with qdrant instance
init_router(qdrantClient)

# Include all routers in app
app.include_router(chat_router)
app.include_router(index_router)
app.include_router(models_router)
app.include_router(multimodal_router)
