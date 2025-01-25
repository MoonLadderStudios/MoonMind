import google.generativeai as genai

from moonmind.config.settings import settings


def get_google_model(model_name: str):
    genai.configure(api_key=settings.google.google_api_key)
    model = genai.GenerativeModel(model_name)
    return model