from moonmind.config.settings import settings

print(f"Default Chat Provider: {settings.default_chat_provider}")
print(f"Default Chat Model: {settings.get_default_chat_model()}")

# Print specific default models for enabled providers to help with verification
if settings.is_provider_enabled("google"):
    print(f"Google Default Chat Model: {settings.google.google_chat_model}")
if settings.is_provider_enabled("openai"):
    print(f"OpenAI Default Chat Model: {settings.openai.openai_chat_model}")
if settings.is_provider_enabled("ollama"):
    print(f"Ollama Default Chat Model: {settings.ollama.ollama_chat_model}")
if settings.is_provider_enabled("anthropic"):
    print(f"Anthropic Default Chat Model: {settings.anthropic.anthropic_chat_model}")
