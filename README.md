# MoonMind

**The Kubernetes of AI Agents**

MoonMind is an open-source orchestrator for automating the context assembly, scheduling, and reliable execution of AI agent workloads from a single pane of glass.

Supports Claude Code, Codex, Gemini CLI, Jules, and any agent with an MCP or API endpoint. Bring your own subscription, API key, or entire agent runtime.

## Why MoonMind?

### Bring Your Own Agent
Use your own API key, OAuth subscription, or entire agent runtime with MoonMind.

- **More than just an API key:** We support OAuth login and will try to maintain support for leading providers that allow it.
- **Universal agent support:** Point MoonMind at any agent's MCP or API and it will manage it — even runtimes it wasn't built for.
- **Cloud agent orchestration:** MoonMind orchestrates cloud agents like Jules and Codex Cloud. Even with only "black box" access, it provides significant value by tracking status, providing context, answering clarifying questions, and following up on open PRs.

### Avoid Vendor Lock-In
MoonMind abstracts away the agent runtime, making it easy to switch vendors without rewriting your workflows.

- Swap between proprietary and open-source model providers with a configuration change.
- Mix and match models and providers: use a proprietary model for planning and an open-source model for implementation.

### Self-Healing Reliable Execution
Backed by [Temporal](https://temporal.io/) for durable execution — workflows survive crashes and restarts with deterministic replay. No work is ever lost.

- Automatic stuck detection with soft and hard resets.
- Failure classification (transient vs. deterministic) prevents retry loops on permanent failures.
- Retry budgets with configurable attempt limits and timeouts.
- Operator takeover when human judgment is needed.

### Three-Plane Memory Architecture
Enhance your agents' context with your data and memories — even when using cloud agents like Jules or Codex Cloud.

- **Planning memory (Beads):** Git-native, repo-scoped planning context.
- **Task history (Run Digests + Fix Patterns):** Structured summaries and procedural memory indexed in Qdrant.
- **Long-term memory (Mem0):** Curated reusable knowledge — decisions, conventions, playbooks.
- **RAG:** LlamaIndex + Qdrant with loaders for Confluence, GitHub, Google Drive, Jira, and local files.
- **Graceful degradation:** Unavailable memory subsystems degrade quality, never block execution.
- Two methods for external agent context: input injection and exposing callback URLs or MCP so agents can pull data securely.

### Single Pane of Glass
Track the progress of all your tasks in Mission Control with real-time status, event timelines, artifact browsing, and live operator controls.

### Security
- **Docker socket proxy** with whitelisted API endpoints — workers never get raw host access.
- **Capability-based job routing:** Workers advertise capabilities; jobs go to the right worker automatically.
- **Approval gates:** High-risk operations require operator approval before proceeding.
- **File-level allowlists** restrict which files can be modified.
- **Secret sanitization** ensures credentials never appear in logs or artifacts.

### Scheduling
- Set execution order and dependencies between tasks.
- Schedule recurring tasks via Temporal Schedules.
- Step-level task decomposition with artifact preservation between steps.

### Open Source
MIT licensed and ready for personal or commercial use.

### One-Click Deployment
Run anywhere you can run Docker.

## Quick Start

### Prerequisites

- **Docker** and **Docker Compose** installed and running ([Docker Desktop](https://www.docker.com/products/docker-desktop) includes both).
- **Environment file:** Copy the template and configure your settings:
  ```bash
  cp .env-template .env
  ```
  At minimum, set:
  - `GITHUB_TOKEN=<github_pat_with_repo_access>`
  - API keys or OAuth for your chosen runtime(s)

### Authenticate Agent Runtimes (One-Time)

Before running workers, authenticate the runtimes you plan to use:

```bash
./tools/auth-codex-volume.sh    # Codex CLI (OAuth)
./tools/auth-gemini-volume.sh   # Gemini CLI (OAuth)
./tools/auth-claude-volume.sh   # Claude Code (OAuth mode only — skip for API key mode)
```

This persists credentials in named Docker volumes so all subsequent runs reuse them.

For Claude Code, the default auth mode is `MOONMIND_CLAUDE_CLI_AUTH_MODE=api_key`, which uses `ANTHROPIC_API_KEY` from `.env` with no volume login step. Set it to `oauth` to use browser-based OAuth instead.

### Start MoonMind

```bash
docker compose up -d
```

This brings up the full stack: API, Temporal, workers, PostgreSQL, Qdrant, MinIO, and the Mission Control UI.

To build locally instead of pulling images:
```bash
docker compose up -d --build
```

### Access the UI

Once services are running, open **Mission Control** at `http://localhost:8080`.

### Manage API Keys

When `AUTH_PROVIDER` is `disabled` (the default for local setups), provider keys from `.env` are copied to the default user profile on startup. View or change them at `http://localhost:8080/settings`.

### Initialize the Vector Database (Optional)

To load initial data into Qdrant from configured sources:

1. Set `INIT_DATABASE=true` in `.env`
2. Restart: `docker compose down && docker compose up -d`
3. Check logs: `docker compose logs init-db`
4. Set `INIT_DATABASE=false` after initialization to prevent re-runs.

### Stop MoonMind

```bash
docker compose down
```

## Architecture

MoonMind uses a Temporal-first architecture with specialized worker fleets:

- **API Service** (FastAPI): OpenAI-compatible `/v1/chat/completions` and `/v1/models` endpoints with multi-provider model routing (Gemini, OpenAI, Anthropic, Ollama, vLLM). Also serves the MCP endpoint.
- **Temporal Server**: Durable execution engine with PostgreSQL persistence and SQL visibility.
- **Temporal Workers**:
  - `temporal-worker-workflow` — orchestration logic
  - `temporal-worker-artifacts` — artifact storage and retrieval (MinIO/S3)
  - `temporal-worker-llm` — LLM calls
  - `temporal-worker-sandbox` — runtime CLI execution (Codex, Gemini, Claude)
  - `temporal-worker-integrations` — external agent coordination
- **Mission Control UI** (Open-WebUI): Task dashboard, real-time status, artifact browsing, operator controls.
- **PostgreSQL**: Durable state for jobs, runs, and users.
- **Qdrant**: Vector database for RAG and memory indexing.
- **MinIO**: S3-compatible artifact storage.
- **Docker Proxy**: Restricted Docker socket access for worker containers.

All services run from a single `docker-compose.yaml`. A shared Docker image (`api_service/Dockerfile`) serves both the API and all workers, with runtime behavior selected via environment variables.

### Optional Profiles

```bash
docker compose --profile ollama up -d       # Local LLM inference (GPU required)
docker compose --profile vllm up -d         # vLLM OpenAI-compatible inference
docker compose --profile openhands up -d    # OpenHands agent integration
docker compose --profile keycloak up -d     # OIDC identity provider
docker compose --profile temporal-ui up -d  # Temporal debugging UI (port 8088)
```

### Temporal Operations

**Cleaning Temporal state** (reset between test runs):
```bash
./scripts/temporal_clean_state.sh
```

**End-to-end test:**
```bash
python scripts/temporal_e2e_test.py
```

**Visibility schema rehearsal** (required before upgrades):
```bash
TEMPORAL_SHARD_DECISION_ACK=acknowledged \
docker compose --profile temporal-tools run --rm temporal-visibility-rehearsal
```

Temporal gRPC is internal (`temporal:7233`) and not published on a host port. The UI is opt-in via the `temporal-ui` profile.

## Model Context Protocol (MCP)

MoonMind supports MCP as both a server and a client:

- **As a server:** The `/context` endpoint lets external agents route chat and tool calls through MoonMind, with optional RAG context injection.
- **As a client:** MoonMind can consume external agents' MCP capabilities via its dynamic tool registry.
- **Reverse MCP:** External agents receive scoped, ephemeral MCP sessions to read/write artifacts without direct storage access.

MCP tools include queue management (`enqueue`, `claim`, `heartbeat`, `complete`, `fail`), artifact operations, and dynamically registered external agent capabilities.

## Private Skills

MoonMind supports run-scoped worker skills, including private skill definitions:

1. Add a private skill in the local mirror (`.agents/skills/local`, gitignored):

```text
.agents/skills/local/
  my-private-scan/
    SKILL.md
    ... (skill implementation files)
```

2. Configure in `.env`:
   - `WORKFLOW_SKILLS_LOCAL_MIRROR_ROOT=.agents/skills/local`
   - `WORKFLOW_SKILL_POLICY_MODE=permissive` (default) or `allowlist`

3. Skills can be sourced from local paths or private git repos:
```text
skill_sources:
  my-private-scan:1.0.0: git+https://<token>@github.com/org/my-private-scan.git
```

Per-run, MoonMind materializes a shared skill directory and links both CLI adapters:
- `<run_root>/.agents/skills -> ../skills_active`
- `<run_root>/.gemini/skills -> ../skills_active`

## Document Loaders

MoonMind includes document loaders for populating the RAG vector database:

### Confluence
`POST /documents/confluence/load` — Load from a Confluence space or specific page IDs.

### GitHub Repository
`POST /documents/github/load` — Ingest documents from a GitHub repository with optional branch and file extension filters.

### Google Drive
`POST /documents/google_drive/load` — Load from a Google Drive folder or specific file IDs. Supports service account keys or Application Default Credentials.

See the API for full request/response schemas.

## Configuration

MoonMind uses Pydantic settings configured via environment variables or `.env`.

### Model Providers

| Provider | Key Variable | Default Model |
|----------|-------------|---------------|
| Google Gemini | `GOOGLE_API_KEY` | `gemini-3.1-pro` |
| OpenAI | `OPENAI_API_KEY` | `gpt-3.5-turbo` |
| Anthropic | `ANTHROPIC_API_KEY` | — |
| Ollama | `OLLAMA_BASE_URL` | `devstral:24b` |
| vLLM | (profile) | Configurable |

The `/v1/models` endpoint returns a combined list from all enabled providers, cached and refreshed periodically.

### Authentication Providers

MoonMind resolves secrets using pluggable providers. Lookup order: **profile → environment → error**.

| Auth mode | Key lookup order |
|-----------|-----------------|
| `disabled` | user profile → environment variable |
| `keycloak` | user profile → environment variable |

### Ollama Configuration

Control local inference models via environment variables:

- `OLLAMA_CHAT_MODEL` — Chat model (default: `devstral:24b`)
- `OLLAMA_EMBEDDING_MODEL` — Embedding model (default: `hf.co/qwen/gte-Qwen2-7B-instruct-GGUF:Q6_K`)
- `OLLAMA_MODES` — Which models to load: `chat`, `embed`, or `chat,embed`

Launch with the `ollama` profile: `docker compose --profile ollama up -d`

### vLLM Configuration

- `VLLM_MODEL_NAME` — HuggingFace model ID (default: `ByteDance-Seed/UI-TARS-1.5-7B`)
- `VLLM_DTYPE` — Data type (default: `float16`)
- `VLLM_GPU_MEMORY_UTILIZATION` — GPU memory fraction (default: `0.90`)

Launch with the `vllm` profile: `docker compose --profile vllm up -d`

## Development

### Pre-commit

```bash
pip install pre-commit
pre-commit install
```

Run checks manually:
```bash
pre-commit run --all-files
```

### Running Tests

All test scripts run pre-commit checks automatically before executing tests.

**Unit tests:**
```bash
./tools/test_unit.sh
```

**Integration tests:**
```bash
docker compose -f docker-compose.test.yaml run --rm orchestrator-tests
```

**Confluence integration tests:**
```powershell
.\tools\test-integration.ps1
```
Requires Confluence credentials in `.env`. Tests are skipped if credentials are not configured.

## Design Principles

1. One-click deployment with smart defaults
2. Powerful runtime configurability
3. Modular and extensible architecture
4. Graceful degradation: unavailable subsystems degrade quality, never block execution
5. Vendor-agnostic: swap providers without rewriting workflows

## Roadmap

- **More agent runtimes** — including leading open-source options
- **Richer memory tools** — long-lived project and user memories beyond vector search
- **Voice-driven orchestration** — spoken commands to orchestrator runs with streamed status updates
