import logging
import time
from typing import List, Optional
from uuid import uuid4
import openai
import google.generativeai as genai

# RAG imports from feat/rag branch
from llama_index.core import Settings as LlamaSettings
from llama_index.core import VectorStoreIndex
from llama_index.core.schema import NodeWithScore

# Multi-provider imports from main branch
from fastapi import APIRouter, Depends, HTTPException
from moonmind.config.settings import settings
from moonmind.factories.google_factory import get_google_model
from moonmind.factories.openai_factory import get_openai_model
from moonmind.factories.ollama_factory import get_ollama_model, chat_with_ollama
from moonmind.rag.retriever import QdrantRAG
from moonmind.schemas.chat_models import (ChatCompletionRequest,
                                          ChatCompletionResponse, Choice,
                                          ChoiceMessage, Usage)
from moonmind.models_cache import model_cache

# Dependencies for RAG functionality
from ..dependencies import get_service_context, get_vector_index

router = APIRouter()
logger = logging.getLogger(__name__)

def format_context_for_prompt(retrieved_nodes: List[NodeWithScore]) -> str:
    """Format retrieved RAG context for injection into prompts."""
    if not retrieved_nodes:
        return ""

    context_str = "Retrieved context:\n"
    for i, node_with_score in enumerate(retrieved_nodes):
        # Ensure text is not None
        text_content = node_with_score.node.get_text() if node_with_score.node else "Node text unavailable"
        score = node_with_score.score if node_with_score.score is not None else "N/A"
        context_str += f"--- Source {i+1} (Score: {score}) ---\n"
        context_str += f"{text_content}\n"
    context_str += "--- End of Retrieved Context ---\n"
    return context_str

def inject_rag_context(messages: List, retrieved_context_str: str, user_query: str) -> List:
    """Inject RAG context into the last user message."""
    if not retrieved_context_str:
        return messages
    
    processed_messages = []
    context_injected = False
    
    for i, msg in enumerate(messages):
        is_last_user_message = (msg.role == "user" and
                               all(m.role != "user" for m in messages[i+1:]))
        
        current_content = msg.content
        if is_last_user_message and not context_injected:
            # Augment the content of the last user message
            current_content = (
                f"Please consider the following context to answer the user's question:\n"
                f"{retrieved_context_str}\n\n"
                f"User's question: {msg.content}"
            )
            context_injected = True
            logger.debug("Injected RAG context into the last user message.")
        
        processed_messages.append(type(msg)(role=msg.role, content=current_content))
    
    # Fallback: if no user message found but we have context
    if not context_injected and retrieved_context_str and user_query:
        logger.warning("RAG context was retrieved but not injected into message history.")
        # Create a new user message with context
        from moonmind.schemas.chat_models import Message
        processed_messages.append(Message(
            role="user",
            content=f"Please consider the following context to answer the user's question:\n"
                   f"{retrieved_context_str}\n\n"
                   f"User's question: {user_query}"
        ))
    
    return processed_messages

async def get_rag_context(user_query: str, vector_index: Optional[VectorStoreIndex], 
                         llama_settings: LlamaSettings) -> str:
    """Retrieve and format RAG context for the given query."""
    if not settings.rag.rag_enabled or not user_query or not vector_index:
        if settings.rag.rag_enabled and not vector_index:
            logger.warning("RAG is enabled but VectorStoreIndex is not available. Skipping RAG.")
        return ""
    
    logger.info(f"RAG enabled. Retrieving context for query: '{user_query[:100]}...'")
    rag_instance = QdrantRAG(
        index=vector_index,
        service_settings=llama_settings,
        similarity_top_k=settings.rag.similarity_top_k
    )
    retrieved_nodes = rag_instance.retrieve_context(user_query)
    
    if not retrieved_nodes:
        logger.info("No context retrieved from RAG for this query.")
        return ""
    
    retrieved_context_str = format_context_for_prompt(retrieved_nodes)
    
    # Truncate context if it's too long
    if len(retrieved_context_str) > settings.rag.max_context_length_chars:
        logger.warning(f"Retrieved context length ({len(retrieved_context_str)}) "
                      f"exceeds max ({settings.rag.max_context_length_chars}). Truncating.")
        retrieved_context_str = retrieved_context_str[:settings.rag.max_context_length_chars] + \
                               "\n[Context Truncated]"
    
    logger.debug(f"Formatted context for prompt:\n{retrieved_context_str[:500]}...")
    return retrieved_context_str

