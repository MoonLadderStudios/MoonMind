from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class Message(BaseModel):
    role: str = Field(..., description="The role of the message (system, user, assistant)")
    content: str = Field(..., description="The content of the message")

class ChatCompletionRequest(BaseModel):
    model: str = Field("gemini-1.5-pro", description="The model to use (currently defaults to Gemini Pro)") # You can expand model mapping later
    messages: List[Message] = Field(..., description="A list of messages describing the conversation so far.")
    temperature: Optional[float] = Field(1.0, description="Sampling temperature, between 0 and 2. Higher values like 0.8 will make the output more random, while lower values like 0.2 will make it more focused and deterministic.")
    max_tokens: Optional[int] = Field(None, description="The maximum number of tokens to generate in the chat completion.")

class ChoiceMessage(BaseModel):
    role: str = "assistant"
    content: str

class Choice(BaseModel):
    index: int = 0
    message: ChoiceMessage
    finish_reason: str = "stop" # Or "length", "content_filter", etc.

class Usage(BaseModel): # Optional Usage Model
    prompt_tokens: int = Optional[int]
    completion_tokens: int = Optional[int]
    total_tokens: int = Optional[int]

class ChatCompletionResponse(BaseModel):
    id: str = "cmpl-xxxxxxxxxxxxxxxxxxxxxxx"  # Example ID, you can generate a UUID
    object: str = "chat.completion"
    created: int = 1678909000  # Example timestamp, use time.time() in real app
    model: str = "gemini-1.5-pro" # Echo back the model or the actual Gemini model used
    choices: List[Choice]
    usage: Optional[Usage] = None # Optional usage information