from llama_index import ServiceContext

from ..config.settings import AppSettings


def build_service_context(settings: AppSettings, embed_model):
    return ServiceContext.from_defaults(embedding=embed_model)