@router.post("/v1/chat/completions", response_model=ChatCompletionResponse)
async def chat_completions(
    request: ChatCompletionRequest,
    vector_index: Optional[VectorStoreIndex] = Depends(get_vector_index),
    llama_settings: LlamaSettings = Depends(get_service_context)
):
    try:
        # Extract the last user message as the query for RAG
        user_query = ""
        if request.messages:
            for msg in reversed(request.messages):
                if msg.role == "user":
                    user_query = msg.content
                    break
        
        if not user_query:
            logger.info("No user query found in messages. Skipping RAG for this request.")
        
        # Get RAG context if enabled
        retrieved_context_str = await get_rag_context(user_query, vector_index, llama_settings)
        
        # Apply RAG context to messages
        processed_messages = inject_rag_context(request.messages, retrieved_context_str, user_query)
        
        # Determine which model/provider to use
        model_to_use = request.model
        if not model_to_use:
            model_to_use = settings.get_default_chat_model()
            logger.info(f"No model specified, using default: {model_to_use}")
        
        provider = model_cache.get_model_provider(model_to_use)
        logger.info(f"Requested model: {model_to_use}, Provider: {provider}")

        if provider == "OpenAI":
            return await handle_openai_request(request, processed_messages, model_to_use)
        elif provider == "Google":
            return await handle_google_request(request, processed_messages, model_to_use)
        elif provider == "Ollama":
            return await handle_ollama_request(request, processed_messages, model_to_use)
        else:
            # Handle case where model is not found in cache or provider is unknown
            logger.warning(f"Model '{model_to_use}' not found in cache or provider is unknown.")
            # Force a cache refresh and try again once
            model_cache.refresh_models_sync()
            provider = model_cache.get_model_provider(model_to_use)
            if provider:
                logger.info(f"Retrying with provider '{provider}' for model '{model_to_use}' after cache refresh.")
                raise HTTPException(status_code=404, detail=f"Model '{model_to_use}' found after refresh, but retry logic needs full implementation. Please try request again.")
            
            raise HTTPException(status_code=404, detail=f"Model '{model_to_use}' not found or provider unknown.")

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Unhandled error in chat_completions for model {getattr(request, 'model', 'unknown')}: {e}")
        raise HTTPException(status_code=500, detail=f"An internal server error occurred: {str(e)}")

async def handle_openai_request(request: ChatCompletionRequest, messages: List, model_to_use: str) -> ChatCompletionResponse:
    """Handle OpenAI provider requests."""
    # Check if OpenAI is enabled
    if not settings.is_provider_enabled("openai"):
        if not settings.openai.openai_enabled:
            raise HTTPException(status_code=400, detail="OpenAI provider is disabled.")
        else:
            raise HTTPException(status_code=400, detail="OpenAI API key not configured for the server.")
    
    openai_model_name = get_openai_model(model_to_use)
    logger.info(f"Routing to OpenAI for model: {openai_model_name}")

    # Prepare messages for OpenAI
    openai_messages = [{"role": msg.role, "content": msg.content} for msg in messages]

    try:
        openai.api_key = settings.openai.openai_api_key
        openai_response = await openai.ChatCompletion.acreate(
            model=openai_model_name,
            messages=openai_messages,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
        )
    except Exception as e:
        logger.error(f"Error calling OpenAI API for model {openai_model_name}: {e}")
        raise HTTPException(status_code=500, detail=f"OpenAI API error: {str(e)}")

    if not openai_response.choices:
        raise HTTPException(status_code=500, detail="Invalid response from OpenAI API: No choices returned.")
    
    ai_message_content = openai_response.choices[0].message.content
    usage_data = openai_response.usage
    
    return ChatCompletionResponse(
        id=openai_response.id,
        object="chat.completion",
        created=openai_response.created,
        model=openai_response.model,
        choices=[
            Choice(
                index=0,
                message=ChoiceMessage(role="assistant", content=ai_message_content.strip()),
                finish_reason=openai_response.choices[0].finish_reason,
            )
        ],
        usage=Usage(
            prompt_tokens=usage_data.prompt_tokens,
            completion_tokens=usage_data.completion_tokens,
            total_tokens=usage_data.total_tokens,
        ),
    )

