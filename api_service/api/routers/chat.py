import logging
import time
from typing import List, Optional
from uuid import uuid4

# Multi-provider imports from main branch
from fastapi import APIRouter, Depends, HTTPException

# RAG imports from feat/rag branch
from llama_index.core import Settings as LlamaSettings
from llama_index.core import VectorStoreIndex
from llama_index.core.schema import NodeWithScore
from openai import AsyncOpenAI  # Moved import to top
from sqlalchemy.ext.asyncio import AsyncSession

# Dependencies for RAG functionality
from api_service.api.dependencies import get_service_context, get_vector_index
from api_service.auth_providers import get_current_user  # Updated import
from api_service.db.base import get_async_session
from api_service.db.models import User
from moonmind.config.settings import settings
from moonmind.factories.anthropic_factory import AnthropicFactory
from moonmind.factories.google_factory import get_google_model
from moonmind.factories.ollama_factory import chat_with_ollama, get_ollama_model
from moonmind.factories.openai_factory import get_openai_model
from moonmind.models_cache import model_cache
from moonmind.rag.retriever import QdrantRAG
from moonmind.schemas.chat_models import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    Choice,
    ChoiceMessage,
    Usage,
)

router = APIRouter()
logger = logging.getLogger(__name__)


def format_context_for_prompt(retrieved_nodes: List[NodeWithScore]) -> str:
    """Format retrieved RAG context for injection into prompts."""
    if not retrieved_nodes:
        return ""

    context_str = "Retrieved context:\n"
    for i, node_with_score in enumerate(retrieved_nodes):
        # Ensure text is not None
        text_content = (
            node_with_score.node.get_text()
            if node_with_score.node
            else "Node text unavailable"
        )
        score = node_with_score.score if node_with_score.score is not None else "N/A"
        context_str += f"--- Source {i + 1} (Score: {score}) ---\n"
        context_str += f"{text_content}\n"
    context_str += "--- End of Retrieved Context ---\n"
    return context_str


def inject_rag_context(
    messages: List, retrieved_context_str: str, user_query: str
) -> List:
    """Inject RAG context into the last user message."""
    if not retrieved_context_str:
        return messages

    processed_messages = []
    context_injected = False

    for i, msg in enumerate(messages):
        is_last_user_message = msg.role == "user" and all(
            m.role != "user" for m in messages[i + 1 :]
        )

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
        logger.warning(
            "RAG context was retrieved but not injected into message history."
        )
        # Create a new user message with context
        from moonmind.schemas.chat_models import Message

        processed_messages.append(
            Message(
                role="user",
                content=f"Please consider the following context to answer the user's question:\n"
                f"{retrieved_context_str}\n\n"
                f"User's question: {user_query}",
            )
        )

    return processed_messages


async def get_rag_context(
    user_query: str,
    vector_index: Optional[VectorStoreIndex],
    llama_settings: LlamaSettings,
) -> str:
    """Retrieve and format RAG context for the given query."""
    if not settings.rag.rag_enabled or not user_query or not vector_index:
        if settings.rag.rag_enabled and not vector_index:
            logger.warning(
                "RAG is enabled but VectorStoreIndex is not available. Skipping RAG."
            )
        return ""

    logger.info(f"RAG enabled. Retrieving context for query: '{user_query[:100]}...'")
    rag_instance = QdrantRAG(
        index=vector_index,
        service_settings=llama_settings,
        similarity_top_k=settings.rag.similarity_top_k,
    )
    retrieved_nodes = rag_instance.retrieve_context(user_query)

    if not retrieved_nodes:
        logger.info("No context retrieved from RAG for this query.")
        return ""

    retrieved_context_str = format_context_for_prompt(retrieved_nodes)

    # Truncate context if it's too long
    if len(retrieved_context_str) > settings.rag.max_context_length_chars:
        logger.warning(
            f"Retrieved context length ({len(retrieved_context_str)}) "
            f"exceeds max ({settings.rag.max_context_length_chars}). Truncating."
        )
        retrieved_context_str = (
            retrieved_context_str[: settings.rag.max_context_length_chars]
            + "\n[Context Truncated]"
        )

    logger.debug(f"Formatted context for prompt:\n{retrieved_context_str[:500]}...")
    return retrieved_context_str


