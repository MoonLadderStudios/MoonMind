# MoonMind

**MoonMind makes it easy to enhance AI chat with personal or proprietary data. It is a Retrieval-Augmented Generation (RAG) app built with Llama Index, FastAPI, Open-WebUI, Qdrant, and docker-compose. The API adheres to the OpenAI architecture, so Open-WebUI can be swapped for any OpenAI-compatible UI. It can also be exposed using MCP to other tools like agents.**

## Quick Start

This section guides you through a one-click deployment of MoonMind using Docker Compose. This setup will start the necessary services: the User Interface (Open-WebUI), the API backend, and the Qdrant vector database.

**Prerequisites:**

*   **Docker:** Ensure Docker is installed and running on your system. You can download it from [Docker's official website](https://www.docker.com/products/docker-desktop).
*   **Docker Compose:** Docker Compose is included with most Docker Desktop installations. If not, follow the [official installation guide](https://docs.docker.com/compose/install/).
*   **Environment File:** Create a `.env` file in the root of the project by copying the `.env-template` file:
    ```bash
    cp .env-template .env
    ```
    Review the `.env` file and fill in any necessary API keys or configuration values if you plan to use services like OpenAI, Google, Confluence, etc. For a basic local setup, default values might suffice for most fields other than an LLM provider API key.

**Running MoonMind:**

1.  **Open a terminal** in the root directory of the MoonMind project.
2.  **Start the services** using the following command:
    ```bash
    docker-compose up -d
    ```
    The `-d` flag runs the containers in detached mode, meaning they will run in the background.

3.  **Accessing the UI:** Once the services are up and running (this might take a few minutes the first time as images are downloaded and built), you can access the Open-WebUI by navigating to `http://localhost:8080` in your web browser.

4.  **Manage API Keys:** When `AUTH_PROVIDER` is left as `disabled` (the default for local setups), any provider keys you place in `.env` are copied to the default user profile on startup. Visit `http://localhost:8080/settings` to view or change these values.
5.  **Initializing the Vector Database (Optional but Recommended):**
    If you want to load initial data into the Qdrant vector database (e.g., from local files or other sources configured in `config.toml`), you can trigger the initialization process.
    Set the `INIT_DATABASE` variable in your `.env` file to `true`:
    ```env
    INIT_DATABASE=true
    ```
    Then, restart your Docker Compose setup:
    ```bash
    docker-compose down && docker-compose up -d
    ```
    The `init-vector-db` service will run, attempt to load data, and then exit. You can check its logs using `docker-compose logs init-vector-db`. After initialization, you may want to set `INIT_DATABASE=false` again to prevent re-initialization on subsequent restarts.

**Stopping MoonMind:**

To stop all running services, execute the following command in the project root:
```bash
docker-compose down
```

This setup uses the main `docker-compose.yaml` file, which is configured for a production-like deployment with the Qdrant vector store. For development purposes, or if you need to use a different configuration (e.g., without Qdrant or with different services), you might use `docker-compose.dev.yaml` or other specific compose files.

## Development
MoonMind relies on `pre-commit` to enforce formatting and linting. Install the hooks after cloning:

```bash
pre-commit install
```

Attempting to commit with style violations will fail:

```bash
$ git commit -am "msg"
isort....................................................................Failed
```

## Design Principles
1. One-click deployment with smart defaults
2. Powerful runtime configurability
3. Modular and extensible architecture

## Configuration
Pydantic settings allow you to configure:
- one embedding model, e.g. hf.co/tensorblock/gte-Qwen2-7B-instruct-GGUF:Q6_K
- one vector store, e.g. Qdrant
- multiple chat models, e.g. Google's `gemini-pro`, `gemini-1.5-flash-latest`, and OpenAI's `gpt-3.5-turbo`, `gpt-4o`.
- multiple document loaders, e.g. Confluence, Google Drive, GitHub, etc.
- API keys for the respective providers (e.g., `GOOGLE_API_KEY`, `OPENAI_API_KEY`).

Document indexers and routes are available, but if documents have already been indexed into the vector store, then they can be used as long as the same embeddings model is used MoonMind.

### Ollama Model Configuration

If you are using the provided Ollama service for local LLM inference, you can control which model or models (chat and/or embedding) are loaded by default at startup.

The following environment variables in your `.env` file are used:

*   `OLLAMA_CHAT_MODEL`: Specifies the chat model. Defaults to `"devstral:24b"`.
*   `OLLAMA_EMBEDDING_MODEL`: Specifies the embedding model. Defaults to `"hf.co/tensorblock/gte-Qwen2-7B-instruct-GGUF:Q6_K"`.
*   `OLLAMA_MODES`: Determines which model(s) to load by default. This is a comma-separated string. Valid values are "chat", "embed", or "chat,embed". If not set, it defaults to "chat".

**Launching with Specific Models:**

You can specify which models to load at launch time using the `tools/ollama.ps1` script with its new switch parameters. This will override the `OLLAMA_MODES` value in your `.env` file for that specific run.

*   `-LoadChatModel`: Use this switch to load the chat model specified by `OLLAMA_CHAT_MODEL`.
*   `-LoadEmbeddingModel`: Use this switch to load the embedding model specified by `OLLAMA_EMBEDDING_MODEL`.

If neither switch is provided, the script defaults to loading only the chat model (equivalent to `OLLAMA_MODES="chat"`).

Examples:

*   To launch Ollama and load only the configured chat model:
    ```powershell
    .\tools\ollama.ps1 -LoadChatModel
    ```
    (or simply `.\tools\ollama.ps1` as this is the default if no switches are passed)

*   To launch Ollama and load only the configured embedding model:
    ```powershell
    .\tools\ollama.ps1 -LoadEmbeddingModel
    ```

*   To launch Ollama and load both the chat and embedding models:
    ```powershell
    .\tools\ollama.ps1 -LoadChatModel -LoadEmbeddingModel
    ```

The script will automatically attempt to pull the selected model(s) if not already available locally and then make them active within the Ollama server.

**Note on Resource Usage:** Loading multiple models simultaneously (e.g., both chat and embedding) will consume more system resources (CPU, RAM, VRAM). Ensure your system has adequate resources if you choose to load multiple models.

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

- **API**: A FastAPI service that provides:
  - An OpenAI-compatible REST API for Retrieval-Augmented Generation
  - A Model Context Protocol server for agent interactions
- **UI**: An Open-WebUI container that provides a UI for Retrieval-Augmented Generation
- **Qdrant**: A Qdrant container that provides a vector database
- **Ollama**: An Ollama container that handles local LLM inference (optional)

It is possible to run inference with Ollama, with third-party AI providers (like Google and OpenAI), or with a hybrid approach (e.g. local embedding models with cloud LLM inference).

If using the default Ollama container, an NVIDIA GPU with appropriate drivers is required.

The API container is powered by FastAPI and LangChain, employing Dependency Injection with abstract interfaces to enable modular service selection. It supports both OpenAI-compatible endpoints and the Model Context Protocol, making it versatile for different client applications and AI agents.

## Apps

Apps are higher-level workflows built on top of MoonMind. They can be invoked from the CLI or other tools using the Model Context Protocol. When started with a manifest file, the application uses `ManifestLoader` to read reader definitions and defaults from YAML, returning a new settings instance that merges the manifest values with the environment configuration.

## Running the VLLM Service

This project includes a Docker Compose configuration to run a VLLM (Very Large Language Model) service with GPU acceleration, providing an OpenAI-compatible API endpoint.

### Prerequisites

- NVIDIA GPU drivers installed on your host machine.
- NVIDIA Container Toolkit installed to enable GPU access for Docker containers.
- Docker and Docker Compose.

### Setup

1.  **Environment Configuration:**
    You can customize the VLLM service by setting the following environment variables. Create a `.env` file in the root of the project (you can copy from `.env.vllm-template` if it exists or will be created) or set these variables in your shell environment:

    - `VLLM_MODEL_NAME`: The Hugging Face model identifier to be used by VLLM.
      (Default: `ByteDance-Seed/UI-TARS-1.5-7B`)
    - `VLLM_DTYPE`: The data type for model weights (e.g., `float16`, `bfloat16`, `auto`).
      (Default: `float16`)
    - `VLLM_GPU_MEMORY_UTILIZATION`: Proportion of GPU memory to be used by VLLM (0.0 to 1.0).
      (Default: `0.90`)

    Example `.env` file content:
    ```
    VLLM_MODEL_NAME="mistralai/Mistral-7B-Instruct-v0.1"
    VLLM_DTYPE="bfloat16"
    VLLM_GPU_MEMORY_UTILIZATION="0.95"
    ```

2.  **Models Directory:**
    The service uses a local `./models` directory to cache downloaded models. This directory is mounted into the container at `/root/.cache/huggingface/hub`. Ensure this directory exists or can be created by Docker.

### Launching the Service

To build (if necessary) and start the VLLM service, run:

```bash
docker-compose --profile vllm up -d
```

The VLLM OpenAI-compatible API will be available at `http://localhost:8000/v1`.

### Accessing Logs

To view the logs from the VLLM service:

```bash
docker-compose --profile vllm logs -f vllm
```

### Stopping the Service

To stop the VLLM service:

```bash
docker-compose --profile vllm down
```

## Component Definitions

TODO...

Embedding model:
Vector Store:
Storage Context:
Service Context:

## Model Context Protocol Support

MoonMind now supports the Model Context Protocol, allowing it to act as a server that OpenHands and other agents can make client requests to. This provides a standardized way for AI agents to communicate with language models through MoonMind.

The Model Context Protocol is exposed via the `/context` endpoint, which accepts POST requests with messages and other parameters. For detailed information about the protocol implementation, see [Model Context Protocol Documentation](docs/model_context_protocol.md).

### Example Client

An example client is provided in `/examples/context_protocol_client.py` to demonstrate how to interact with the Model Context Protocol endpoint:

```bash
# Run with default model (gemini-pro)
python examples/context_protocol_client.py

# Run with a specific model
python examples/context_protocol_client.py gemini-pro-vision
```

## Model Endpoints

### `/v1/models`

This endpoint lists the available chat models from all configured providers. It now returns a combined list that can include models from Google, OpenAI, and potentially others in the future. The model list is cached in memory for improved performance after the initial fetch and is refreshed periodically (defaulting to every hour, but configurable).

**Example Response Snippet:**
```json
{
  "object": "list",
  "data": [
    {
      "id": "models/gemini-pro",
      "object": "model",
      "created": 1677609600,
      "owned_by": "Google",
      // ... other fields
    },
    {
      "id": "gpt-3.5-turbo",
      "object": "model",
      "created": 1677609600,
      "owned_by": "OpenAI",
      // ... other fields
    }
  ]
}
```

### `/v1/chat/completions`

This endpoint now routes chat completion requests to the appropriate provider based on the `model` field in the request body. You can specify a model ID from Google (e.g., `"gemini-pro"`) or OpenAI (e.g., `"gpt-4o"`).

**Example Request (OpenAI model):**
```json
{
    "model": "gpt-4o",
    "messages": [
        {"role": "user", "content": "What is the capital of France?"}
    ],
    "max_tokens": 50
}
```

## Environment Variables and Settings

MoonMind uses Pydantic settings, which can be configured via environment variables or a `.env` file.

Key settings related to model providers include:

*   **Google:**
    *   `GOOGLE_API_KEY`: Your Google API key for accessing Gemini models.
    *   `GOOGLE_CHAT_MODEL` (optional, default: `"gemini-pro"`): Default Google chat model to use if not specified in a request.
*   **OpenAI:**
    *   `OPENAI_API_KEY`: Your OpenAI API key.
    *   `OPENAI_CHAT_MODEL` (optional, default: `"gpt-3.5-turbo"`): Default OpenAI chat model.

The application will attempt to load these from environment variables. For local development, you can create a `.env` file in the project root:

```env
GOOGLE_API_KEY="your_google_api_key_here"
# GOOGLE_CHAT_MODEL="gemini-1.5-flash-latest" # Optional

OPENAI_API_KEY="your_openai_api_key_here"
# OPENAI_CHAT_MODEL="gpt-4o" # Optional
```

### Authentication providers

MoonMind resolves secrets using pluggable providers. The `profile` provider
reads values from the current user's stored profile while the `env` provider
falls back to environment variables. The lookup order is **profile → env →
error**.

Example manifest snippet:

```yaml
auth:
  github_token:
    secretRef:
      provider: profile
      key: GITHUB_TOKEN
```

### Provider Key Precedence

MoonMind checks user profile settings first when looking up API keys. If a key is not stored in the profile, the value from the environment is used. The default `disabled` auth mode automatically seeds the default profile with keys from `.env` so they can be managed via the UI.

| Auth mode | Key lookup order |
|-----------|-----------------|
| `disabled` | user profile → environment variable |
| `keycloak` | user profile → environment variable |

You can view or change keys at `http://localhost:8080/settings`.

## Roadmap
MoonMind now supports:
- Multiple chat models available from different providers (Google, OpenAI) without redeployment.
- The ability to choose a provider based on the model name in API requests.
- Enabling or disabling providers by setting their respective API keys.

Future plans include:
- Multiple embedding models available without redeployment, e.g. a code embedding model and a general purpose embedding model.
- The ability to change more settings at runtime.
- The ability to pass API credentials with requests (as an alternative to server-side configuration).

We may add support for:
- Multiple projects with different settings in one deployment, e.g. different collection names and vector store configurations.

TODO: Add a notion of a collection which tracks the vector store and embedder. Once created, when you choose a collection, the vector store and embedder are selected for you.

## Gemini

While LangChain's direct support for the newest Gemini models might vary, MoonMind integrates with Google's generative AI SDK, allowing usage of available Gemini models like `gemini-pro` and `gemini-1.5-flash-latest` when a `GOOGLE_API_KEY` is provided.

## Running Tests

### Unit Tests

To run unit tests:
```powershell
.\tools\test-unit.ps1
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
```powershell
.\tools\test-integration.ps1
```
The tests will be skipped if the required Confluence environment variables (`CONFLUENCE_URL`, `CONFLUENCE_USERNAME`, `CONFLUENCE_API_KEY`, `TEST_CONFLUENCE_SPACE_KEY`) are not found in the `.env` file.

## Manifests

Reader configurations can be validated using the `Manifest` schema. For example:

```python
from moonmind.schemas import Manifest
manifest = Manifest.model_validate_yaml("samples/github_manifest.yaml")
```

The JSON Schema can be exported with `export_schema("manifest.schema.json")`.
