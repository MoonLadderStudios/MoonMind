from langchain_google_genai import ChatGoogleGenerativeAI

from ..config.settings import AppSettings


def build_chat_provider(settings: AppSettings):
    if settings.chat_provider == "google":
        return ChatGoogleGenerativeAI(
            model=settings.google.google_chat_model
        )
    else:
        raise ValueError(f"Unsupported chat provider: {settings.chat_provider}")