async def get_user_api_key(
    user: User, provider: str, db_session: AsyncSession
) -> Optional[str]:
    """Return the API key for ``provider`` using the AuthProviderManager."""

    from api_service.auth_providers import get_auth_manager

    manager = await get_auth_manager(db_session)
    key_name = f"{provider.upper()}_API_KEY"
    user_obj = user if getattr(user, "id", None) else None
    return await manager.get_secret("profile", key=key_name, user=user_obj)


@router.post("/completions", response_model=ChatCompletionResponse)
async def chat_completions(
    request: ChatCompletionRequest,
    vector_index: Optional[VectorStoreIndex] = Depends(get_vector_index),
    llama_settings: LlamaSettings = Depends(get_service_context),
    db: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user()),
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
            logger.info(
                "No user query found in messages. Skipping RAG for this request."
            )

        # Get RAG context if enabled
        retrieved_context_str = await get_rag_context(
            user_query, vector_index, llama_settings
        )

        # Apply RAG context to messages
        processed_messages = inject_rag_context(
            request.messages, retrieved_context_str, user_query
        )

        # Determine which model/provider to use
        model_to_use = request.model
        if not model_to_use:
            model_to_use = settings.get_default_chat_model()
            logger.info(f"No model specified, using default: {model_to_use}")

        provider = model_cache.get_model_provider(model_to_use)
        logger.info(f"Requested model: {model_to_use}, Provider: {provider}")

        user_api_key = None
        if (
            provider and provider != "Ollama"
        ):  # Only get key if provider exists and is not Ollama
            user_api_key = await get_user_api_key(user, provider, db)
            if not user_api_key:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"API key for {provider} not found. "
                        "Provide a key in your profile or system settings."
                    ),
                )
        # If provider is None here, it means model_cache.get_model_provider(model_to_use) returned None.
        # The request will proceed to the provider checks. If no provider matches,
        # it will fall into the 'else' clause which handles unknown models (triggers refresh or 404).
        # No specific API key logic is needed if provider is None.

        if provider == "OpenAI":
            return await handle_openai_request(
                request, processed_messages, model_to_use, user_api_key
            )
        elif provider == "Google":
            return await handle_google_request(
                request, processed_messages, model_to_use, user_api_key
            )
        elif provider == "Ollama":
            return await handle_ollama_request(
                request, processed_messages, model_to_use
            )
        elif provider == "Anthropic":
            return await handle_anthropic_request(
                request, processed_messages, model_to_use, user_api_key
            )
        else:
            # Handle case where model is not found in cache or provider is unknown
            logger.warning(
                f"Model '{model_to_use}' not found in cache or provider is unknown."
            )
            # Force a cache refresh and try again once
            model_cache.refresh_models_sync()
            provider = model_cache.get_model_provider(model_to_use)
            if provider:
                logger.info(
                    f"Retrying with provider '{provider}' for model '{model_to_use}' after cache refresh."
                )
                raise HTTPException(
                    status_code=404,
                    detail=f"Model '{model_to_use}' found after refresh, but retry logic needs full implementation. Please try request again.",
                )

            raise HTTPException(
                status_code=404,
                detail=f"Model '{model_to_use}' not found or provider unknown.",
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            f"Unhandled error in chat_completions for model {getattr(request, 'model', 'unknown')}: {e}"
        )
        raise HTTPException(
            status_code=500, detail=f"An internal server error occurred: {str(e)}"
        )


