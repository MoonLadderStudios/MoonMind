# MoonMind Architecture

MoonMind is a self-hostable AI “hub” that combines:

* A web UI (Open-WebUI) configured to talk to a local OpenAI-style API base
* A FastAPI-based API service that brokers model calls and exposes a **Model Context Protocol** endpoint (`/context`)
* A **task/agent queue** where runtime-specific workers (Codex/Gemini/Claude) claim and execute jobs
* A **RAG + memory** subsystem built around **LlamaIndex** and **Qdrant**
* A **self-hosted Temporal foundation** for durable workflow execution and scheduling
* Optional local model backends (Ollama, vLLM) and OpenHands integration

This overview is written from the project’s compose files, architecture docs, and dependency manifests. ([GitHub][1])



---

## Architecture at a glance

```mermaid
flowchart LR
  U[Browser] --> UI[Open-WebUI container]
  UI -->|OpenAI API base| API[MoonMind API (FastAPI)]
  API --> PG[(Postgres)]
  API --> QD[(Qdrant)]
  API -->|workflow control| TMP[Temporal]
  TMP -->|task queues| W1[Codex worker]
  TMP -->|task queues| W2[Gemini worker]
  TMP -->|task queues| W3[Claude worker]
  W1 -->|docker API (restricted)| DP[docker-socket-proxy]
  W2 -->|docker API (restricted)| DP
  W3 -->|docker API (restricted)| DP

  OH[OpenHands] -->|MCP /context| API

  subgraph Temporal Foundation
    TDB[(Temporal Postgres)]
    TMP --> TDB
  end
```

Key points:

* **FastAPI API container** is the system’s “control plane”: it starts Temporal workflows, indexes/retrieves vectors via Qdrant, and exposes the MCP `/context` endpoint used by OpenHands and similar agents. ([GitHub][2])
* **Workers** are “execution plane”: Temporal workers run deterministic orchestration workflows and execute side-effecting activities under capability boundaries. ([GitHub][2])
* **Temporal foundation** is the primary durable engine for workflow executions, task scheduling, and background job fan-out. ([GitHub][1])

---

## Docker deployment: compose files and container roles

MoonMind uses multiple compose files for different operational modes:

* `docker-compose.yaml`: primary runtime stack (UI + API + DB + Qdrant + workers + Temporal foundation + optional auth/model backends) ([GitHub][3])
* `docker-compose.downloader.yaml`: one-off downloader (e.g., pulling Qwen artifacts into `model_data`) ([GitHub][5])
* `docker-compose.test.yaml`: test harness containers (pytest + smoke checks) ([GitHub][6])

### `docker-compose.yaml` — Core runtime stack

The table below focuses on **what each container does** in the running system.

