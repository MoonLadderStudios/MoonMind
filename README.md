# MoonMind

**MoonMind is a Retrieval-Augmented Generation (RAG) app built with LangChain, FastAPI, Open-WebUI, Qdrant, and docker-compose. The API adheres to the OpenAI architecture, so Open-WebUI can be swapped for any OpenAI-compatible UI. In a future release, it will suport easy swapping between different vector databases.**.

## Quick Start

TODO

## Design Principles
1. One-click deployment with smart defaults
2. Powerful runtime configurability
3. Modular and extensible architecture

## Configuration
Pydantic settings allow you to configure:
- one embedding model, e.g. hf.co/tensorblock/gte-Qwen2-7B-instruct-GGUF:Q6_K
- one vector store, e.g. Qdrant
- multiple chat models, e.g. gemini-2.0-flash-thinking-exp-01-21, gpt-4o
- multiple document loaders, e.g. Confluence, Google Drive, GitHub, etc.

Document indexers and routes are available, but if documents have already been indexed into the vector store, then they can be used as long as the same embeddings model is used MoonMind.

## Document Loaders

This section describes the available document loaders and how to use their respective API endpoints.

### Confluence Loader

The Confluence loader ingests documents from a specified Confluence space or specific page IDs.

**Endpoint:** `POST /documents/confluence/load`

**Request Body:**

*   `space_key` (string, mandatory): The key of the Confluence space to load documents from.
*   `page_ids` (array of strings, optional, default: `null`): A list of specific Confluence page IDs to load. If provided, only these pages will be fetched.
*   `max_num_results` (integer, optional, default: `100`): The maximum number of results to fetch per batch when loading by `space_key`.

**Example Request (Space Key):**
```json
{
    "space_key": "MYSPACEKEY",
    "max_num_results": 50
}
```

**Example Request (Page IDs):**
```json
{
    "space_key": "ANYKEY", // Still required by model, but ignored if page_ids are present
    "page_ids": ["12345", "67890"]
}
```

**Success Response:**
```json
{
    "status": "success",
    "message": "Successfully loaded 75 nodes from Confluence space MYSPACEKEY.", // Or from X specified page IDs.
    "total_nodes_indexed": 75
}
```

**Error Handling:**
The endpoint returns appropriate HTTP status codes for errors such as Confluence being disabled, authentication issues, or space/page not found.


### GitHub Repository Loader

This loader allows you to ingest documents directly from a GitHub repository.

**Endpoint:** `POST /documents/github/load`

**Request Body:**

The request body should be a JSON object with the following fields:

*   `repo` (string, mandatory): The full path to the repository in the format `"owner_username/repository_name"`.
*   `branch` (string, optional, default: `"main"`): The specific branch of the repository to load documents from.
*   `filter_extensions` (array of strings, optional, default: `null`): A list of file extensions to specifically include in the loading process (e.g., `[".py", ".md", ".java"]`). If omitted or `null`, all files encountered will be processed.
*   `github_token` (string, optional, default: `null`): A GitHub Personal Access Token (PAT). This is required for accessing private repositories. It's also recommended for public repositories to avoid potential rate limiting by GitHub.

**Security Note:** The `github_token` grants access to your GitHub repositories. Ensure it's handled securely. It's best practice to use a token with the minimum necessary permissions (e.g., read-only access to the specific repositories you intend to load).

**Example Request:**

```json
{
    "repo": "my-org/my-awesome-project",
    "branch": "feature/new-docs",
    "filter_extensions": [".md", ".txt"],
    "github_token": "ghp_YourGitHubPersonalAccessTokenIfPrivateOrForRateLimits"
}
```

**Success Response:**

On successful loading, the API will return a JSON object similar to this:

```json
{
    "status": "success",
    "message": "Successfully loaded 153 nodes from GitHub repository my-org/my-awesome-project on branch feature/new-docs",
    "total_nodes_indexed": 153,
    "repository": "my-org/my-awesome-project",
    "branch": "feature/new-docs"
}
```

**Error Handling:**

The endpoint will return appropriate HTTP status codes and error messages for issues such as:
*   Invalid `repo` format.
*   Missing or invalid `github_token` for private repositories.
*   Repository not found or inaccessible.
*   Other errors during document processing.


### Google Drive Loader

This loader enables you to ingest documents from Google Drive, either from a specified folder or by listing individual file IDs.

**Endpoint:** `POST /documents/google_drive/load`

**Request Body:**

The request body should be a JSON object with the following fields:

*   `folder_id` (string, optional): The ID of the Google Drive folder from which to load documents.
*   `file_ids` (array of strings, optional): A list of specific Google Drive file IDs to load.
    *   *Note: You must provide either `folder_id` or `file_ids`.*
*   `recursive` (boolean, optional, default: `False`): This field is available in the request. The underlying LlamaIndex Google Drive reader, when given a `folder_id`, typically processes all files within that folder.
*   `service_account_key_path` (string, optional, default: `null`): The server-side path to your Google Cloud service account JSON key file.

**Authentication:**

To access your Google Drive files, the application needs Google Cloud credentials:
1.  **Service Account Key Path:** You can provide the full path to a service account key JSON file using the `service_account_key_path` field in your request. Ensure this file is accessible on the server where the application is running.
2.  **Application Default Credentials (ADC):** If `service_account_key_path` is not provided in the request, the application will attempt to use ADC. This typically involves setting the `GOOGLE_APPLICATION_CREDENTIALS` environment variable on the server to point to your service account key file. Refer to Google Cloud documentation for details on setting up ADC.
3.  **Default Server Configuration:** Alternatively, a default service account key path can be configured in the application's settings (`settings.google.google_account_file`) by the server administrator.

**Example Requests:**

*Loading from a folder (using ADC or a server-configured default key):*
```json
{
    "folder_id": "1aBcDeFgHiJkLmNoPqRsTuVwXyZ_12345"
}
```

*Loading specific files using a provided service account key path:*
```json
{
    "file_ids": ["1_abcdefgHIJKLMNOPQRSTUVWXYZabcdefg", "1_anotherFileIDJKLMNOPQRSTUVW"],
    "service_account_key_path": "/etc/gcp_keys/my_project_sa_key.json"
}
```

**Success Response:**

A successful response will include the number of nodes indexed:
```json
{
    "status": "success",
    "message": "Successfully loaded 75 nodes from Google Drive (folder ID 1aBcDeFgHiJkLmNoPqRsTuVwXyZ_12345).",
    "total_nodes_indexed": 75,
    "folder_id": "1aBcDeFgHiJkLmNoPqRsTuVwXyZ_12345",
    "file_ids": null
}
```

**Error Handling:**
The API will return appropriate error messages for issues like missing `folder_id`/`file_ids`, authentication problems, or errors from the Google Drive API.


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
