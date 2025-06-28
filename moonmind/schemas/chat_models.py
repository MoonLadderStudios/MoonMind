from typing import List, Optional
from pydantic import BaseModel, Field


class Message(BaseModel):
    role: str = Field(
        ..., description="The role of the message (system, user, assistant)"
    )
    content: str = Field(..., description="The content of the message")


class ChatCompletionRequest(BaseModel):
    model: Optional[str] = Field(
        None,
        description="The model to use. If not specified, the default model will be used.",
    )
    messages: List[Message] = Field(
        ..., description="A list of messages describing the conversation so far."
    )
    temperature: Optional[float] = Field(
        1.0, description="Sampling temperature, between 0 and 2."
    )
    max_tokens: Optional[int] = Field(
        None, description="The maximum number of tokens to generate."
    )


class ChoiceMessage(BaseModel):
    role: str = "assistant"
    content: str


class Choice(BaseModel):
    index: int = 0
    message: ChoiceMessage
    finish_reason: str = "stop"


class Usage(BaseModel):
    prompt_tokens: Optional[int] = None  # Made fields optional as per original
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None  # Made field optional


class ChatCompletionResponse(BaseModel):
    id: str = "cmpl-xxxxxxxxxxxxxxxxxxxxxxx"
    object: str = "chat.completion"
    created: int = 1678909000  # Example, should be dynamic
    model: str = "gemini-1.5-pro"
    choices: List[Choice]
    usage: Optional[Usage] = None

from enum import Enum

class ModelProvider(Enum):
    GOOGLE = "google"
    OPENAI = "openai"
    OLLAMA = "ollama"
    ANTHROPIC = "anthropic"
