import time
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from moonmind.config.logging import logger
from moonmind.factories.google_factory import get_google_model
from moonmind.models.models import (ChatCompletionRequest,
                                    ChatCompletionResponse, Choice,
                                    ChoiceMessage, Usage)

router = APIRouter()

@router.post("/v1/chat/completions", response_model=ChatCompletionResponse)
async def chat_completions(request: ChatCompletionRequest):
    try:
        # Convert OpenAI-style messages to Google LLM format
        contents = []
        for msg in request.messages:
            if msg.role in {"system", "user", "assistant"}:
                contents.append({"role": msg.role, "parts": [msg.content]})
            else:
                raise HTTPException(status_code=400, detail=f"Invalid message role: {msg.role}")

        logger.info(f"Requested model was: {request.model}")

        # Fetch the Google LLM and generate content
        chat_model = get_google_model(request.model)
        response_gemini = chat_model.generate_content(contents)

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
        logger.error(f"Error in chat_completions: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"An internal error occurred: {str(e)}"
        )