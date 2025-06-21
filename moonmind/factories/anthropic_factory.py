from llama_index.llms.anthropic import Anthropic
from llama_index.core.llms.llm import LLM

from moonmind.config.settings import settings


class AnthropicFactory:
    """
    Factory class for creating Anthropic models.
    """

    @staticmethod
    def create_anthropic_model() -> LLM:
        """
        Creates an Anthropic model instance.

        Returns:
            LLM: An instance of the Anthropic model.

        Raises:
            ValueError: If the Anthropic API key is not configured.
        """
        if not settings.anthropic.anthropic_api_key:
            raise ValueError("Anthropic API key not configured.")

        return Anthropic(
            api_key=settings.anthropic.anthropic_api_key,
            model=settings.anthropic.anthropic_chat_model,
        )
