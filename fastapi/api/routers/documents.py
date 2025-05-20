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
            user_name=settings.confluence.confluence_username, # Corrected parameter name
            api_token=settings.confluence.confluence_api_key, # Corrected parameter name
            logger=logger
        )

        # Pass page_ids and max_num_results from the request
        index_result = confluence_indexer.index(
            space_key=request.space_key,
            storage_context=storage_context,
            service_context=service_context,
            page_ids=request.page_ids,
            confluence_fetch_batch_size=request.max_num_results
        )

        total_nodes_indexed = index_result["total_nodes_indexed"]
        # The index object itself can be accessed via index_result["index"] if needed later

        message = ""
        if request.page_ids:
            message = f"Successfully loaded {total_nodes_indexed} nodes from {len(request.page_ids)} specified page IDs."
        else:
            message = f"Successfully loaded {total_nodes_indexed} nodes from Confluence space {request.space_key}."

        return {
            "status": "success",
            "message": message,
            "total_nodes_indexed": total_nodes_indexed
        }
    except Exception as e:
        logger.exception(f"Error loading Confluence documents: {e}")
        raise HTTPException(status_code=500, detail=str(e))
