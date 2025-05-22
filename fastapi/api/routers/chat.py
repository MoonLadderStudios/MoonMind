import logging
import time
from typing import List, Optional
from uuid import uuid4

from llama_index.core import Settings as LlamaSettings
from llama_index.core import VectorStoreIndex
from llama_index.core.schema import NodeWithScore

from fastapi import APIRouter, Depends, HTTPException
from moonmind.config.settings import settings
from moonmind.factories.google_factory import get_google_model
from moonmind.rag.retriever import QdrantRAG
from moonmind.schemas.chat_models import (ChatCompletionRequest,
                                          ChatCompletionResponse, Choice,
                                          ChoiceMessage, Usage)

from ..dependencies import get_service_context, get_vector_index

router = APIRouter()
logger = logging.getLogger(__name__)

def format_context_for_prompt(retrieved_nodes: List[NodeWithScore]) -> str:
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

@router.post("/v1/chat/completions", response_model=ChatCompletionResponse)
async def chat_completions(
    request: ChatCompletionRequest,
    vector_index: Optional[VectorStoreIndex] = Depends(get_vector_index),
    llama_settings: LlamaSettings = Depends(get_service_context)
):
    try:
        user_query = ""
        # Extract the last user message as the query for RAG
        if request.messages:
            for msg in reversed(request.messages):
                if msg.role == "user":
                    user_query = msg.content
                    break
        if not user_query:
            logger.info("No user query found in messages. Skipping RAG for this request.")

        retrieved_context_str = ""
        if settings.rag.rag_enabled and user_query and vector_index:
            logger.info(f"RAG enabled. Retrieving context for query: '{user_query[:100]}...'")
            rag_instance = QdrantRAG(
                index=vector_index,
                service_settings=llama_settings,
                similarity_top_k=settings.rag.similarity_top_k
            )
            retrieved_nodes = rag_instance.retrieve_context(user_query)

            if retrieved_nodes:
                retrieved_context_str = format_context_for_prompt(retrieved_nodes)
                # Truncate context if it's too long
                if len(retrieved_context_str) > settings.rag.max_context_length_chars:
                    logger.warning(f"Retrieved context length ({len(retrieved_context_str)}) "
                                   f"exceeds max ({settings.rag.max_context_length_chars}). Truncating.")
                    retrieved_context_str = retrieved_context_str[:settings.rag.max_context_length_chars] + \
                                           "\n[Context Truncated]"
                logger.debug(f"Formatted context for prompt:\n{retrieved_context_str[:500]}...")
            else:
                logger.info("No context retrieved from RAG for this query.")
        elif settings.rag.rag_enabled and not vector_index:
            logger.warning("RAG is enabled but VectorStoreIndex is not available. Skipping RAG.")

        # Convert OpenAI-style messages to Google LLM format
        contents = []

        # Process messages and inject RAG context at appropriate point
        processed_messages = []
        context_injected = False
        for i, msg in enumerate(request.messages):
            is_last_user_message = (msg.role == "user" and
                                   all(m.role != "user" for m in request.messages[i+1:]))

            gemini_role = "user" if msg.role == "user" else "model"
            if msg.role not in {"user", "system", "assistant"}:
                 raise HTTPException(status_code=400, detail=f"Invalid message role: {msg.role}")

            current_content = msg.content
            if retrieved_context_str and is_last_user_message and not context_injected:
                # Augment the content of the last user message
                current_content = (
                    f"Please consider the following context to answer the user's question:\n"
                    f"{retrieved_context_str}\n\n"
                    f"User's question: {msg.content}"
                )
                context_injected = True
                logger.debug("Injected RAG context into the last user message.")

            processed_messages.append({"role": gemini_role, "parts": [current_content]})

        # If context wasn't injected and we have context + a user_query, we might need a fallback
        if not context_injected and retrieved_context_str and user_query:
            logger.warning("RAG context was retrieved but not injected into message history (e.g. no final user message).")
            # Fallback: create a new user message with context and query if no messages
            if not processed_messages:
                processed_messages.append({
                    "role": "user",
                    "parts": [
                        f"Please consider the following context to answer the user's question:\n"
                        f"{retrieved_context_str}\n\n"
                        f"User's question: {user_query}"
                    ]
                })

        contents = processed_messages
        if not contents and user_query:
            contents.append({"role": "user", "parts": [user_query]})
        elif not contents and not user_query:
            raise HTTPException(status_code=400, detail="No messages or query provided.")

        logger.info(f"Requested model was: {request.model}")
        logger.debug(f"Final `contents` for Gemini (with RAG context if any): {str(contents)[:1000]}...")

        chat_model = get_google_model(request.model)
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
            raise

        if not response_gemini.candidates or not response_gemini.candidates[0].content.parts:
            raise HTTPException(status_code=500, detail="Invalid response from Google LLM")

        ai_message = response_gemini.candidates[0].content.parts[0].text

        # Simple token counting (replace with model.count_tokens if precise needed)
        prompt_tokens_estimate = sum(len(str(part).split()) for c in contents for part_list in c.get("parts", [])
                                    for part in (part_list if isinstance(part_list, list) else [part_list]))
        completion_tokens_estimate = len(ai_message.split())

        response = ChatCompletionResponse(
            id=f"cmpl-{uuid4().hex}",
            object="chat.completion",
            created=int(time.time()),
            model=request.model,
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
        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error in chat_completions: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"An internal error occurred: {str(e)}"
        )