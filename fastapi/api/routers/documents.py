import logging
from typing import Dict, List, Optional

from llama_index.core.contexts import ServiceContext, StorageContext
from pydantic import BaseModel

from fastapi import APIRouter, Depends, HTTPException
from moonmind.config.settings import settings
from moonmind.indexers.confluence_indexer import ConfluenceIndexer
from moonmind.indexers.github_indexer import GitHubIndexer # Added import
from moonmind.models.documents_models import ConfluenceLoadRequest, GitHubLoadRequest # Added import

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


@router.post("/documents/github/load")
async def load_github_repo(
    request: GitHubLoadRequest,
    storage_context: StorageContext = Depends(get_storage_context),
    service_context: ServiceContext = Depends(get_service_context)
):
    """Load documents from a GitHub repository."""
    try:
        github_indexer = GitHubIndexer(
            github_token=request.github_token,
            logger=logger 
        )

        # GitHubIndexer.index is synchronous
        index_result = github_indexer.index(
            repo_full_name=request.repo,
            branch=request.branch,
            filter_extensions=request.filter_extensions,
            storage_context=storage_context,
            service_context=service_context
        )
        
        total_nodes_indexed = index_result["total_nodes_indexed"]

        return {
            "status": "success",
            "message": f"Successfully loaded {total_nodes_indexed} nodes from GitHub repository {request.repo} on branch {request.branch}",
            "total_nodes_indexed": total_nodes_indexed,
            "repository": request.repo,
            "branch": request.branch
        }
    except ValueError as ve:
        logger.error(f"Validation error for GitHub loading for repo {request.repo}: {ve}")
        raise HTTPException(status_code=400, detail=str(ve))
    except HTTPException as he: # Re-raise HTTPExceptions raised by the indexer
        logger.error(f"HTTP error during GitHub loading for repo {request.repo}: {he.detail}")
        raise he
    except Exception as e:
        logger.exception(f"Error loading GitHub repository {request.repo}: {e}")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred while processing {request.repo}: {str(e)}")
