from fastapi import APIRouter
from moonai.config.logging import logger

# Shared qdrant instance
_qdrant = None

def init_router(qdrant_instance):
    global _qdrant
    _qdrant = qdrant_instance

def get_qdrant():
    return _qdrant

# Extract text content from NodeWithScore objects
def extract_node_content(result):
    if hasattr(result, 'node'):
        return result.node.text
    elif isinstance(result, (list, tuple)):
        return [extract_node_content(item) for item in result]
    elif isinstance(result, dict):
        return {k: extract_node_content(v) for k, v in result.items()}
    return str(result)
