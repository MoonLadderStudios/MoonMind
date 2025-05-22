import logging
import time
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from moonmind.factories.google_factory import get_google_model
from moonmind.schemas.chat_models import (ChatCompletionRequest, # Updated import path
                                         ChatCompletionResponse, Choice,
                                         ChoiceMessage, Usage)

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/v1/chat/completions", response_model=ChatCompletionResponse)
async def chat_completions(request: ChatCompletionRequest):
    try:
        # Convert OpenAI-style messages to Google LLM format
        contents = []
        for msg in request.messages:
            # Map OpenAI roles to Gemini roles (Gemini only accepts "user" and "model" roles)
            if msg.role == "user":
                gemini_role = "user"
            elif msg.role in {"system", "assistant"}:
                gemini_role = "model"
            else:
                raise HTTPException(status_code=400, detail=f"Invalid message role: {msg.role}")
            
            contents.append({"role": gemini_role, "parts": [msg.content]})

        logger.info(f"Requested model was: {request.model}")
        logger.debug(f"Converted messages format: {contents}")

        # Fetch the Google LLM and generate content
        chat_model = get_google_model(request.model)
        try:
            response_gemini = chat_model.generate_content(contents)
        except Exception as e:
            logger.error(f"Error generating content with Gemini: {e}")
            # Check if this is a role-related error and provide a more helpful message
            if "Please use a valid role" in str(e):
                raise HTTPException(
                    status_code=400,
                    detail=f"Role error with Gemini API: {str(e)}. Note that Gemini only accepts 'user' and 'model' roles."
                )
            raise

        # Extract the response from the Google LLM
        if not response_gemini.candidates or not response_gemini.candidates[0].content.parts:
            raise HTTPException(status_code=500, detail="Invalid response from Google LLM")

        ai_message = response_gemini.candidates[0].content.parts[0].text

        # Construct the OpenAI-style response
        response = ChatCompletionResponse(
            id=f"cmpl-{uuid4().hex}",  # Generate a unique ID
            object="chat.completion",
            created=int(time.time()),
            model=request.model,  # Echo back the requested model
            choices=[
                Choice(
                    index=0,
                    message=ChoiceMessage(role="assistant", content=ai_message),
                    finish_reason="stop",  # You could map this from the Google LLM response
                )
            ],
            usage=Usage(
                prompt_tokens=len(contents),  # This is a placeholder; use actual token count if available
                completion_tokens=len(ai_message.split()),  # Estimate token count from generated message
                total_tokens=len(contents) + len(ai_message.split()),
            ),
        )

        return response

    except Exception as e:
        logger.exception(f"Error in chat_completions: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"An internal error occurred: {str(e)}"
        )