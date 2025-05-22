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