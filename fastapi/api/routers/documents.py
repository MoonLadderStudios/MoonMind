import logging
from typing import Dict, List, Optional

from langchain.vectorstores import VectorStore
from pydantic import BaseModel

from fastapi import APIRouter, Depends, HTTPException
from moonmind.config.settings import settings
from moonmind.indexers.confluence_indexer import ConfluenceIndexer
from moonmind.models.documents_models import ConfluenceLoadRequest

from ..dependencies import get_vector_store

router = APIRouter()
logger = logging.getLogger(__name__)

# TODO: There should be a way to load specific documents or load a space

@router.post("/documents/confluence/load")
async def load_confluence_documents(
    request: ConfluenceLoadRequest,
    vector_store: VectorStore = Depends(get_vector_store)
):
    """Load documents from Confluence workspace"""
    try:
        confluence_indexer = ConfluenceIndexer(
            url=settings.confluence.confluence_url,
            api_key=settings.confluence.confluence_api_key,
            username=settings.confluence.confluence_username,
            space_key=request.space_key,
            include_attachments=request.include_attachments,
            limit=request.limit,
            logger=logger
        )

        ids = confluence_indexer.index_space(vector_store=vector_store)

        return {
            "status": "success",
            "message": f"Loaded {len(ids)} documents from Confluence workspace {request.space_key}"
        }
    except Exception as e:
        logger.exception(f"Error loading Confluence documents: {e}")
        raise HTTPException(status_code=500, detail=str(e))
