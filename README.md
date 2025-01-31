# MoonMind

**MoonMind is a Retrieval-Augmented Generation (RAG) app built with LangChain, FastAPI, Open-WebUI, Qdrant, and docker-compose. The API adheres to the OpenAI architecture, so Open-WebUI can be swapped for any OpenAI-compatible UI. In a future release, it will suport easy swapping between different vector databases.**.

## Quick Start

TODO

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
- one chat model, e.g. gemini-2.0-flash-thinking-exp-01-21
- one embedding model, e.g. hf.co/tensorblock/gte-Qwen2-7B-instruct-GGUF:Q6_K
- one vector store, e.g. Qdrant
- multiple document loaders

Document indexers and routes are available, but if documents have already been indexed into the vector store, then they can be used as long as the same embeddings model is used MoonMind.

## Roadmap

The long-term goal of MoonMind is to provide strong defaults that support a one-click deployment, while also offering modularity and runtime configurability. Generally speaking, we prioritize customizability over extremely low latency due to our focus on internal company use cases over consumer applications.

In the future, we will support:
- multiple chat models available without redeployment
- multiple embedding models available without redeployment
- the ability to change many settings at runtime
- the ability to pass API credentials with requests
- the ability to choose a provider based on the model name and have multiple providers active
- the ability to enable or disable multiple model providers

We may add support for:
- multiple projects with different settings in one deployment, e.g. different collection names and vector store configurations

## Gemini

LangChain does not currently support the latest experimental Gemini models, so using Gemini requires using the Google provider.
