from llama_index.core.llms.llm import LLM
from llama_index.llms.anthropic import Anthropic

from moonmind.config.settings import settings


class AnthropicFactory:
    """
    Factory class for creating Anthropic models.
    """

    @staticmethod
    def create_anthropic_model(api_key: str = None, model_name: str = None) -> LLM:
        """
        Creates an Anthropic model instance.

        Args:
            api_key (str, optional): The API key to use. Defaults to None (uses settings).
            model_name (str, optional): The model name to use. Defaults to None (uses settings).


        Returns:
            LLM: An instance of the Anthropic model.

        Raises:
            ValueError: If the Anthropic API key is not configured (and not provided).
        """
        key_to_use = api_key if api_key else settings.anthropic.anthropic_api_key
        name_to_use = (
            model_name if model_name else settings.anthropic.anthropic_chat_model
        )

        if not key_to_use:
            raise ValueError("Anthropic API key not configured and not provided.")

        return Anthropic(
            api_key=key_to_use,
            model=name_to_use,
        )
