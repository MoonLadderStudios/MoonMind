from typing import Dict, List, Optional

from pydantic import BaseModel

from fastapi import APIRouter, Depends, HTTPException
from moonmind.config.logging import logger
from moonmind.models.documents_models import ConfluenceLoadRequest

from ..dependencies import get_indexers

router = APIRouter()

# TODO: There should be a way to load specific documents or load a space

@router.post("/v1/documents/confluence/load")
async def load_confluence_documents(
    request: ConfluenceLoadRequest,
    indexers: Dict = Depends(get_indexers)
):
    """Load documents from Confluence workspace"""
    try:
        if "confluence" not in indexers:
            raise HTTPException(
                status_code=400,
                detail="Confluence loader is not configured"
            )

        confluence_indexer = indexers["confluence"]
        ids = confluence_indexer.index_space(
            space_key=request.space_key,
            include_attachments=request.include_attachments,
            limit=request.limit
        )

        return {
            "status": "success",
            "message": f"Loaded {len(ids)} documents from Confluence workspace {request.space_key}"
        }
    except Exception as e:
        logger.error(f"Error loading Confluence documents: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