| Service                 | What it is                                                                        | Purpose in MoonMind                                                                | Notes / why it exists                                                                                                                                                                   |
| ----------------------- | --------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ui`                    | `ghcr.io/open-webui/open-webui:main`                                              | User-facing chat UI                                                                | Open-WebUI is configured to treat MoonMind as an OpenAI-compatible “API base” (typical Open-WebUI pattern). Persistent state stored in `open-webui` volume. ([GitHub][3])               |
| `api`                   | `ghcr.io/moonladderstudios/moonmind:latest` (built from `api_service/Dockerfile`) | Main API: chat/model routing, Temporal workflow APIs, RAG retrieval, `/context` MCP | Runs the API entrypoint script from the image. It is explicitly configured to expose MCP `/context` and route to providers based on requested model. ([GitHub][7])                      |
| `api-db`                | Postgres                                                                          | Durable application state                                                          | Stores job/run state (queue jobs, workflow runs), user/auth state, etc. The “durable execution state” part of the system. ([GitHub][1])                                                 |
| `qdrant`                | `qdrant/qdrant`                                                                   | Vector store for embeddings / retrieval                                            | Primary vector DB backing LlamaIndex retrieval for chat and `/context`. ([GitHub][1])                                                                                                   |
| `init-db`               | MoonMind image                                                                    | One-shot initializer                                                               | Bootstraps/initializes the DB + Qdrant index (ingestion bootstrap) then exits. Useful for first-run setup and repeatable environment bring-up. ([GitHub][3])                            |
| `agent-workspaces-init` | Alpine                                                                            | One-shot volume prep                                                               | Creates/fixes permissions for the `agent_workspaces` volume used by workers. Prevents “root-owned volume” pain and makes worker runs more deterministic. ([GitHub][3])     |
| `codex-auth-init`       | Alpine                                                                            | One-shot auth volume prep                                                          | Initializes the persistent volume used to store Codex/CLI auth material so worker containers can reuse tokens across restarts. ([GitHub][3])                                            |
| `gemini-auth-init`      | Alpine                                                                            | One-shot auth volume prep                                                          | Initializes the Gemini CLI auth volume (OAuth/token material) for the Gemini runtime worker(s). ([GitHub][3])                                                                           |
| `claude-auth-init`      | Alpine                                                                            | One-shot auth volume prep                                                          | Initializes the Claude auth volume for Claude runtime worker(s). ([GitHub][3])                                                                                                          |
| `codex-worker`          | MoonMind image                                                                    | Runtime worker (Codex)                                                             | Runs Temporal activities using the Codex runtime & tooling installed in the image. ([GitHub][2])                                                                                        |
| `gemini-worker`         | MoonMind image                                                                    | Runtime worker (Gemini)                                                            | Same worker pattern, configured for Gemini runtime; uses a persistent Gemini auth volume and Temporal task queue routing/capabilities for Gemini execution. ([GitHub][2])               |
| `claude-worker`         | MoonMind image                                                                    | Runtime worker (Claude)                                                            | Optional Temporal activity worker for Claude; typically enabled via a compose profile. ([GitHub][2])                                                                                    |
| `temporal-db`           | Postgres                                                                          | Temporal persistence + SQL visibility backend                                      | Stores Temporal workflow state/history metadata and advanced visibility data for all managed flows. ([GitHub][3])                                                                       |
| `temporal`              | `temporalio/auto-setup`                                                           | Temporal server                                                                    | Provides workflow orchestration, timers, retries, schedules, and visibility for Temporal-managed executions. ([GitHub][3])                                                              |
| `temporal-namespace-init` | MoonMind/bootstrap helper                                                       | Namespace bootstrap                                                                | Applies MoonMind namespace and retention defaults idempotently during environment bring-up. ([GitHub][3])                                                                               |
| `docker-proxy`          | `tecnativa/docker-socket-proxy`                                                   | Restricted Docker API for workers                                                  | Provides “docker-outside-of-docker” access while limiting which Docker endpoints are reachable (safer than exposing raw `/var/run/docker.sock` directly to every worker). ([GitHub][3]) |
| `keycloak-db`           | Postgres                                                                          | Optional auth DB (Keycloak)                                                        | Stores Keycloak state when running OIDC auth. Enabled via a profile. ([GitHub][3])                                                                                                      |
| `keycloak`              | Keycloak                                                                          | Optional OIDC provider                                                             | Provides OIDC login/identity for MoonMind services. Enabled via a profile. ([GitHub][3])                                                                                                |
| `ollama`                | `ollama/ollama`                                                                   | Optional local model provider                                                      | Runs a local Ollama server for inference and/or embeddings. Enabled via a profile. ([GitHub][3])                                                                                        |
| `vllm`                  | `vllm/vllm-openai`                                                                | Optional local model provider (OpenAI-style)                                       | Runs a local OpenAI-compatible inference server (vLLM), typically for hosting local models with an OpenAI API surface. Enabled via a profile. ([GitHub][3])                             |
| `openhands`             | OpenHands                                                                         | Optional agent UI/runtime that can call MoonMind MCP                               | Runs OpenHands; MoonMind’s MCP `/context` endpoint is intended to be consumed by OpenHands and other agents. Enabled via a profile. ([GitHub][7])                                       |

**Why there are “init” containers:**
MoonMind explicitly separates **persistent volume/bootstrap concerns** (permissions, OAuth token volume existence) from the long-running workers. This reduces first-run friction and makes “docker compose up” far more reliable across clean environments. ([GitHub][3])



### `docker-compose.downloader.yaml` — Model downloader

| Service      | Purpose                                                                                                                                                                                                   |
| ------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `downloader` | Runs a scripted entrypoint (e.g., `/app/tools/get-qwen.sh`) to populate `model_data/` with required model artifacts. Useful for air-gapped-ish workflows or reproducible local model setup. ([GitHub][5]) |

---

### `docker-compose.test.yaml` — Test harness

| Service              | Purpose                                                                                                                                        |
| -------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| `pytest`             | Runs unit tests inside the project image with test dependencies enabled. ([GitHub][6])                                                         |
| `cli-tooling-smoke`  | Verifies CLI tooling is present (`codex --version`, `agentkit --version`), which is important because workers rely on these CLIs. ([GitHub][6]) |

---

## Task execution model

MoonMind implements a durable execution model using **Temporal Workflows** and **Activities**:

*   **Workflow Executions** are the durable orchestration primitive coordinating complex flows like data ingestion or spec fulfillment.
*   **Activities** execute all side-effects (e.g., LLM calls, shell commands, and filesystem ops) within specialized capability boundaries.
*   Activities communicate via **ArtifactRef** values, securely reading external artifacts and writing outputs without storing large payloads in the workflow history.

This design gives you:

*   Horizontal scaling of specialized activity workers by extending task queues (e.g. `mm.activity.llm` or `mm.activity.sandbox`)
*   Runtime specialization (a Gemini worker handles specific Temporal tasks advertising Gemini capabilities)
*   Better operational controls via Temporal Visibility for lists/queries, and Temporal Schedules for recurring workflows.

---

## Memory & RAG architecture

MoonMind’s memory architecture doc frames “memory” as an accelerator that is never a hard dependency (“fail-open”), while remaining audit-friendly and repo-scoped. ([GitHub][1])

### Baseline primitives (current foundations)

The “current state” called out in the memory doc includes:

* **Document retrieval (RAG)**: **LlamaIndex + Qdrant** powering chat and `/context` ([GitHub][1])
* **Durable execution state**: Postgres tables for workflows/runs/jobs ([GitHub][1])
* **Durable artifacts**: filesystem artifact roots (logs, patches, outputs) ([GitHub][1])
* **Background jobs**: Temporal Workflows for async orchestration ([GitHub][1])

### “Context pack” concept

The desired “context pack” read path composes:

1. Planning memory (repo-scoped planning substrate)
2. Task history memory (run digests, fix patterns)
3. Long-term memory (curated knowledge, with provenance)
4. Documents (RAG via LlamaIndex + Qdrant)

…and then applies token budgeting and provenance tracking. ([GitHub][1])

### LlamaIndex Manifest System

MoonMind includes a detailed spec for a **declarative ingestion/index/retrieval manifest** for LlamaIndex usage (draft v0), covering ingestion → transforms → indexing → retrieval → optional evaluation. ([GitHub][10])

This is especially valuable when you want:

* Repeatable indexing pipelines across environments
* “Configuration as data” for RAG systems
* Easier ops/debugging (manifests can be reviewed and versioned)

---

## Model Context Protocol support

MoonMind implements the **Model Context Protocol** as a server endpoint at:

* `POST /context` — accepts message payloads and routes to the appropriate provider by model name ([GitHub][7])

The MCP doc explicitly calls out **OpenHands** as a client and provides an example client under `examples/`. ([GitHub][7])

Why this matters:

* It gives agent runtimes (OpenHands or others) a standardized interface to MoonMind’s routing, RAG, and policy layers.
* It decouples agent UX/runtime from specific model vendors, since routing happens inside MoonMind. ([GitHub][7])

---

## Key libraries and why they matter in MoonMind

MoonMind’s dependency set (Poetry) is a strong signal of its architectural choices. ([GitHub][8])

### API & service framework

* **FastAPI + Starlette + Uvicorn**: high-performance ASGI API server foundation, async-friendly for streaming, webhooks, and integrating external providers. ([GitHub][8])
* **Pydantic + pydantic-settings**: typed request/response models, config validation, and “schema-first” API ergonomics. Crucial for complex systems like queue job payloads, runtime policies, and manifest-driven RAG. ([GitHub][8])
* **orjson**: fast JSON serialization/deserialization for high-throughput endpoints (chat, queue events, task status). ([GitHub][8])
* **structlog**: structured logs that can be enriched with job IDs, run IDs, worker IDs—vital for debugging agent systems. ([GitHub][8])

### Task execution & orchestration

* **Temporalio (Python SDK)**: durable execution framework powering workflows and activities in MoonMind. It replaces older queue systems by offering first-class primitives for retries, signal-based event handling, and scheduled tasks. ([GitHub][1])

Why Temporal is valuable here (practically):

* Separates **slow/long-running** or **batch** workflows (indexing, spec workflows, orchestration plans) from the latency-sensitive API surface
* Guarantees code-level determinism and resilient retries for flaky external dependencies (LLMs, GitHub, Jira, Confluence, etc.)
* Enforces separation between deterministic orchestration code and side-effecting activity workers

### Retrieval, indexing, and “memory”

* **LlamaIndex**: MoonMind’s main RAG composition layer; used with multiple readers (Confluence, GitHub, Google, Jira, files) and multiple embeddings backends. ([GitHub][8])
* **Qdrant + llama-index-vector-stores-qdrant + qdrant-client**: scalable vector storage and retrieval for embeddings; explicitly called out as powering chat and `/context`. ([GitHub][1])

Why LlamaIndex is valuable here:

* Treats ingestion + transformation + indexing + retrieval as composable primitives
* Lets MoonMind “standardize” RAG across multiple data sources via readers
* Supports a manifest-driven operational model (MoonMind’s spec doc) ([GitHub][10])

### Model/provider clients

* **openai**, **google-generativeai**, **anthropic**: provider SDKs enabling MoonMind to route calls based on requested model. ([GitHub][7])
* **ollama**: Python client to integrate with the optional Ollama runtime. ([GitHub][8])

### Data layer & auth

* **SQLAlchemy + Alembic + asyncpg/psycopg2**: robust Postgres persistence with migrations. Fits the “durable execution state” requirement for runs/jobs/workflows. ([GitHub][1])
* **fastapi-users + PyJWT + cryptography**: ready-made user/auth plumbing; pairs with optional OIDC (Keycloak) deployments. ([GitHub][8])

### Docker + CLI tooling as part of runtime

MoonMind’s main image can optionally install Node-based CLIs:

* `@openai/codex` (Codex CLI)
* `@githubnext/spec-kit` (workflow)
* `@google/gemini-cli`
* `@anthropic-ai/claude-code`

The Dockerfile includes explicit build args to enable/disable these installs and provides “stub” fallbacks when packages aren’t available in build context. ([GitHub][11])

Why this matters:

* Workers depend on stable CLI tooling to execute tasks reproducibly.
* Stub fallback prevents “image build breaks everything” when optional tooling isn’t configured, enabling progressive adoption.

---

## Operational notes and “how things fit together”

### Startup / bootstrap (typical flow)

1. Bring up Postgres + Qdrant + API
2. Run `init-db` (or equivalent initialization path) to bootstrap DB schema and/or indices
3. Start workers; they register/claim jobs via the queue system
4. Optionally enable Keycloak, Ollama, vLLM, OpenHands profiles depending on your deployment needs ([GitHub][3])

### Scaling strategy

* **Scale workers horizontally** by capability boundary (e.g., `mm.activity.llm`, `mm.activity.sandbox`) according to demand; task queues naturally distribute load to eligible workers. ([GitHub][2])

### Security posture highlights

* Task queue design includes explicit rules to prevent leaking secrets into payloads and to avoid confusing user inputs with worker outputs. ([GitHub][2])
* Workers that need Docker use a restricted `docker-socket-proxy`; direct host-socket exposure should be avoided. ([GitHub][3])
* Optional OIDC via Keycloak exists for stronger identity management. ([GitHub][3])
