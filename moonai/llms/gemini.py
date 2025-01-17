from typing import List, Optional

from llama_index.core.llms import ChatMessage, ChatResponse, CompletionResponse
from llama_index.llms.gemini import Gemini


class GeminiLLM:
    """Wrapper class for Gemini LLM using LlamaIndex."""

    def __init__(
        self,
        model: str = "models/gemini-1.5-pro",
        temperature: float = 0.7,
    ):
        """Initialize Gemini LLM.

        Args:
            model: The Gemini model to use
            api_key: Optional Google API key (uses GOOGLE_API_KEY env var if not provided)
            temperature: Controls randomness in responses (0.0 to 1.0)
        """
        self.llm = Gemini(
            model=model,
            temperature=temperature,
        )

    def complete(self, prompt: str) -> CompletionResponse:
        """Generate a completion for a given prompt.

        Args:
            prompt: The input text prompt

        Returns:
            CompletionResponse containing the generated text
        """
        return self.llm.complete(prompt)

    def chat(self, messages: List[ChatMessage]) -> ChatResponse:
        """Have a chat conversation with the model.

        Args:
            messages: List of ChatMessage objects containing the conversation history

        Returns:
            ChatResponse containing the model's reply
        """
        return self.llm.chat(messages)

    async def acomplete(self, prompt: str) -> CompletionResponse:
        """Async version of complete()."""
        return await self.llm.acomplete(prompt)

    async def achat(self, messages: List[ChatMessage]) -> ChatResponse:
        """Async version of chat()."""
        return await self.llm.achat(messages)

    async def stream_chat(self, messages: List[ChatMessage]):
        """Stream a chat conversation with the model.

        Args:
            messages: List of ChatMessage objects containing the conversation history

        Yields:
            ChatResponse chunks containing the model's reply
        """
        # Get the generator from the underlying LLM
        generator = self.llm.stream_chat(messages)

        # Convert regular generator to async generator
        for chunk in generator:
            yield chunk
