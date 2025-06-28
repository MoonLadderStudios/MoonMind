from llama_index.core import StorageContext

from ..config.settings import AppSettings


def build_storage_context(settings: AppSettings, vector_store):
    return StorageContext.from_defaults(vector_store=vector_store)
