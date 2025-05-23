import logging
import time
from uuid import uuid4
import openai
import google.generativeai as genai

from fastapi import APIRouter, HTTPException
from moonmind.factories.google_factory import get_google_model # Still needed for Google logic
from moonmind.factories.openai_factory import get_openai_model # Still needed for OpenAI logic
from moonmind.schemas.chat_models import ChatCompletionRequest
from moonmind.schemas.chat_models import (ChatCompletionResponse, Choice,
                                          ChoiceMessage, Usage)
from moonmind.config.settings import settings
from moonmind.models_cache import model_cache # Import the model cache

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/v1/chat/completions", response_model=ChatCompletionResponse)
async def chat_completions(request: ChatCompletionRequest):
    try:
        provider = model_cache.get_model_provider(request.model)

        if provider == "OpenAI":
            if not settings.openai.openai_api_key:
                raise HTTPException(status_code=400, detail="OpenAI API key not configured for the server.")
            
            # The get_openai_model factory function simply returns the model name.
            # It's kept for consistency but could be bypassed if model_id from request is directly used.
            openai_model_name = get_openai_model(request.model) 
            logger.info(f"Routing to OpenAI for model: {openai_model_name}")

            # Prepare messages for OpenAI
            openai_messages = [{"role": msg.role, "content": msg.content} for msg in request.messages]

            try:
                openai.api_key = settings.openai.openai_api_key
                openai_response = await openai.ChatCompletion.acreate(
                    model=openai_model_name, # Use the potentially settings-adjusted model name
                    messages=openai_messages,
                    max_tokens=request.max_tokens,
                    temperature=request.temperature,
                    # TODO: Add other parameters like top_p, n, stream from request
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
                model=openai_response.model, # This will be the actual model used by OpenAI
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

        elif provider == "Google":
            if not settings.google.google_api_key:
                raise HTTPException(status_code=400, detail="Google API key not configured for the server.")
            
            google_model_name = request.model # Use the requested model ID directly
            logger.info(f"Routing to Google for model: {google_model_name}")

            # Convert messages to Google's format
            google_contents = []
            for msg in request.messages:
                role = "user" # Default role
                if msg.role == "assistant" or msg.role == "system": # System messages are treated as model turns before user
                    role = "model"
                elif msg.role == "user":
                    role = "user"
                else: # Should be caught by Pydantic, but good to log
                    logger.warning(f"Invalid role {msg.role} for Google model, defaulting to 'user'.")
                google_contents.append({"role": role, "parts": [{"text": msg.content}]})

            chat_model_instance = get_google_model(google_model_name) # Get the actual model instance from factory
            try:
                google_response = chat_model_instance.generate_content(google_contents)
            except ValueError as e:
                logger.error(f"ValueError with Google Gemini model {google_model_name}: {e}")
                detail_msg = f"Invalid argument for Google model. Original: {str(e)}"
                if "role" in str(e).lower(): # More specific message for role errors
                    detail_msg = f"Invalid role in request for Google model. Ensure roles are 'user' or 'model'. Original: {str(e)}"
                raise HTTPException(status_code=400, detail=detail_msg)
            except Exception as e:
                logger.error(f"Error generating content with Google Gemini model {google_model_name}: {e}")
                raise HTTPException(status_code=500, detail=f"Google Gemini API error: {str(e)}")

            if not google_response.candidates or not google_response.candidates[0].content.parts:
                raise HTTPException(status_code=500, detail="Invalid response from Google Gemini: No content.")
            
            ai_message_text = google_response.candidates[0].content.parts[0].text
            
            # Estimate tokens for Google response (actual token count might not be readily available)
            prompt_tokens_est = sum(len(part["parts"][0]["text"].split()) for part in google_contents)
            completion_tokens_est = len(ai_message_text.split())

            return ChatCompletionResponse(
                id=f"cmpl-goog-{uuid4().hex}",
                object="chat.completion",
                created=int(time.time()),
                model=google_model_name, # Echo back the requested model
                choices=[
                    Choice(
                        index=0,
                        message=ChoiceMessage(role="assistant", content=ai_message_text.strip()),
                        finish_reason="stop", # Or map from google_response if available
                    )
                ],
                usage=Usage(
                    prompt_tokens=prompt_tokens_est,
                    completion_tokens=completion_tokens_est,
                    total_tokens=prompt_tokens_est + completion_tokens_est,
                ),
            )
        else:
            # Handle case where model is not found in cache or provider is unknown
            logger.warning(f"Model '{request.model}' not found in cache or provider is unknown.")
            # Force a cache refresh and try again once, in case the model was just added
            model_cache.refresh_models_sync()
            provider = model_cache.get_model_provider(request.model)
            if provider: # Retry logic after refresh
                 logger.info(f"Retrying with provider '{provider}' for model '{request.model}' after cache refresh.")
                 # This is a simplified retry; ideally, refactor to avoid code duplication
                 # For now, just redirecting to the start of the function or raising error
                 # To prevent potential infinite loop on persistent error, just raise for now
                 raise HTTPException(status_code=404, detail=f"Model '{request.model}' found after refresh, but retry logic needs full implementation. Please try request again.")
            
            raise HTTPException(status_code=404, detail=f"Model '{request.model}' not found or provider unknown.")

    except HTTPException:
        raise # Re-raise HTTPExceptions directly
    except Exception as e:
        logger.exception(f"Unhandled error in chat_completions for model {request.model}: {e}")
        raise HTTPException(status_code=500, detail=f"An internal server error occurred: {str(e)}")