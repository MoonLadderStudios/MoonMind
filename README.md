# MoonMind

**MoonMind is a self-hosted AI orchestration hub for chat, memory, and automation. It combines multi-provider LLM routing, retrieval-augmented generation, and a Celery-based orchestrator that can run skills-based workflows across your code and infrastructure.**

## What is MoonMind?

MoonMind is an AI control plane you run yourself. It gives you:

- **Model routing:** An OpenAI-compatible `/v1/chat/completions` and `/v1/models` API that fans out to Google Gemini, OpenAI, Anthropic, Ollama, and VLLM without redeploying. Models are discovered from all enabled providers and cached for fast listing and routing.
- **Memory and retrieval:** A RAG pipeline powered by LlamaIndex, Qdrant, and configurable embeddings, with loaders for Confluence, GitHub, Google Drive, and more so agents and UIs can ground their reasoning in your real documents.
- **Automation and orchestration:** A Celery-based automation layer and mm-orchestrator service that run skills-based workflows, execute task chains over RabbitMQ + PostgreSQL, emit StatsD metrics, and write artifacts under `var/artifacts/spec_workflows/<run_id>`.

MoonMind exposes all of this through:

- **OpenAI-compatible APIs** for drop-in use with tools and UIs like Open-WebUI.
- **Model Context Protocol (MCP)** so external agents can treat MoonMind as a standardized model and tools backend.
- **Apps and manifests** that describe higher-level workflows declaratively and can be invoked from CLIs, agents, or CI.

## Quick Start

This section guides you through a one-click deployment of MoonMind using Docker Compose. The default stack includes the UI (Open-WebUI), API backend, Qdrant, RabbitMQ/Postgres dependencies, Celery/orchestrator services, and the `moonmind-codex-worker` daemon for `/api/queue` jobs.

**Prerequisites:**

