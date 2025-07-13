import logging
from typing import Any, Dict, List

import httpx

from moonmind.config.settings import settings

logger = logging.getLogger(__name__)


def get_ollama_model(model_name: str) -> str:
    """
    Get the Ollama model name. For now, just return the model name as-is.
    In the future, this could handle model name mapping or validation.
    """
    return model_name


async def chat_with_ollama(
    model_name: str, messages: List[Dict[str, str]], **kwargs
) -> Dict[str, Any]:
    """
    Send a chat completion request to Ollama.

    Args:
        model_name: The name of the Ollama model to use
        messages: List of message dictionaries with 'role' and 'content'
        **kwargs: Additional parameters like temperature, max_tokens, etc.

    Returns:
        Response dictionary from Ollama
    """
    url = f"{settings.ollama.ollama_base_url}/api/chat"

    # Convert messages to Ollama format
    ollama_messages = []
    for msg in messages:
        ollama_messages.append({"role": msg["role"], "content": msg["content"]})

    payload = {"model": model_name, "messages": ollama_messages, "stream": False}

    # Add optional parameters
    if "temperature" in kwargs and kwargs["temperature"] is not None:
        payload["options"] = payload.get("options", {})
        payload["options"]["temperature"] = kwargs["temperature"]

    if "max_tokens" in kwargs and kwargs["max_tokens"] is not None:
        payload["options"] = payload.get("options", {})
        payload["options"]["num_predict"] = kwargs["max_tokens"]

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=60.0)
            response.raise_for_status()
            return response.json()
    except httpx.RequestError as e:
        logger.error(f"Error calling Ollama API: {e}")
        raise e


async def list_ollama_models() -> List[Any]:
    """
    List available Ollama models.

    Returns:
        List of model objects from Ollama
    """
    if not settings.ollama.ollama_enabled:
        logger.warning("Ollama models are not available because Ollama is disabled")
        return []

    url = f"{settings.ollama.ollama_base_url}/api/tags"

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=10.0)
            response.raise_for_status()
            data = response.json()

            # Return the models list
            models = data.get("models", [])
            return models
    except httpx.RequestError as e:
        logger.error(f"Error listing Ollama models: {e}")
        return []
