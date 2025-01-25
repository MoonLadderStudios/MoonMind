from fastapi import APIRouter, HTTPException
from moonmind.config.logging import logger
from moonmind.factories.google_factory import get_google_model
from moonmind.models.models import ChatCompletionRequest

router = APIRouter()
@router.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    try:
        contents = []
        for msg in request.messages:
            if msg.role == "system":
                contents.append({"role": "system", "parts": [msg.content]})
            elif msg.role == "user":
                contents.append({"role": "user", "parts": [msg.content]})
            elif msg.role == "assistant":
                contents.append({"role": "model", "parts": [msg.content]})
            else:
                raise HTTPException(status_code=400, detail=f"Invalid message role: {msg.role}")

        logger.info(f"Requested model was: {request.model}")

        chat_model = get_google_model(request.model)
        response_gemini = chat_model.generate_content(contents)
        ai_message = response_gemini.candidates[0].content.parts[0].text

        # Return a simple dictionary with just the text response
        return {"response": ai_message}

    except Exception as e:
        logger.error(f"Error in chat_completions: {e}")
        raise HTTPException(status_code=500, detail=str(e))