*   **Docker:** Ensure Docker is installed and running on your system. You can download it from [Docker's official website](https://www.docker.com/products/docker-desktop).
*   **Docker Compose:** Docker Compose is included with most Docker Desktop installations. If not, follow the [official installation guide](https://docs.docker.com/compose/install/).
*   **Environment File:** Create a `.env` file in the root of the project by copying the `.env-template` file:
    ```bash
    cp .env-template .env
    ```
    Review the `.env` file and fill in any necessary API keys or configuration values if you plan to use services like OpenAI, Google, Confluence, etc.
    For the fastest Codex-worker + Gemini-embedding path, make sure these are set:
    - `GOOGLE_API_KEY=<your_google_api_key>`
    - `DEFAULT_EMBEDDING_PROVIDER=google`
    - `GOOGLE_EMBEDDING_MODEL=gemini-embedding-001`
    - `CODEX_ENV=prod`
    - `CODEX_MODEL=gpt-5-codex`
    - `GITHUB_TOKEN=<github_pat_with_repo_access>`
    The shared Docker image for the API, orchestrator, and Celery workers already bundles the Gemini CLI; set `GOOGLE_API_KEY` in `.env` so the CLI can authenticate during development (you can verify with `tools/verify-gemini.sh`).

**Running MoonMind:**

1.  **Open a terminal** in the root directory of the MoonMind project.
2.  **Authenticate the Codex worker volume (one-time per environment)** before running Codex automation (Celery or `/api/queue` worker):
    ```bash
    ./tools/auth-codex-volume.sh
    ```
    This persists Codex auth in `codex_auth_volume` so worker pre-flight checks pass.
    By default (`AUTH_PROVIDER=disabled`) the `codex-worker` service auto-creates and persists a worker token on first start. If auth is enabled, set either `MOONMIND_WORKER_TOKEN` (recommended) or `MOONMIND_API_TOKEN` in `.env`. If Codex is not authenticated yet, the worker stays idle and retries until `codex login status` passes.
3.  **Start the services** using the following command:
    ```bash
    docker-compose up -d
    ```
    The `-d` flag runs the containers in detached mode, meaning they will run in the background.
    To force-refresh remote images first, run:
    ```bash
    docker-compose pull && docker-compose up -d
    ```
    To build locally instead of pulling images, run:
    ```bash
    docker-compose up -d --build
    ```

4.  **Accessing the UI:** Once the services are up and running (this might take a few minutes the first time as images are downloaded and built), you can access the Open-WebUI by navigating to `http://localhost:8080` in your web browser.

5.  **Manage API Keys:** When `AUTH_PROVIDER` is left as `disabled` (the default for local setups), any provider keys you place in `.env` are copied to the default user profile on startup. Visit `http://localhost:8080/settings` to view or change these values.
6.  **Initializing the Vector Database (Optional but Recommended):**
    If you want to load initial data into the Qdrant vector database (e.g., from local files or other sources configured in `config.toml`), you can trigger the initialization process.
    Set the `INIT_DATABASE` variable in your `.env` file to `true`:
    ```env
    INIT_DATABASE=true
    ```
    Then, restart your Docker Compose setup:
    ```bash
    docker-compose down && docker-compose up -d
    ```
    The `init-db` service will run, attempt to load data, and then exit. You can check its logs using `docker-compose logs init-db`. After initialization, you may want to set `INIT_DATABASE=false` again to prevent re-initialization on subsequent restarts.

**Stopping MoonMind:**

To stop all running services, execute the following command in the project root:
```bash
docker-compose down
```

This setup uses the main `docker-compose.yaml` file, which is configured for a production-like deployment with the Qdrant vector store. For development purposes, or if you need to use a different configuration, you might use other specific compose files (for example `docker-compose.test.yaml`).

### Private skills for worker jobs

MoonMind now supports run-scoped worker skills, including private skill definitions.

1. Add a private skill artifact in the local mirror:

- Mirror root: `.agents/skills/local` (local-only, gitignored)

```text
.agents/skills/local/
  my-private-scan/
    SKILL.md
    ... (skill implementation files)
```

`SKILL.md` must include frontmatter naming the skill, and the `name` must match the directory name.

```yaml
---
name: my-private-scan
description: Private project-specific scan helper skill
---
```

2. Point workers at private skills and choose policy mode:

- For spec workflow/Celery/Gemini workers, set in `.env`:

  - `SPEC_SKILLS_LOCAL_MIRROR_ROOT=.agents/skills/local`
  - `SPEC_SKILLS_LEGACY_MIRROR_ROOT=.agents/skills/skills` (optional compatibility fallback)
  - `SPEC_SKILLS_VALIDATE_LOCAL_MIRROR=true` (optional startup validation)
  - `SPEC_WORKFLOW_SKILL_POLICY_MODE=permissive` (default; auto-accept resolvable skills without allowlist maintenance)
  - `SPEC_WORKFLOW_ALLOWED_SKILLS="speckit,my-private-scan"` (only enforced when `SPEC_WORKFLOW_SKILL_POLICY_MODE=allowlist`)
  - `SPEC_WORKFLOW_DEFAULT_SKILL=my-private-scan` (optional)
  - `SPEC_WORKFLOW_DISCOVER_SKILL=my-private-scan` / `SPEC_WORKFLOW_SUBMIT_SKILL=my-private-scan` / `SPEC_WORKFLOW_PUBLISH_SKILL=my-private-scan` (optional per-stage selection)

- For standalone `moonmind-codex-worker`, also use:

  - `MOONMIND_DEFAULT_SKILL=my-private-scan`
  - `MOONMIND_SKILL_POLICY_MODE=permissive` (default; set `allowlist` to enforce worker allowlists)
  - `MOONMIND_ALLOWED_SKILLS=my-private-scan,speckit` (only enforced when `MOONMIND_SKILL_POLICY_MODE=allowlist`)

3. Source private skills from external locations when needed.

- Use local path sources:

```text
skill_sources:
  my-private-scan:1.0.0: /absolute/path/to/my-private-scan
```

- Use private git sources:

```text
skill_sources:
  my-private-scan:1.0.0: git+https://<token>@github.com/org/my-private-scan.git
```

These mappings are consumed from workflow job context as `skill_selection` + `skill_sources` when your orchestration path submits runs through the workflow context payload.

4. Enqueue `codex_skill` jobs with a `skillId` to route via worker selection metadata (must be allowlisted only when policy mode is `allowlist`):

```json
{
  "type": "codex_skill",
  "payload": {
    "skillId": "my-private-scan"
  }
}
```

5. Validate the path after startup:

- Start workers with `SPEC_SKILLS_VALIDATE_LOCAL_MIRROR=true` and confirm startup logs show skill materialization success.
- For run workspace checks, inspect `<run_root>/skills_active` and linked adapters:
  - `<run_root>/.agents/skills -> ../skills_active`
  - `<run_root>/.gemini/skills -> ../skills_active`

## Automation & Orchestrator

### Spec-driven Celery automation

MoonMind ships with dedicated Celery workers for GitHub Spec Kit, Codex, and Gemini automation. The workers share configuration with the API service through `.env`. Populate the following variables (defaults are provided in `.env-template`):

- `CELERY_BROKER_URL` – AMQP connection string for RabbitMQ (e.g., `amqp://moonmind:password@rabbitmq:5672//`).
- `CELERY_RESULT_BACKEND` – SQLAlchemy URL for the PostgreSQL result backend (e.g., `db+postgresql://postgres:password@api-db:5432/moonmind`).
- `CELERY_DEFAULT_QUEUE` – Default queue name for Spec Kit tasks (`speckit`).
- `CELERY_DEFAULT_EXCHANGE` – Exchange used for the Spec Kit queue (`speckit`).
- `CELERY_DEFAULT_ROUTING_KEY` – Routing key for Spec Kit tasks (`speckit`).
- `SPEC_WORKFLOW_CODEX_QUEUE` – Codex queue name (default `codex`).
- `SPEC_WORKFLOW_USE_SKILLS` – Enables skills-first stage routing (default `true`).
- `SPEC_WORKFLOW_DEFAULT_SKILL` – Default skill for discover/submit/publish stages (default `speckit`).
- `SPEC_WORKFLOW_SKILL_POLICY_MODE` – Skill policy mode (`permissive` default, `allowlist` for strict enforcement).
- `SPEC_WORKFLOW_ALLOWED_SKILLS` – Comma-separated allowlist of selectable skills (enforced when policy mode is `allowlist`).
- `SPEC_SKILLS_WORKSPACE_ROOT` – Run workspace subdirectory under `SPEC_WORKFLOW_WORKSPACE_ROOT` used to create shared skill adapters (default `runs`).
- `SPEC_SKILLS_CACHE_ROOT` – Immutable local cache for verified skill artifacts (default `var/skill_cache`).
- `SPEC_SKILLS_LOCAL_MIRROR_ROOT` – Local mirror root for skill source resolution (default `.agents/skills/local`).
- `SPEC_SKILLS_LEGACY_MIRROR_ROOT` – Compatibility fallback mirror root checked after the local mirror (default `.agents/skills/skills`).
- `SPEC_SKILLS_VERIFY_SIGNATURES` – Require signature metadata during materialization (default `false`).
- `SPEC_SKILLS_VALIDATE_LOCAL_MIRROR` – Enforce startup validation of local mirror contents (default `false`).
- `CODEX_VOLUME_NAME` – Docker volume that stores persistent Codex auth (default `codex_auth_volume`).
- `CODEX_VOLUME_PATH` – In-container Codex auth path (default `/home/app/.codex`).
- `CODEX_ENV` and `CODEX_MODEL` – Required by credential validation before Codex phases execute.
- `GITHUB_TOKEN` – Required for private repository clone/push/PR operations.

During workflow execution, MoonMind materializes a per-run shared skill directory and links both adapters to the same active set:

- `<run_root>/skills_active/`
- `<run_root>/.agents/skills -> ../skills_active`
- `<run_root>/.gemini/skills -> ../skills_active`

**Gemini Automation:**

The Gemini worker listens on the `gemini` queue and uses the `celery_worker.gemini_worker` entrypoint.

- `GEMINI_CELERY_QUEUE` - Queue name for Gemini tasks (default: `gemini`).
- `GEMINI_HOME` - Path to the persistent volume for Gemini CLI configuration (default: `/var/lib/gemini-auth`).

See [docs/GeminiCliWorkers.md](docs/GeminiCliWorkers.md) for detailed architecture and configuration.

After configuring the environment, start the workers from the project root:

```bash
# Fastest path: one worker consumes both discovery (`speckit`) and Codex (`codex`) queues
poetry run celery -A celery_worker.speckit_worker worker -Q speckit,codex --loglevel=info

# Gemini Worker
poetry run celery -A celery_worker.gemini_worker worker -Q gemini --loglevel=info
```

The worker entrypoints load `moonmind.config.settings.AppSettings`, ensuring broker and result backend defaults always match the active MoonMind environment. The Spec Kit worker runs a Codex pre-flight (`codex login status`) and will fail fast if the configured auth volume is not authenticated.

### Agent queue Codex worker

MoonMind also includes a standalone queue worker daemon for `/api/queue/*` jobs (canonical `task`, plus legacy `codex_exec` / `codex_skill`):

- Compose service: `codex-worker` (started by default via `docker compose up -d`).
- CLI entrypoint: `moonmind-codex-worker`.
- Claim path: `POST /api/queue/jobs/claim` with `X-MoonMind-Worker-Token`.

Run it manually outside Compose if needed:

```bash
poetry run moonmind-codex-worker
```

Quick checks:

```bash
docker compose logs -f codex-worker
curl http://localhost:5000/api/queue/jobs/<job-id>
```

The shared `api_service` image includes pinned Codex CLI and GitHub Spec Kit CLI versions so workers can run automation workflows without runtime downloads:

- Build args set defaults: `CODEX_CLI_VERSION=latest` and `SPEC_KIT_VERSION=0.4.0`.
- Override the pins when building the image:

  ```bash
  docker build \
    --build-arg CODEX_CLI_VERSION=latest \
    --build-arg SPEC_KIT_VERSION=0.4.1 \
    -f api_service/Dockerfile .
  ```

Release notes should record the versions shipped with each published image so operators know when the automation toolchain changed.

### mm-orchestrator service

For longer-running, multi-step workflows, MoonMind provides an `orchestrator` service. It:

- Listens on a dedicated Celery queue (`ORCHESTRATOR_CELERY_QUEUE`, default `orchestrator.run`).
- Mounts `/workspace` and connects to a Docker host (`ORCHESTRATOR_DOCKER_HOST`) to patch, build, restart, and verify services based on an ActionPlan.
- Emits StatsD metrics (`ORCHESTRATOR_STATSD_HOST` / `ORCHESTRATOR_STATSD_PORT`).
- Stores run artifacts under `ORCHESTRATOR_ARTIFACT_ROOT` (default `var/artifacts/spec_workflows`) for later inspection.

The orchestrator is designed to be driven by agents and CLIs: submit a Spec, get back a run id, and watch the system move through analyze → patch → build → restart → verify → rollback, with approvals enforced where required.

## Development

### Setting Up Pre-commit

MoonMind relies on `pre-commit` to enforce formatting and linting. Install pre-commit and set up the hooks after cloning:

```bash
# Install pre-commit (if not already installed)
pip install pre-commit

# Install the git hooks
pre-commit install
```

Attempting to commit with style violations will fail:

```bash
$ git commit -am "msg"
isort....................................................................Failed
```

To manually run pre-commit checks on all files:
```bash
pre-commit run --all-files
```

**Note:** All test scripts (`test-unit.ps1`, `test-integration.ps1`, `test-e2e.ps1`) automatically run pre-commit checks before executing tests.

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

Apps are orchestrated workflows built on top of MoonMind.

An App can:

- Declare its readers and defaults via a YAML manifest.
- Use MoonMind’s retrieval layer to build a working context over your code and documents.
- Dispatch long-running steps to the Spec Kit worker or mm-orchestrator over Celery queues.

Apps can be invoked from:

- the CLI,
- agents via the Model Context Protocol (`/context`),
- or CI pipelines that call MoonMind’s APIs.

## Using MoonMind as an agent backend

MoonMind is built to sit behind agent frameworks:

- **Model Context Protocol:** The `/context` endpoint exposes a standard interface that tools like OpenHands can use to route chat and tool calls through MoonMind.
- **Agent environments:** Sample configs and guides for running MoonMind from inside agent sandboxes like the Jules Agent and OpenHands (`OPENHANDS__*` settings) so agents can reuse your models, memory, and orchestrator queues.

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

If your environment does not provide a `python` binary, use `python3` for these commands.

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

## Roadmap: from RAG server to orchestration hub

Today MoonMind supports:

- Multi-provider chat (Google Gemini, OpenAI, Anthropic, Ollama, VLLM) behind a single OpenAI-compatible API.
- Retrieval-augmented chat over your own Confluence, GitHub, Google Drive, and other sources.
- Celery-backed Spec Kit and Codex workflows plus an mm-orchestrator service for plan/patch/build/restart/verify loops.

Planned evolution:

- **Richer memory tools** – long-lived project and user memories that Apps and agents can read/write, beyond vector search, to ground orchestrated workflows and approvals.
- **Voice-driven orchestration** – a small voice gateway that turns spoken commands into orchestrator runs (e.g., “deploy the latest Spec to staging and run tests”) and streams status updates back.

The north star is for MoonMind to act as a single, self-hosted hub where chat, memory, and automation all meet.

## Gemini

While LangChain's direct support for the newest Gemini models might vary, MoonMind integrates with Google's generative AI SDK, allowing usage of available Gemini models like `gemini-pro` and `gemini-1.5-flash-latest` when a `GOOGLE_API_KEY` is provided.

## Running Tests

All test scripts now include automatic pre-commit checks (formatting and linting) before running tests. If formatting issues are detected, the script will fail and prompt you to fix them.

### Unit Tests

To run unit tests:
```powershell
.\tools\test-unit.ps1
```

This script will:
1. Run `pre-commit` checks (black, isort, ruff)
2. Build the test Docker container
3. Execute all unit tests

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
