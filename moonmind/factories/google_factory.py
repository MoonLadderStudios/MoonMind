import logging

import google.generativeai as genai

from moonmind.config.settings import settings

logger = logging.getLogger(__name__)


def list_google_models(api_key: str | None = None):
    key_to_use = api_key if api_key else settings.google.google_api_key
    if not key_to_use:
        logger.warning(
            "Google models are not available because the API key is not set in settings"
        )
        return []

    genai.configure(api_key=key_to_use)
    return genai.list_models()


def get_google_model(model_name: str = None, api_key: str = None):
    if not model_name:
        model_name = settings.google.google_chat_model

    key_to_use = api_key if api_key else settings.google.google_api_key

    if key_to_use:
        # генai.configure is global. This means concurrent requests from different users
        # with different keys will interfere if not handled carefully.
        # For a truly isolated per-request key, the SDK would need to support
        # passing the key directly to GenerativeModel or request methods,
        # or creating client instances with specific keys.
        # As of current google-generativeai SDK, configure is the primary way.
        # This is a known limitation for multi-tenant applications needing distinct keys per call.
        # A potential workaround could involve a lock when configuring and making the call,
        # or exploring if the SDK offers non-global configuration options not immediately obvious.
        # For now, we'll proceed with genai.configure, acknowledging this.
        genai.configure(api_key=key_to_use)
    else:
        logger.warning(
            "Google API key is not provided via argument or settings. "
            "Direct genai model initialization might fail if GOOGLE_API_KEY env var is not set."
        )

    model = genai.GenerativeModel(model_name=model_name)

    return model