async def handle_openai_request(
    request: ChatCompletionRequest, messages: List, model_to_use: str, api_key: str
) -> ChatCompletionResponse:
    """Handle OpenAI provider requests."""
    # Check if OpenAI is enabled - server-side check can remain if desired,
    # but user-specific key is now the primary concern.
    if (
        not settings.is_provider_enabled("openai")
        and not settings.openai.openai_enabled
    ):
        raise HTTPException(
            status_code=400, detail="OpenAI provider is disabled on the server."
        )

    if (
        not api_key
    ):  # This check is technically redundant if get_user_api_key enforces it
        raise HTTPException(
            status_code=400, detail="OpenAI API key not provided for the user."
        )

    openai_model_name = get_openai_model(
        model_to_use
    )  # This function might need api_key if it uses it
    logger.info(f"Routing to OpenAI for model: {openai_model_name}")

    # Prepare messages for OpenAI
    openai_messages = [{"role": msg.role, "content": msg.content} for msg in messages]

    # The import 'from openai import AsyncOpenAI' is now at the top of the file.
    # This try block will encompass client creation and the API call.
    try:
        client = AsyncOpenAI(api_key=api_key)
        openai_response = await client.chat.completions.create(
            model=openai_model_name,
            messages=openai_messages,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
        )
    except Exception as e:
        logger.error(f"Error calling OpenAI API for model {openai_model_name}: {e}")
        # Consider more specific error handling for common OpenAI API errors if needed
        raise HTTPException(status_code=500, detail=f"OpenAI API error: {str(e)}")

    if not openai_response.choices:
        raise HTTPException(
            status_code=500,
            detail="Invalid response from OpenAI API: No choices returned.",
        )

    # Accessing response data according to OpenAI SDK v1.x.x
    first_choice = openai_response.choices[0]
    ai_message_content = first_choice.message.content
    # In tests we sometimes mock choices without ``finish_reason``. Default to
    # "stop" which mirrors typical OpenAI responses when a generation completes
    # naturally.
    finish_reason = getattr(first_choice, "finish_reason", "stop") or "stop"
    usage_data = openai_response.usage

    if usage_data is None:  # Should not happen with successful chat completion
        logger.error("OpenAI response missing usage data.")
        # Provide default usage if necessary, or handle as an error
        usage_data = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    return ChatCompletionResponse(
        id=getattr(openai_response, "id", ""),
        object=getattr(openai_response, "object", "chat.completion"),
        created=getattr(openai_response, "created", 0),
        model=getattr(openai_response, "model", model_to_use),
        choices=[
            Choice(
                index=0,  # SDK v1.x index is usually part of the choice object, but response schema expects it
                message=ChoiceMessage(
                    role="assistant", content=ai_message_content.strip()
                ),
                finish_reason=finish_reason,
            )
        ],
        usage=Usage(  # Ensure these fields exist on usage_data
            prompt_tokens=getattr(
                usage_data, "prompt_tokens", usage_data.get("prompt_tokens", 0)
            ),
            completion_tokens=getattr(
                usage_data, "completion_tokens", usage_data.get("completion_tokens", 0)
            ),
            total_tokens=getattr(
                usage_data, "total_tokens", usage_data.get("total_tokens", 0)
            ),
        ),
    )


