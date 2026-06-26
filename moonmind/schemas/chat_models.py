from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field

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
    cost_estimate_usd: Optional[float] = None
    pricing_source: Optional[str] = None

class ChatCompletionResponse(BaseModel):
    id: str = "cmpl-xxxxxxxxxxxxxxxxxxxxxxx"
    object: str = "chat.completion"
    created: int = 1678909000  # Example, should be dynamic
    model: str = "gemini-1.5-pro"
    choices: List[Choice]
    usage: Optional[Usage] = None

class ResponseCreateRequest(BaseModel):
    """Supported subset of OpenAI's Responses API create request."""

    model_config = ConfigDict(populate_by_name=True)

    model: Optional[str] = Field(
        None,
        description="The model to use. If omitted, MoonMind's default chat model is used.",
    )
    input: str | list[Any] = Field(
        ...,
        description="Text input or Responses-style message input items.",
    )
    instructions: Optional[str] = Field(
        None,
        description="Optional system/developer instructions prepended to the input.",
    )
    temperature: Optional[float] = Field(
        1.0,
        description="Sampling temperature, between 0 and 2.",
    )
    max_output_tokens: Optional[int] = Field(
        None,
        alias="max_output_tokens",
        description="Maximum output tokens to generate.",
    )
    stream: bool = Field(
        False,
        description="Streaming is not supported by MoonMind's compatibility route.",
    )
    tools: Optional[list[Any]] = Field(
        None,
        description="Tool use is not supported by MoonMind's compatibility route.",
    )
    conversation: Optional[Any] = Field(
        None,
        description="Conversation state is not supported by MoonMind's compatibility route.",
    )
    previous_response_id: Optional[str] = Field(
        None,
        alias="previous_response_id",
        description="Previous response state is not supported by MoonMind's compatibility route.",
    )
    background: bool = Field(
        False,
        description="Background responses are not supported by MoonMind's compatibility route.",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

class ResponseOutputText(BaseModel):
    type: str = "output_text"
    text: str
    annotations: list[Any] = Field(default_factory=list)

class ResponseOutputMessage(BaseModel):
    id: str
    type: str = "message"
    status: str = "completed"
    role: str = "assistant"
    content: list[ResponseOutputText]

class ResponseUsage(BaseModel):
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    cost_estimate_usd: Optional[float] = None
    pricing_source: Optional[str] = None

class ResponseCreateResponse(BaseModel):
    id: str
    object: str = "response"
    created_at: int
    status: str = "completed"
    model: str
    output: list[ResponseOutputMessage]
    output_text: str
    usage: Optional[ResponseUsage] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

from enum import Enum

class ModelProvider(Enum):
    GOOGLE = "google"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
