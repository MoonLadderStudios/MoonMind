import logging

from fastapi import APIRouter, Depends, HTTPException
from llama_index.core import Settings, StorageContext

from api_service.api.dependencies import get_service_context, get_storage_context
from api_service.auth_providers import get_current_user  # Auth dependency
from api_service.db.models import User  # User model for type hinting
from moonmind.config.settings import settings
from moonmind.indexers.confluence_indexer import ConfluenceIndexer
from moonmind.indexers.github_indexer import GitHubIndexer
from moonmind.indexers.google_drive_indexer import GoogleDriveIndexer  # Added import
from moonmind.schemas.documents_models import (  # Updated import path with GoogleDriveLoadRequest
    ConfluenceLoadRequest,
    GitHubLoadRequest,
    GoogleDriveLoadRequest,
)

router = APIRouter()
logger = logging.getLogger(__name__)

# TODO: There should be a way to load specific documents or load a space


@router.post("/confluence/load")  # Path relative to the /v1/documents prefix
async def load_confluence_documents(
    request: ConfluenceLoadRequest,
    storage_context: StorageContext = Depends(get_storage_context),
    service_context: Settings = Depends(get_service_context),
    _user: User = Depends(get_current_user()),  # Protected
):
    if not settings.confluence.confluence_enabled:
        raise HTTPException(status_code=500, detail="Confluence is not enabled")

    """Load documents from Confluence workspace"""
    try:
        confluence_indexer = ConfluenceIndexer(
            base_url=settings.atlassian.atlassian_url,
            user_name=settings.atlassian.atlassian_username,
            api_token=settings.atlassian.atlassian_api_key,  # Corrected to use global Atlassian API key
            logger=logger,
        )

        # Pass parameters from the request to the indexer
        index_result = confluence_indexer.index(
            storage_context=storage_context,
            service_context=service_context,
            space_key=request.space_key,
            page_id=request.page_id,
            page_title=request.page_title,
            cql_query=request.cql_query,
            max_pages_to_fetch=request.max_pages_to_fetch,
        )

        total_nodes_indexed = index_result["total_nodes_indexed"]
        # The index object itself can be accessed via index_result["index"] if needed later

        message_parts = ["Successfully loaded {total_nodes_indexed} nodes"]
        source_description_parts = []

        if request.page_id:
            source_description_parts.append(f"page ID '{request.page_id}'")
        elif request.space_key and request.page_title:
            source_description_parts.append(
                f"page title '{request.page_title}' in space '{request.space_key}'"
            )
        elif request.cql_query:
            source_description_parts.append(
                f"Confluence using CQL query: '{request.cql_query[:50]}{'...' if len(request.cql_query) > 50 else ''}'"
            )
        elif request.space_key:
            source_description_parts.append(f"Confluence space '{request.space_key}'")

        if source_description_parts:
            message_parts.append("from")
            message_parts.append(", ".join(source_description_parts))
        else:
            # This case should ideally be caught by Pydantic validation
            message_parts.append("from Confluence (undefined source)")

        message = " ".join(message_parts) + "."

        # Replace placeholder with actual count
        message = message.format(total_nodes_indexed=total_nodes_indexed)

        return {
            "status": "success",
            "message": message,
            "total_nodes_indexed": total_nodes_indexed,
        }
    except Exception as e:
        logger.exception(f"Error loading Confluence documents: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/documents/github/load")
async def load_github_repo(
    request: GitHubLoadRequest,
    storage_context: StorageContext = Depends(get_storage_context),
    service_context: Settings = Depends(get_service_context),
    _user: User = Depends(get_current_user()),  # Protected
):
    """Load documents from a GitHub repository."""
    try:
        github_indexer = GitHubIndexer(github_token=request.github_token, logger=logger)

        # GitHubIndexer.index is synchronous
        index_result = github_indexer.index(
            repo_full_name=request.repo,
            branch=request.branch,
            filter_extensions=request.filter_extensions,
            storage_context=storage_context,
            service_context=service_context,
        )

        total_nodes_indexed = index_result["total_nodes_indexed"]

        return {
            "status": "success",
            "message": f"Successfully loaded {total_nodes_indexed} nodes from GitHub repository {request.repo} on branch {request.branch}",
            "total_nodes_indexed": total_nodes_indexed,
            "repository": request.repo,
            "branch": request.branch,
        }
    except ValueError as ve:
        logger.error(
            f"Validation error for GitHub loading for repo {request.repo}: {ve}"
        )
        raise HTTPException(status_code=400, detail=str(ve))
    except HTTPException as he:  # Re-raise HTTPExceptions raised by the indexer
        logger.error(
            f"HTTP error during GitHub loading for repo {request.repo}: {he.detail}"
        )
        raise he
    except Exception as e:
        logger.exception(f"Error loading GitHub repository {request.repo}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"An unexpected error occurred while processing {request.repo}: {str(e)}",
        )


@router.post("/documents/google_drive/load")
async def load_google_drive_documents(
    request: GoogleDriveLoadRequest,
    storage_context: StorageContext = Depends(get_storage_context),
    service_context: Settings = Depends(get_service_context),
    _user: User = Depends(get_current_user()),  # Protected
):
    """Load documents from Google Drive."""
    try:
        sa_key_path = request.service_account_key_path
        if (
            not sa_key_path
            and hasattr(settings, "google")
            and hasattr(settings.google, "google_account_file")
        ):  # Ensure 'google_account_file' is used
            sa_key_path = settings.google.google_account_file

        if sa_key_path:  # Add a log if a path is found/used
            logger.info(f"Using Google service account key path: {sa_key_path}")
        else:
            logger.info(
                "No service account key path provided directly or in settings; GoogleDriveIndexer will attempt ADC."
            )

        google_drive_indexer = GoogleDriveIndexer(
            service_account_key_path=sa_key_path, logger=logger
        )

        # GoogleDriveIndexer.index is synchronous
        index_result = google_drive_indexer.index(
            storage_context=storage_context,
            service_context=service_context,
            folder_id=request.folder_id,
            file_ids=request.file_ids,
            # 'recursive' from request is noted in Pydantic model, but not directly used by the current indexer logic
        )

        total_nodes_indexed = index_result["total_nodes_indexed"]

        source_description = ""
        if request.file_ids:
            source_description = f"file IDs {request.file_ids}"
        elif request.folder_id:
            source_description = f"folder ID {request.folder_id}"
        else:
            # This case should ideally be caught by the indexer's validation,
            # but as a fallback for the message:
            source_description = "the specified Google Drive location"

        return {
            "status": "success",
            "message": f"Successfully loaded {total_nodes_indexed} nodes from Google Drive ({source_description}).",
            "total_nodes_indexed": total_nodes_indexed,
            "folder_id": request.folder_id,
            "file_ids": request.file_ids,
        }
    except ValueError as ve:
        logger.error(
            f"Validation error for Google Drive loading (folder: {request.folder_id}, files: {request.file_ids}): {ve}"
        )
        raise HTTPException(status_code=400, detail=str(ve))
    except HTTPException as he:
        logger.error(
            f"HTTP error during Google Drive loading (folder: {request.folder_id}, files: {request.file_ids}): {he.detail}"
        )
        raise he  # Re-raise if it's already an HTTPException (e.g. from indexer)
    except Exception as e:
        logger.exception(
            f"Error loading from Google Drive (folder: {request.folder_id}, files: {request.file_ids}): {e}"
        )
        raise HTTPException(
            status_code=500,
            detail=f"An unexpected error occurred while loading from Google Drive: {str(e)}",
        )
