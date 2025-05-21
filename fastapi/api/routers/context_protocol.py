import logging
import time
from typing import Dict, List, Optional, Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, Depends
from pydantic import BaseModel, Field

from moonmind.factories.google_factory import get_google_model

router = APIRouter()
logger = logging.getLogger(__name__)

# Model Context Protocol Schema Definitions
class ContextMessage(BaseModel):
    role: str
    content: str
    name: Optional[str] = None

class ContextRequest(BaseModel):
    messages: List[ContextMessage]
    model: str
    max_tokens: Optional[int] = None
    temperature: Optional[float] = 0.7
    stream: Optional[bool] = False
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)

class ContextResponse(BaseModel):
    id: str
    content: str
    model: str
    created_at: int
    metadata: Dict[str, Any] = Field(default_factory=dict)

@router.post("/context", response_model=ContextResponse)
async def process_context(request: ContextRequest):
    try:
        # Convert Context Protocol messages to Google LLM format
        contents = []
        for msg in request.messages:
            if msg.role in {"system", "user", "assistant"}:
                contents.append({"role": msg.role, "parts": [msg.content]})
            else:
                raise HTTPException(status_code=400, detail=f"Invalid message role: {msg.role}")

        logger.info(f"Context Protocol request for model: {request.model}")

        # Fetch the Google LLM and generate content
        chat_model = get_google_model(request.model)
        
        # Set generation parameters if provided
        generation_config = {}
        if request.max_tokens:
            generation_config["max_output_tokens"] = request.max_tokens
        if request.temperature is not None:
            generation_config["temperature"] = request.temperature
            
        # Generate response
        response_gemini = chat_model.generate_content(
            contents,
            generation_config=generation_config
        )

        # Extract the response from the Google LLM
        if not response_gemini.candidates or not response_gemini.candidates[0].content.parts:
            raise HTTPException(status_code=500, detail="Invalid response from Google LLM")

        ai_message = response_gemini.candidates[0].content.parts[0].text

        # Construct the Context Protocol response
        response = ContextResponse(
            id=f"ctx-{uuid4().hex}",
            content=ai_message,
            model=request.model,
            created_at=int(time.time()),
            metadata={
                "usage": {
                    "prompt_tokens": len(contents),  # Placeholder
                    "completion_tokens": len(ai_message.split()),  # Estimate
                    "total_tokens": len(contents) + len(ai_message.split()),
                }
            }
        )

        return response

    except Exception as e:
        logger.exception(f"Error in context protocol: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"An internal error occurred: {str(e)}"
        )