"""Centralized LLM Provider Configuration."""
from typing import TypedDict

class ProviderConfig(TypedDict):
    base_url: str
    auth_header_key: str
    auth_header_format: str

# Centralized mapping of supported proxy providers
PROVIDERS: dict[str, ProviderConfig] = {
    "anthropic": {
        "base_url": "https://api.anthropic.com",
        "auth_header_key": "x-api-key",
        "auth_header_format": "{token}",
    },
    "openai": {
        "base_url": "https://api.openai.com",
        "auth_header_key": "authorization",
        "auth_header_format": "Bearer {token}",
    },
    "minimax": {
        "base_url": "https://api.minimax.io",
        "auth_header_key": "authorization",
        "auth_header_format": "Bearer {token}",
    },
}
