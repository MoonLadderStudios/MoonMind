from fastapi import Request


def get_chat_provider(request: Request):
    return request.app.state.chat_provider

def get_embeddings_provider(request: Request):
    return request.app.state.embeddings_provider

def get_vector_store(request: Request):
    return request.app.state.vector_store