import logging
import openai
from moonmind.config.settings import settings

logger = logging.getLogger(__name__)

def list_openai_models():
    if not settings.openai.openai_api_key:
        logger.warning("OpenAI models are not available because the API key is not set in settings")
        return []

    openai.api_key = settings.openai.openai_api_key
    try:
        models = openai.Model.list()
        # Filter for models that support chat completions, or adjust as needed
        return [model for model in models.data if "chat" in model.id or "gpt" in model.id]
    except Exception as e:
        logger.error(f"Error listing OpenAI models: {e}")
        return []

def get_openai_model(model_name: str = None):
    if not model_name:
        model_name = settings.openai.openai_chat_model
    if settings.openai.openai_api_key:
        openai.api_key = settings.openai.openai_api_key
    else:
        logger.warning("OpenAI API key is not set in settings. OpenAI model initialization might fail.")
    
    # Note: Unlike Google's library, OpenAI's library doesn't have a model object initialization.
    # The model is specified when making API calls (e.g., openai.ChatCompletion.create).
    # This function will return the model name, which is then used in API requests.
    # If you need to wrap or configure it further, this is the place.
    return model_name

# Example of how to adjust settings if they are not already structured for openai
# This is a placeholder, actual settings structure might differ.
# Ensure your settings (e.g., in a Pydantic model) include an OpenAI section like:
# class OpenAISettings(BaseModel):
#     openai_api_key: Optional[str] = None
#     openai_chat_model: str = "gpt-3.5-turbo"
#
# class Settings(BaseSettings):
#     # ... other settings
#     openai: OpenAISettings = OpenAISettings()
