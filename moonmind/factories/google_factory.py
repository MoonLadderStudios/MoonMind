import logging

import google.generativeai as genai

from moonmind.config.settings import settings

logger = logging.getLogger(__name__)


def list_google_models():
    if not settings.google.google_api_key:
        logger.warning(
            "Google models are not available because the API key is not set in settings"
        )
        return []

    genai.configure(api_key=settings.google.google_api_key)
    return genai.list_models()


def get_google_model(model_name: str = None):
    if not model_name:
        model_name = settings.google.google_chat_model
    if settings.google.google_api_key:
        genai.configure(api_key=settings.google.google_api_key)
    else:
        logger.warning(
            "Google API key is not set in settings. Direct genai model initialization might fail if GOOGLE_API_KEY env var is not set."
        )

    model = genai.GenerativeModel(model_name=model_name)

    return model
