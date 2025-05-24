import logging
from typing import Optional

from llama_index.core import (Settings, StorageContext, VectorStoreIndex,
                              load_index_from_storage)

from fastapi import Request


def get_chat_provider(request: Request):
    return request.app.state.chat_provider

def get_embed_model(request: Request):
    return request.app.state.embed_model

def get_embed_dimensions(request: Request):
    return request.app.state.embed_dimensions

def get_vector_store(request: Request):
    return request.app.state.vector_store

def get_storage_context(request: Request):
    return request.app.state.storage_context

def get_service_context(request: Request):
    # Return the global Settings object instead of ServiceContext
    return request.app.state.settings

def get_vector_index(request: Request) -> Optional[VectorStoreIndex]:
    logger_dep = logging.getLogger(__name__)
    storage_context: Optional[StorageContext] = getattr(request.app.state, 'storage_context', None)
    service_settings: Optional[Settings] = getattr(request.app.state, 'settings', None)

    if not storage_context or not service_settings:
        logger_dep.error("StorageContext or LlamaIndex Settings not available in app state. Cannot provide VectorStoreIndex.")
        return None

    try:
        # Always try to load from storage to get the freshest index
        index = load_index_from_storage(storage_context=storage_context)
        logger_dep.debug("Successfully reloaded VectorStoreIndex from storage in dependency.")
        # A basic check if the loaded index has content
        if not index.docstore.docs:
             logger_dep.warning("Dependency: Reloaded index appears to be empty.")
        return index
    except ValueError:
        logger_dep.warning("Could not load VectorStoreIndex from storage in dependency (may be empty/new). "
                           "Returning the index from app.state (which might be an empty initialized one).")
        # Fallback to the one initialized at startup (which could be an empty one)
        return getattr(request.app.state, 'vector_index', None)
    except Exception as e:
        logger_dep.exception(f"Unexpected error loading VectorStoreIndex in dependency: {e}")
        return None