import logging
from typing import Dict, List, Optional

from llama_index.core.contexts import ServiceContext, StorageContext
from pydantic import BaseModel

from fastapi import APIRouter, Depends, HTTPException
from moonmind.config.settings import settings
from moonmind.indexers.confluence_indexer import ConfluenceIndexer
from moonmind.models.documents_models import ConfluenceLoadRequest

from ..dependencies import get_service_context, get_storage_context

router = APIRouter()
logger = logging.getLogger(__name__)

# TODO: There should be a way to load specific documents or load a space

@router.post("/documents/confluence/load")
async def load_confluence_documents(
    request: ConfluenceLoadRequest,
    storage_context: StorageContext = Depends(get_storage_context),
    service_context: ServiceContext = Depends(get_service_context)
):
    if not settings.confluence.confluence_enabled:
        raise HTTPException(status_code=500, detail="Confluence is not enabled")

    """Load documents from Confluence workspace"""
    try:
        confluence_indexer = ConfluenceIndexer(
            base_url=settings.confluence.confluence_url,
            username=settings.confluence.confluence_username,
            api_key=settings.confluence.confluence_api_key,
            logger=logger
        )

        # TODO: max_num_results should be a parameter
        index = confluence_indexer.index(space_key=request.space_key, storage_context=storage_context, service_context=service_context)

        return {
            "status": "success",
            "message": f"Loaded {len(ids)} documents from Confluence workspace {request.space_key}"
        }
    except Exception as e:
        logger.exception(f"Error loading Confluence documents: {e}")
        raise HTTPException(status_code=500, detail=str(e))
