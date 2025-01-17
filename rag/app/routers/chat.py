import json
import time
from os import getenv
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse
from llama_index.core.llms import ChatMessage
from moonai.config.logging import logger
from moonai.llms.gemini import GeminiLLM
from moonai.models.models import ChatCompletionRequest

from .common import extract_node_content, get_qdrant

router = APIRouter()

GEMINI_MODEL = getenv("GEMINI_MODEL", "gemini/gemini-2.0-flash-exp")

async def _enrich_query(request: ChatCompletionRequest) -> tuple[str, str, int, int]:
    """
    Helper function to enrich a query with context from Qdrant.
    Returns: (enriched_content, model, prompt_tokens, completion_tokens)
    """
    # Validate messages in the request
    messages = request.messages
    if not messages:
        raise HTTPException(
            status_code=400,
            detail="No messages provided in the request"
        )

    # Find the last user message
    user_messages = [msg for msg in messages if msg["role"].lower() == "user"]
    if not user_messages:
        raise HTTPException(
            status_code=400,
            detail="No user messages found in the request"
        )

    query = user_messages[-1]["content"]
    logger.info(f"Starting Qdrant query with input: {query}")

    # Ensure Qdrant is initialized
    if not get_qdrant():
        raise HTTPException(
            status_code=500,
            detail="Qdrant connector not initialized"
        )

    # Query Qdrant and extract results
    result1, result2 = get_qdrant().dual_query(query)
    logger.info("Query completed successfully")

    def normalize_content(content):
        """Normalize content to a list of strings."""
        if isinstance(content, list):
            return [str(item) for item in content]
        elif isinstance(content, str):
            return [content]
        elif content:
            return [str(content)]
        return []

    # Prepare the content from results
    result1_content = normalize_content(extract_node_content(result1))
    result2_content = normalize_content(extract_node_content(result2))

    # Safely join normalized content with markdown headers
    content = "\n\n".join([
        "# Result1:",
        "\n\n".join(result1_content),
        "# Result2:",
        "\n\n".join(result2_content)
    ])

    # Token count calculations
    prompt_tokens = len(query.split())
    completion_tokens = len(content.split())

    return content, prompt_tokens, completion_tokens

@router.post("/chat/completions")
@router.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest, authorization: Optional[str] = Header(None)):
    try:
        # Get enriched context
        content, prompt_tokens, completion_tokens = await _enrich_query(request)

        # Create context dictionary here since it's used later
        context = {
            "id": f"chatcmpl-{str(uuid4())}",
            "created": int(time.time()),
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens
            }
        }

        # Initialize GeminiLLM with the correct model name format
        llm = GeminiLLM(
            model=GEMINI_MODEL,
            temperature=request.temperature if hasattr(request, 'temperature') else 0.7
        )

        # Update system message to use content directly
        messages = request.messages.copy()
        system_message = {
            "role": "system",
            "content": f"Here is some relevant context for the conversation:\n\n{content}"
        }
        messages.insert(0, system_message)

        # Convert messages to LlamaIndex ChatMessage format
        chat_messages = [
            ChatMessage(
                role="model" if msg["role"] == "assistant" else msg["role"],  # Gemini expects "model" instead of "assistant"
                content=msg["content"]
            ) for msg in messages
        ]

        if request.stream:
            async def stream_generator():
                try:
                    async for chunk in llm.stream_chat(chat_messages):
                        if chunk.delta is not None:
                            data = {
                                'id': context['id'],
                                'object': 'chat.completion.chunk',
                                'created': context['created'],
                                'model': request.model,
                                'choices': [{
                                    'delta': {'content': str(chunk.delta)},
                                    'index': 0,
                                    'finish_reason': None
                                }]
                            }
                            yield f"data: {json.dumps(data)}\n\n"

                    # Send the final chunk with [DONE]
                    yield f"data: {json.dumps(data)}\n\n"
                    yield "data: [DONE]\n\n"
                except Exception as e:
                    logger.error(f"Error in stream_generator: {str(e)}")
                    raise

            return StreamingResponse(stream_generator(), media_type="text/event-stream")
        else:
            # Get response from Gemini
            response = await llm.achat(chat_messages)

            # Format response in OpenAI-compatible format
            return {
                "id": context["id"],
                "object": "chat.completion",
                "created": context["created"],
                "model": request.model,
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": response.message.content
                    },
                    "finish_reason": "stop"
                }],
                "usage": context["usage"]
            }

    except Exception as e:
        logger.error(f"Error in chat_completions: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/chat/enrich-query")
@router.get("/v1/chat/enrich-query")
async def enrich_query(request: ChatCompletionRequest, authorization: Optional[str] = Header(None)):
    try:
        content, prompt_tokens, completion_tokens = await _enrich_query(request)
        total_tokens = prompt_tokens + completion_tokens

        return {
            "id": f"chatcmpl-{str(uuid4())}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": request.model,
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content
                },
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens
            }
        }

    except Exception as e:
        logger.error(f"Error in enrich_query: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))