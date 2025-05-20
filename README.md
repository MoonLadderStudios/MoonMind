# MoonMind

**MoonMind is a Retrieval-Augmented Generation (RAG) app built with LangChain, FastAPI, Open-WebUI, Qdrant, and docker-compose. The API adheres to the OpenAI architecture, so Open-WebUI can be swapped for any OpenAI-compatible UI. In a future release, it will suport easy swapping between different vector databases.**.

## Quick Start

TODO

## Configuration
Pydantic settings allow you to configure:
- one embedding model, e.g. hf.co/tensorblock/gte-Qwen2-7B-instruct-GGUF:Q6_K
- one vector store, e.g. Qdrant
- multiple chat models, e.g. gemini-2.0-flash-thinking-exp-01-21, gpt-4o
- multiple document loaders, e.g. Confluence, Google Drive, GitHub, etc.

Document indexers and routes are available, but if documents have already been indexed into the vector store, then they can be used as long as the same embeddings model is used MoonMind.

## Microservices

MoonMind uses a modular microservices architecture with the following containers:

- **API**: A FastAPI service that provides an OpenAI-compatible REST API for Retrieval-Augmented Generation
- **UI**: An Open-WebUI container that provides a UI for Retrieval-Augmented Generation
- **Qdrant**: A Qdrant container that provides a vector database
- **Ollama**: An Ollama container that handles local LLM inference (optional)

It is possible to run inference with Ollama, with third-party AI providers, or with a hybrid approach (e.g. local embedding models with cloud LLM inference).

If using the default Ollama container, an NVIDIA GPU with appropriate drivers is required.

The API container is an OpenAI-compatible REST API, powered by FastAPI andLangChain, employing Dependency Injection with abstract interfaces to enable modular service selection.

## Component Definitions

TODO...

Embedding model:
Vector Store:
Storage Context:
Service Context:

## Roadmap

The long-term goal of MoonMind is to provide strong defaults that support a one-click deployment, while also offering modularity and runtime configurability. Generally speaking, we prioritize customizability over extremely low latency due to our focus on internal company use cases over consumer applications.

In the future, we will support:
- multiple chat models available without redeployment
- multiple embedding models available without redeployment, e.g. a code embedding model and a general purpose embedding model
- the ability to change many settings at runtime
- the ability to pass API credentials with requests
- the ability to choose a provider based on the model name and have multiple providers active
- the ability to enable or disable multiple model providers

We may add support for:
- multiple projects with different settings in one deployment, e.g. different collection names and vector store configurations

TODO: Add a notion of a collection which tracks the vector store and embedder. Once created, when you choose a collection, the vector store and embedder are selected for you.

## Gemini

LangChain does not currently support the latest experimental Gemini models, so using Gemini requires using the Google provider.

## Running Tests

### Unit Tests

To run unit tests:
```bash
pytest tests/ # Or specific paths like tests/indexers, tests/api
```

### Confluence Integration Tests

These tests verify the end-to-end functionality of loading documents from a real Confluence space into the Qdrant vector database and then querying Qdrant.

**Prerequisites:**
*   A running Confluence instance accessible with the credentials provided in the `.env` file.
*   A running Qdrant instance, configured as specified in the `.env` file.

**Setup:**
1.  Create a `.env` file in the root of the project if you haven't already.
2.  Add the following environment variables to your `.env` file, replacing placeholder values with your actual Confluence and Qdrant details:

    ```env
    CONFLUENCE_URL=https://your-confluence-domain.atlassian.net/wiki
    CONFLUENCE_USERNAME=your_email@example.com
    CONFLUENCE_API_KEY=your_confluence_api_token
    TEST_CONFLUENCE_SPACE_KEY=YOUR_TEST_SPACE_KEY  # A space with a few test documents that the provided user can access
    
    QDRANT_HOST=localhost
    QDRANT_PORT=6333
    QDRANT_COLLECTION_NAME=moonmind_documents # Ensure this matches your application's Qdrant collection name (default in tests)
    ```
    *Note: `QDRANT_HOST`, `QDRANT_PORT`, and `QDRANT_COLLECTION_NAME` should match the settings your application uses for the Qdrant instance being tested against. The default collection name in the integration test setup is `moonmind_documents`.*

**Running the Tests:**
To execute the Confluence integration tests, run the following command from the project root:
```bash
pytest tests/integration/test_confluence_e2e.py
```
The tests will be skipped if the required Confluence environment variables (`CONFLUENCE_URL`, `CONFLUENCE_USERNAME`, `CONFLUENCE_API_KEY`, `TEST_CONFLUENCE_SPACE_KEY`) are not found in the `.env` file.