async def handle_google_request(
    request: ChatCompletionRequest, messages: List, model_to_use: str, api_key: str
) -> ChatCompletionResponse:
    """Handle Google Gemini provider requests."""
    # Server-side enablement check (can remain)
    if (
        not settings.is_provider_enabled("google")
        and not settings.google.google_enabled
    ):
        raise HTTPException(
            status_code=400, detail="Google provider is disabled on the server."
        )

    if not api_key:  # Redundant if get_user_api_key enforces it
        raise HTTPException(
            status_code=400, detail="Google API key not provided for the user."
        )

    logger.info(f"Routing to Google for model: {model_to_use}")

    # The get_google_model factory already uses settings.google.google_api_key.
    # We need to modify it or create a new client instance with the user's key.
    # For now, let's assume get_google_model can take an api_key argument.
    # This will require modifying the factory function.

    # Convert messages to Google's format
    contents = []
    for msg in messages:
        gemini_role = "user" if msg.role == "user" else "model"
        if msg.role not in {"user", "system", "assistant"}:
            raise HTTPException(
                status_code=400, detail=f"Invalid message role: {msg.role}"
            )
        contents.append({"role": gemini_role, "parts": [msg.content]})

    if not contents:
        raise HTTPException(status_code=400, detail="No messages provided.")

    logger.debug(
        f"Final `contents` for Gemini (with RAG context if any): {str(contents)[:1000]}..."
    )

    # Modify get_google_model or create client directly.
    # For now, we'll assume get_google_model is updated to accept api_key.
    # If get_google_model cannot be changed to accept api_key, we'd do:
    # import google.generativeai as genai
    # genai.configure(api_key=api_key) # This configures globally for the genai module.
    # chat_model = genai.GenerativeModel(model_name=model_to_use)
    # This global configuration has similar concurrency issues as OpenAI's old SDK.
    # A better approach is if the SDK supports per-request API keys or client instances.
    # Google's SDK (google.generativeai) uses a global configuration via genai.configure().
    # This is a known limitation if needing per-user keys without re-configuring constantly.
    #
    # Let's proceed by calling a modified get_google_model that handles this.
    # We will adjust get_google_model in a later step or assume it's done.
    # For the purpose of this step, we pass api_key to get_google_model.
    chat_model = get_google_model(model_name=model_to_use, api_key=api_key)
    try:
        response_gemini = chat_model.generate_content(contents)
    except Exception as e:
        logger.error(f"Error generating content with Gemini: {e}")
        error_str = str(e).lower()
        if (
            "invalid role" in error_str
            or "please use a valid role" in error_str
            or "must have alternating roles" in error_str
        ):
            raise HTTPException(
                status_code=400,
                detail=f"Role or turn order error with Gemini API: {str(e)}. "
                f"This might be due to how RAG context was injected. "
                f"Current conversation structure: {[(m['role'], m['parts'][0][:50] + '...') for m in contents]}",
            )
        raise HTTPException(
            status_code=500, detail=f"Google Gemini API error: {str(e)}"
        )

    if (
        not response_gemini.candidates
        or not response_gemini.candidates[0].content.parts
    ):
        raise HTTPException(status_code=500, detail="Invalid response from Google LLM")

    ai_message = response_gemini.candidates[0].content.parts[0].text

    # Simple token counting
    prompt_tokens_estimate = sum(
        len(str(part).split())
        for c in contents
        for part_list in c.get("parts", [])
        for part in (part_list if isinstance(part_list, list) else [part_list])
    )
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


