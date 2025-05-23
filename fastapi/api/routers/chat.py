import logging
import time
from uuid import uuid4
import openai # Added import for openai

from fastapi import APIRouter, HTTPException
from moonmind.factories.google_factory import get_google_model
from moonmind.factories.openai_factory import get_openai_model # Added import for get_openai_model
from moonmind.schemas.chat_models import \
    ChatCompletionRequest
from moonmind.schemas.chat_models import (ChatCompletionResponse, Choice,
                                          ChoiceMessage, Usage)
from moonmind.config.settings import settings # Import settings to check API key presence

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/v1/chat/completions", response_model=ChatCompletionResponse)
async def chat_completions(request: ChatCompletionRequest):
    try:
        model_id = request.model.lower() # Use lower for case-insensitive matching
        is_openai_model = model_id.startswith("gpt-") or "openai" in model_id # Simple check

        if is_openai_model:
            if not settings.openai.openai_api_key:
                raise HTTPException(status_code=400, detail="OpenAI API key not configured.")
            
            openai_model_name = get_openai_model(request.model)
            logger.info(f"Using OpenAI model: {openai_model_name}")

            # Prepare messages for OpenAI
            openai_messages = [{"role": msg.role, "content": msg.content} for msg in request.messages]

            try:
                # Ensure API key is set for the openai client library for this call
                openai.api_key = settings.openai.openai_api_key
                openai_response = await openai.ChatCompletion.acreate( # Use acreate for async
                    model=openai_model_name,
                    messages=openai_messages,
                    max_tokens=request.max_tokens,
                    temperature=request.temperature,
                    # Add other parameters from request if needed (e.g., top_p, n, stream)
                )
            except Exception as e:
                logger.error(f"Error calling OpenAI API: {e}")
                raise HTTPException(status_code=500, detail=f"OpenAI API error: {str(e)}")

            if not openai_response.choices:
                raise HTTPException(status_code=500, detail="Invalid response from OpenAI API: No choices returned.")

            ai_message_content = openai_response.choices[0].message.content
            
            # Extract usage data from OpenAI response
            usage_data = openai_response.usage
            usage = Usage(
                prompt_tokens=usage_data.prompt_tokens,
                completion_tokens=usage_data.completion_tokens,
                total_tokens=usage_data.total_tokens,
            )

            response = ChatCompletionResponse(
                id=openai_response.id,
                object="chat.completion",
                created=openai_response.created,
                model=openai_response.model, # Use the model name from OpenAI's response
                choices=[
                    Choice(
                        index=0,
                        message=ChoiceMessage(role="assistant", content=ai_message_content.strip()),
                        finish_reason=openai_response.choices[0].finish_reason,
                    )
                ],
                usage=usage,
            )
            return response

        else: # Assume Google Model
            if not settings.google.google_api_key:
                raise HTTPException(status_code=400, detail="Google API key not configured.")

            # Convert OpenAI-style messages to Google LLM format
            contents = []
            for msg in request.messages:
                if msg.role == "user":
                    gemini_role = "user"
                elif msg.role in {"system", "assistant"}:
                    gemini_role = "model" # Gemini uses "model" for system/assistant
                else:
                    # This case should ideally be caught by Pydantic validation of request.messages
                    logger.warning(f"Invalid message role '{msg.role}' encountered for Google model. Mapping to 'user'.")
                    gemini_role = "user" 

                contents.append({"role": gemini_role, "parts": [{"text": msg.content}]}) # Gemini expects "text" field for parts

            logger.info(f"Using Google model: {request.model}")
            logger.debug(f"Converted messages format for Google: {contents}")

            chat_model = get_google_model(request.model)
            try:
                # Ensure Google API key is configured for the factory if it relies on it implicitly
                # (Assuming get_google_model or the SDK call handles API key internally via settings)
                response_gemini = chat_model.generate_content(contents)
            except Exception as e:
                logger.error(f"Error generating content with Google Gemini: {e}")
                if "Please use a valid role" in str(e) or "role" in str(e).lower():
                    raise HTTPException(
                        status_code=400,
                        detail=f"Role error with Google Gemini API: {str(e)}. Ensure messages use 'user' or map 'system'/'assistant' to 'model'."
                    )
                raise HTTPException(status_code=500, detail=f"Google Gemini API error: {str(e)}")

            if not response_gemini.candidates or not response_gemini.candidates[0].content.parts:
                raise HTTPException(status_code=500, detail="Invalid response from Google Gemini: No content.")

            ai_message = response_gemini.candidates[0].content.parts[0].text

            # Construct the OpenAI-style response for Google model
            # Placeholder for actual token count from Google, if available.
            # Gemini API might provide token counts in metadata or via a separate API.
            # For now, using simple length-based estimation.
            prompt_tokens_est = sum(len(p["parts"][0]["text"].split()) for p in contents)
            completion_tokens_est = len(ai_message.split())
            
            response = ChatCompletionResponse(
                id=f"cmpl-goog-{uuid4().hex}",
                object="chat.completion",
                created=int(time.time()),
                model=request.model,
                choices=[
                    Choice(
                        index=0,
                        message=ChoiceMessage(role="assistant", content=ai_message.strip()),
                        finish_reason="stop", # Google's API might have a different way to indicate this
                    )
                ],
                usage=Usage(
                    prompt_tokens=prompt_tokens_est,
                    completion_tokens=completion_tokens_est,
                    total_tokens=prompt_tokens_est + completion_tokens_est,
                ),
            )
            return response

    except HTTPException: # Re-raise HTTPExceptions directly
        raise
    except Exception as e:
        logger.exception(f"Unhandled error in chat_completions: {e}")
        raise HTTPException(status_code=500, detail=f"An internal server error occurred: {str(e)}")