async def handle_google_request(request: ChatCompletionRequest, messages: List, model_to_use: str) -> ChatCompletionResponse:
    """Handle Google Gemini provider requests."""
    # Check if Google is enabled
    if not settings.is_provider_enabled("google"):
        if not settings.google.google_enabled:
            raise HTTPException(status_code=400, detail="Google provider is disabled.")
        else:
            raise HTTPException(status_code=400, detail="Google API key not configured for the server.")
    
    logger.info(f"Routing to Google for model: {model_to_use}")

    # Convert messages to Google's format
    contents = []
    for msg in messages:
        gemini_role = "user" if msg.role == "user" else "model"
        if msg.role not in {"user", "system", "assistant"}:
            raise HTTPException(status_code=400, detail=f"Invalid message role: {msg.role}")
        contents.append({"role": gemini_role, "parts": [msg.content]})

    if not contents:
        raise HTTPException(status_code=400, detail="No messages provided.")

    logger.debug(f"Final `contents` for Gemini (with RAG context if any): {str(contents)[:1000]}...")

    chat_model = get_google_model(model_to_use)
    try:
        response_gemini = chat_model.generate_content(contents)
    except Exception as e:
        logger.error(f"Error generating content with Gemini: {e}")
        if "Please use a valid role" in str(e) or "must have alternating roles" in str(e):
            raise HTTPException(
                status_code=400,
                detail=f"Role or turn order error with Gemini API: {str(e)}. "
                       f"This might be due to how RAG context was injected. "
                       f"Current conversation structure: {[(m['role'], m['parts'][0][:50] + '...') for m in contents]}"
            )
        raise HTTPException(status_code=500, detail=f"Google Gemini API error: {str(e)}")

    if not response_gemini.candidates or not response_gemini.candidates[0].content.parts:
        raise HTTPException(status_code=500, detail="Invalid response from Google LLM")

    ai_message = response_gemini.candidates[0].content.parts[0].text

    # Simple token counting
    prompt_tokens_estimate = sum(len(str(part).split()) for c in contents for part_list in c.get("parts", [])
                                for part in (part_list if isinstance(part_list, list) else [part_list]))
    completion_tokens_estimate = len(ai_message.split())

    return ChatCompletionResponse(
        id=f"cmpl-{uuid4().hex}",
        object="chat.completion",
        created=int(time.time()),
        model=model_to_use,
        choices=[
            Choice(
                index=0,
                message=ChoiceMessage(role="assistant", content=ai_message),
                finish_reason="stop",
            )
        ],
        usage=Usage(
            prompt_tokens=prompt_tokens_estimate,
            completion_tokens=completion_tokens_estimate,
            total_tokens=prompt_tokens_estimate + completion_tokens_estimate,
        ),
    )

async def handle_ollama_request(request: ChatCompletionRequest, messages: List, model_to_use: str) -> ChatCompletionResponse:
    """Handle Ollama provider requests."""
    # Check if Ollama is enabled
    if not settings.is_provider_enabled("ollama"):
        raise HTTPException(status_code=400, detail="Ollama provider is disabled or not available.")
    
    ollama_model_name = get_ollama_model(model_to_use)
    logger.info(f"Routing to Ollama for model: {ollama_model_name}")

    # Prepare messages for Ollama
    ollama_messages = [{"role": msg.role, "content": msg.content} for msg in messages]

    try:
        ollama_response = await chat_with_ollama(
            ollama_model_name,
            ollama_messages,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
        )
    except Exception as e:
        logger.error(f"Error calling Ollama API for model {ollama_model_name}: {e}")
        raise HTTPException(status_code=500, detail=f"Ollama API error: {str(e)}")

    if not ollama_response.get("message"):
        raise HTTPException(status_code=500, detail="Invalid response from Ollama API: No message returned.")
    
    ai_message_content = ollama_response["message"]["content"]
    
    # Estimate tokens for Ollama response
    prompt_tokens_est = sum(len(msg.content.split()) for msg in messages)
    completion_tokens_est = len(ai_message_content.split())

    return ChatCompletionResponse(
        id=f"cmpl-ollama-{uuid4().hex}",
        object="chat.completion",
        created=int(time.time()),
        model=ollama_model_name,
        choices=[
            Choice(
                index=0,
                message=ChoiceMessage(role="assistant", content=ai_message_content.strip()),
                finish_reason="stop",
            )
        ],
        usage=Usage(
            prompt_tokens=prompt_tokens_est,
            completion_tokens=completion_tokens_est,
            total_tokens=prompt_tokens_est + completion_tokens_est,
        ),
    )