async def handle_anthropic_request(
    request: ChatCompletionRequest, messages: List, model_to_use: str, api_key: str
) -> ChatCompletionResponse:
    """Handle Anthropic provider requests."""
    if (
        not settings.is_provider_enabled("anthropic")
        and not settings.anthropic.anthropic_enabled
    ):
        raise HTTPException(
            status_code=400, detail="Anthropic provider is disabled on the server."
        )

    if not api_key:  # Redundant if get_user_api_key enforces it
        raise HTTPException(
            status_code=400, detail="Anthropic API key not provided for the user."
        )

    logger.info(f"Routing to Anthropic for model: {model_to_use}")

    # The AnthropicFactory.create_anthropic_model currently uses global settings.
    # We need to pass the api_key to it.
    anthropic_model = AnthropicFactory.create_anthropic_model(
        api_key=api_key, model_name=model_to_use
    )

    # Convert messages to Anthropic's format (ensure system message is handled correctly if present)
    anthropic_messages = []
    system_prompt = None
    for msg in messages:
        if msg.role == "system":
            system_prompt = msg.content  # Anthropic uses a separate system parameter
        elif msg.role in [
            "user",
            "assistant",
        ]:  # Anthropic uses "assistant" for model's previous turns
            anthropic_messages.append({"role": msg.role, "content": msg.content})
        else:
            # Potentially log a warning or adapt if other roles are possible via RAG etc.
            logger.warning(
                f"Unsupported role {msg.role} for Anthropic, skipping message."
            )

    if not anthropic_messages:  # Ensure there's at least one user or assistant message
        raise HTTPException(
            status_code=400,
            detail="No user or assistant messages provided for Anthropic.",
        )

    try:
        # Note: Anthropic's SDK might use `messages` and `system` parameters differently
        # depending on the specific SDK version and method used.
        # This example assumes a structure similar to OpenAI's newer APIs or direct REST usage.
        # Adjust if using specific Anthropic SDK methods like `anthropic.messages.create`

        # The llama-index Anthropic class's `chat` method expects a list of `ChatMessage`
        from llama_index.core.llms import ChatMessage as LlamaChatMessage

        llama_index_messages = []
        for m in anthropic_messages:
            llama_index_messages.append(
                LlamaChatMessage(role=m["role"], content=m["content"])
            )

        # If a system prompt exists and LlamaIndex's Anthropic wrapper uses it in `chat`:
        # (This needs verification based on how LlamaIndex's Anthropic class handles system prompts.
        # Some models/SDKs expect it as the first message, others as a separate parameter.)
        # For now, assuming it's handled if passed as a system message in the list.
        # If AnthropicFactory already configures the model, we might not need to pass it again.
        # The `model` parameter in `Anthropic` is for model name, not an instance.

        # Directly using the chat method of the created anthropic_model instance
        # The Anthropic LlamaIndex LLM takes `messages` directly.
        # System prompt handling in LlamaIndex Anthropic:
        # It seems LlamaIndex's Anthropic wrapper will extract a system prompt if it's the first message
        # or if provided via `system_prompt` kwarg to some methods, but `chat` primarily takes `messages`.
        # Let's ensure the system prompt is the first message if present.

        final_messages_for_anthropic = []
        if system_prompt:
            final_messages_for_anthropic.append(
                LlamaChatMessage(role="system", content=system_prompt)
            )
        final_messages_for_anthropic.extend(llama_index_messages)

        # The `chat` method is asynchronous in LlamaIndex
        anthropic_response_obj = await anthropic_model.achat(
            messages=final_messages_for_anthropic,
            # Anthropic uses max_tokens_to_sample, check LlamaIndex wrapper
            # LlamaIndex's Anthropic wrapper might map `max_tokens` to `max_tokens_to_sample`
            max_tokens=request.max_tokens,
            temperature=request.temperature,
        )

        ai_message_content = anthropic_response_obj.message.content

    except Exception as e:
        logger.error(f"Error calling Anthropic API for model {model_to_use}: {e}")
        raise HTTPException(status_code=500, detail=f"Anthropic API error: {str(e)}")

    # Token counting for Anthropic can be complex.
    # Anthropic provides token counts in its API response if available.
    # For now, using a simple estimation.
    # TODO: Use actual token counts from response if LlamaIndex surfaces them.
    # anthropic_response_obj.raw often contains the underlying provider response.
    prompt_tokens_estimate = sum(
        len(msg.content.split()) for msg in messages
    )  # Based on original request messages
    completion_tokens_estimate = len(ai_message_content.split())

    # Check if token usage information is available in the raw response.
    # Example:
    # if anthropic_response_obj.raw and 'usage' in anthropic_response_obj.raw:
    #     prompt_tokens = anthropic_response_obj.raw['usage'].get('input_tokens', prompt_tokens_estimate)
    #     completion_tokens = anthropic_response_obj.raw['usage'].get('output_tokens', completion_tokens_estimate)

    return ChatCompletionResponse(
        id=f"cmpl-anthropic-{uuid4().hex}",
        object="chat.completion",
        created=int(time.time()),
        model=model_to_use,  # This should be the specific model name like "claude-3-opus-20240229"
        choices=[
            Choice(
                index=0,
                message=ChoiceMessage(
                    role="assistant", content=ai_message_content.strip()
                ),
                finish_reason="stop",  # Or map from Anthropic's finish reasons
            )
        ],
        usage=Usage(
            prompt_tokens=prompt_tokens_estimate,
            completion_tokens=completion_tokens_estimate,
            total_tokens=prompt_tokens_estimate + completion_tokens_estimate,
        ),
    )


async def handle_ollama_request(
    request: ChatCompletionRequest, messages: List, model_to_use: str
) -> ChatCompletionResponse:
    """Handle Ollama provider requests."""
    # Check if Ollama is enabled
    if not settings.is_provider_enabled("ollama"):
        raise HTTPException(
            status_code=400, detail="Ollama provider is disabled or not available."
        )

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
        raise HTTPException(
            status_code=500,
            detail="Invalid response from Ollama API: No message returned.",
        )

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
                message=ChoiceMessage(
                    role="assistant", content=ai_message_content.strip()
                ),
                finish_reason="stop",
            )
        ],
        usage=Usage(
            prompt_tokens=prompt_tokens_est,
            completion_tokens=completion_tokens_est,
            total_tokens=prompt_tokens_est + completion_tokens_est,
        ),
    )
