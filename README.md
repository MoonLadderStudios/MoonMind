# MoonMind

**MoonMind is a Retrieval-Augmented Generation (RAG) app built with LangChain, FastAPI, Open-WebUI, Qdrant, and docker-compose. The API adheres to the OpenAI architecture, so Open-WebUI can be swapped for any OpenAI-compatible UI. In a future release, it will suport easy swapping between different vector databases.**.

## Microservices

MoonMind uses a modular microservices architecture with the following containers:

- **API**: A FastAPI service that provides an OpenAI-compatible REST API for Retrieval-Augmented Generation
- **UI**: An Open-WebUI container that provides a UI for Retrieval-Augmented Generation
- **Qdrant**: A Qdrant container that provides a vector database
- **Ollama**: An Ollama container that handles local LLM inference (optional)

It is possible to run inference with Ollama, with third-party AI providers, or with a hybrid approach (e.g. local embedding models with cloud LLM inference).

If using the default Ollama container, an NVIDIA GPU with appropriate drivers is required.

## API Architecture

The API container is an OpenAI-compatible REST API, powered by FastAPI andLangChain, that employs Dependency Injection with abstract interfaces to enable modular service selection.

Pydantic settings allow you to configure:
- one chat model, e.g. gemini-2.O-flash-thinking-exp-01-21
- one embedding model, e.g. hf.co/tensorblock/gte-Qwen2-7B-instruct-GGUF:Q6_K
- one vector store, e.g. Qdrant
- multiple document loaders

In the future, we intend to add limited agent functionality and include configuration for:
- multiple retrievers
- multiple tools

## Multiple Models

TODO: We can choose a provider based on the model name and have multiple providers active.

TODO: Each model provider can be enabled or disabled.

## Gemini

LangChain does not currently support the latest experimental Gemini models, so using Gemini requires using the Google provider.