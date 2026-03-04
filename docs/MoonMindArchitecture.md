# MoonMind Architecture

MoonMind is a self-hostable AI “hub” that combines:

* A web UI (Open-WebUI) configured to talk to a local OpenAI-style API base
* A FastAPI-based API service that brokers model calls and exposes a **Model Context Protocol** endpoint (`/context`)
* A **task/agent queue** where runtime-specific workers (Codex/Gemini/Claude) claim and execute jobs
* A **RAG + memory** subsystem built around **LlamaIndex** and **Qdrant**
* An optional **Celery + RabbitMQ** orchestration stack for asynchronous/background workflows and “spec workflow” style automation
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
  API -->|/api/queue jobs| W1[Codex worker]
  API -->|/api/queue jobs| W2[Gemini worker]
  API -->|/api/queue jobs| W3[Claude worker]
  W1 -->|docker API (restricted)| DP[docker-socket-proxy]
  W2 -->|docker API (restricted)| DP
  W3 -->|docker API (restricted)| DP

  OH[OpenHands] -->|MCP /context| API

  subgraph Optional: Celery Orchestration Stack
    RMQ[(RabbitMQ)]
    CW[Celery workers] --> RMQ
    ORC[mm-orchestrator] --> RMQ
  end
```

Key points:

* **FastAPI API container** is the system’s “control plane”: it stores durable state in Postgres, indexes/retrieves vectors via Qdrant, exposes the queue API used by workers, and exposes the MCP `/context` endpoint used by OpenHands and similar agents. ([GitHub][2])
* **Workers** are “execution plane”: they claim jobs, hydrate inputs/attachments, run a staged lifecycle, and publish results. ([GitHub][2])
* **Celery stack** is optional but provides an additional async orchestration layer that can fan out spec/workflow tasks through RabbitMQ queues. ([GitHub][1])

---

## Docker deployment: compose files and container roles

MoonMind uses multiple compose files for different operational modes:

* `docker-compose.yaml`: primary runtime stack (UI + API + DB + Qdrant + workers + optional auth/model backends) ([GitHub][3])
* `docker-compose.job.yaml`: Celery/RabbitMQ orchestration stack (background workers + sharded Codex queues + mm-orchestrator) ([GitHub][4])
* `docker-compose.downloader.yaml`: one-off downloader (e.g., pulling Qwen artifacts into `model_data`) ([GitHub][5])
* `docker-compose.test.yaml`: test harness containers (pytest + smoke checks) ([GitHub][6])

### `docker-compose.yaml` — Core runtime stack

The table below focuses on **what each container does** in the running system.

| Service                 | What it is                                                                        | Purpose in MoonMind                                                                | Notes / why it exists                                                                                                                                                                   |
| ----------------------- | --------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ui`                    | `ghcr.io/open-webui/open-webui:main`                                              | User-facing chat UI                                                                | Open-WebUI is configured to treat MoonMind as an OpenAI-compatible “API base” (typical Open-WebUI pattern). Persistent state stored in `open-webui` volume. ([GitHub][3])               |
| `api`                   | `ghcr.io/moonladderstudios/moonmind:latest` (built from `api_service/Dockerfile`) | Main API: chat/model routing, queue/job APIs, RAG retrieval, `/context` MCP server | Runs the API entrypoint script from the image. It is explicitly configured to expose MCP `/context` and route to providers based on requested model. ([GitHub][7])                      |
| `api-db`                | Postgres                                                                          | Durable application state                                                          | Stores job/run state (queue jobs, workflow runs), user/auth state, etc. The “durable execution state” part of the system. ([GitHub][1])                                                 |
| `qdrant`                | `qdrant/qdrant`                                                                   | Vector store for embeddings / retrieval                                            | Primary vector DB backing LlamaIndex retrieval for chat and `/context`. ([GitHub][1])                                                                                                   |
| `init-db`               | MoonMind image                                                                    | One-shot initializer                                                               | Bootstraps/initializes the DB + Qdrant index (ingestion bootstrap) then exits. Useful for first-run setup and repeatable environment bring-up. ([GitHub][3])                            |
| `agent-workspaces-init` | Alpine                                                                            | One-shot volume prep                                                               | Creates/fixes permissions for the `agent_workspaces` volume used by workers/orchestrator. Prevents “root-owned volume” pain and makes worker runs more deterministic. ([GitHub][3])     |
| `codex-auth-init`       | Alpine                                                                            | One-shot auth volume prep                                                          | Initializes the persistent volume used to store Codex/CLI auth material so worker containers can reuse tokens across restarts. ([GitHub][3])                                            |
| `gemini-auth-init`      | Alpine                                                                            | One-shot auth volume prep                                                          | Initializes the Gemini CLI auth volume (OAuth/token material) for the Gemini runtime worker(s). ([GitHub][3])                                                                           |
| `claude-auth-init`      | Alpine                                                                            | One-shot auth volume prep                                                          | Initializes the Claude auth volume for Claude runtime worker(s). ([GitHub][3])                                                                                                          |
| `codex-worker`          | MoonMind image                                                                    | Runtime worker (Codex)                                                             | Claims jobs from the queue system and executes tasks using the Codex runtime & tooling installed in the image. ([GitHub][2])                                                            |
| `gemini-worker`         | MoonMind image                                                                    | Runtime worker (Gemini)                                                            | Same worker pattern, but configured for Gemini runtime; uses a persistent Gemini auth volume and queue routing/capabilities for Gemini execution. ([GitHub][2])                         |
| `claude-worker`         | MoonMind image                                                                    | Runtime worker (Claude)                                                            | Optional runtime-specific worker for Claude; typically enabled via a compose profile. ([GitHub][2])                                                                                     |
| `orchestrator`          | MoonMind image                                                                    | “Orchestrator worker” consuming orchestrator jobs                                  | Runs a worker module intended for orchestrator-type jobs (`orchestrator_run`), rather than end-user “task” jobs. ([GitHub][3])                                                          |
| `scheduler`             | MoonMind image                                                                    | Recurring schedule dispatcher                                                      | Polls Postgres for due recurring tasks and enqueues/dispatches them; implemented as the `moonmind-scheduler` CLI entrypoint. ([GitHub][8])                                              |
| `docker-proxy`          | `tecnativa/docker-socket-proxy`                                                   | Restricted Docker API for workers                                                  | Provides “docker-outside-of-docker” access while limiting which Docker endpoints are reachable (safer than exposing raw `/var/run/docker.sock` directly to every worker). ([GitHub][3]) |
| `keycloak-db`           | Postgres                                                                          | Optional auth DB (Keycloak)                                                        | Stores Keycloak state when running OIDC auth. Enabled via a profile. ([GitHub][3])                                                                                                      |
| `keycloak`              | Keycloak                                                                          | Optional OIDC provider                                                             | Provides OIDC login/identity for MoonMind services. Enabled via a profile. ([GitHub][3])                                                                                                |
| `ollama`                | `ollama/ollama`                                                                   | Optional local model provider                                                      | Runs a local Ollama server for inference and/or embeddings. Enabled via a profile. ([GitHub][3])                                                                                        |
| `vllm`                  | `vllm/vllm-openai`                                                                | Optional local model provider (OpenAI-style)                                       | Runs a local OpenAI-compatible inference server (vLLM), typically for hosting local models with an OpenAI API surface. Enabled via a profile. ([GitHub][3])                             |
| `openhands`             | OpenHands                                                                         | Optional agent UI/runtime that can call MoonMind MCP                               | Runs OpenHands; MoonMind’s MCP `/context` endpoint is intended to be consumed by OpenHands and other agents. Enabled via a profile. ([GitHub][7])                                       |

**Why there are “init” containers:**
MoonMind explicitly separates **persistent volume/bootstrap concerns** (permissions, OAuth token volume existence) from the long-running workers. This reduces first-run friction and makes “docker compose up” far more reliable across clean environments. ([GitHub][3])

---

### `docker-compose.job.yaml` — Celery/RabbitMQ orchestration stack

This compose file stands up a classic Celery architecture with a broker plus worker fleet (including sharded queues) and an orchestrator container.

| Service              | What it is                     | Purpose in MoonMind                    | Notes / why it exists                                                                                                                                                          |
| -------------------- | ------------------------------ | -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `rabbitmq`           | `rabbitmq:3.13-management`     | Celery broker                          | Enables Celery queueing and routing; exposes AMQP and the management UI. ([GitHub][4])                                                                                         |
| `celery-worker`      | MoonMind image                 | Default Celery worker                  | Runs `celery -A celery_worker.speckit_worker worker ...` and listens on a configurable queue. ([GitHub][4])                                                                    |
| `celery-codex-0/1/2` | MoonMind image                 | Sharded Celery workers                 | Three explicit queue shards (`codex-0`, `codex-1`, `codex-2`) for parallelism/throughput and routing control. Each shard can have its own auth volume. ([GitHub][4])           |
| `orchestrator`       | `moonmind/orchestrator:latest` | “mm-orchestrator” control-plane worker | Designed to run orchestration jobs: analyze failures, patch repo, build/restart compose services, verify/rollback. Can be driven by Celery jobs (or equivalent). ([GitHub][9]) |
| `docker-proxy`       | docker-socket-proxy            | Restricted Docker API access           | Used to let orchestrator/workers safely call the Docker daemon without handing over the raw socket broadly. ([GitHub][4])                                                      |
| `job`                | MoonMind build context         | Utility/development job container      | A general-purpose container meant to be overridden to run job scripts against the repo in a controlled environment. ([GitHub][4])                                              |

**When you use this stack:**
When you want **asynchronous orchestration** (fan-out/fan-in, task chains, retries, queue routing) over RabbitMQ, rather than only the API-managed task queue. The project’s memory architecture explicitly calls out “Background jobs: Celery + RabbitMQ” as a baseline primitive. ([GitHub][1])

---

### `docker-compose.downloader.yaml` — Model downloader

| Service      | Purpose                                                                                                                                                                                                   |
| ------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `downloader` | Runs a scripted entrypoint (e.g., `/app/tools/get-qwen.sh`) to populate `model_data/` with required model artifacts. Useful for air-gapped-ish workflows or reproducible local model setup. ([GitHub][5]) |

---

### `docker-compose.test.yaml` — Test harness

| Service              | Purpose                                                                                                                                        |
| -------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| `pytest`             | Runs unit tests inside the project image with test dependencies enabled. ([GitHub][6])                                                         |
| `orchestrator-tests` | Runs integration tests focused on orchestrator workflows. ([GitHub][6])                                                                        |
| `cli-tooling-smoke`  | Verifies CLI tooling is present (`codex --version`, `speckit --version`), which is important because workers rely on these CLIs. ([GitHub][6]) |

---

## Task execution model

MoonMind implements an API-driven **Task Queue System** for “agent jobs”:

* Jobs can include **attachments**, with explicit separation between user-provided inputs (`inputs/`) and worker-generated artifacts; attempts to upload outputs that masquerade as inputs are rejected. ([GitHub][2])
* Workers claim jobs under explicit **eligibility rules** (type allowed, repo allowed, capabilities satisfied) and use a lease/heartbeat model. ([GitHub][2])
* Each job runs through a staged lifecycle: `prepare → execute → publish` (with publish optional). ([GitHub][2])

This design gives you:

* Horizontal scaling by adding worker replicas
* Runtime specialization (a Gemini worker can advertise `["gemini","git","gh"]` while a “universal” worker can advertise multiple runtimes) ([GitHub][2])
* Better operational controls (stale lease detection, cancellation/cloning patterns) ([GitHub][2])

---

## Memory & RAG architecture

MoonMind’s memory architecture doc frames “memory” as an accelerator that is never a hard dependency (“fail-open”), while remaining audit-friendly and repo-scoped. ([GitHub][1])

### Baseline primitives (current foundations)

The “current state” called out in the memory doc includes:

* **Document retrieval (RAG)**: **LlamaIndex + Qdrant** powering chat and `/context` ([GitHub][1])
* **Durable execution state**: Postgres tables for workflows/runs/jobs ([GitHub][1])
* **Durable artifacts**: filesystem artifact roots (logs, patches, outputs) ([GitHub][1])
* **Background jobs**: Celery + RabbitMQ for async orchestration ([GitHub][1])

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

## Orchestrator architecture (mm-orchestrator)

MoonMind’s orchestrator architecture doc describes an “operator agent” container that can:

* Interpret high-level instructions
* Safely modify code/Dockerfiles
* Rebuild and relaunch services in a compose stack
* Verify health and rollback if necessary ([GitHub][9])

It explicitly chooses **Docker-outside-of-Docker (DooD)** (mount the host Docker socket) as the control mechanism, using `docker compose` as the control plane. ([GitHub][9])

Operational implication:

* This enables powerful self-healing/automation on a single host (no Kubernetes required), but it also requires careful security controls because Docker socket access is effectively host-root access. ([GitHub][9])

---

## Key libraries and why they matter in MoonMind

MoonMind’s dependency set (Poetry) is a strong signal of its architectural choices. ([GitHub][8])

### API & service framework

* **FastAPI + Starlette + Uvicorn**: high-performance ASGI API server foundation, async-friendly for streaming, webhooks, and integrating external providers. ([GitHub][8])
* **Pydantic + pydantic-settings**: typed request/response models, config validation, and “schema-first” API ergonomics. Crucial for complex systems like queue job payloads, runtime policies, and manifest-driven RAG. ([GitHub][8])
* **orjson**: fast JSON serialization/deserialization for high-throughput endpoints (chat, queue events, task status). ([GitHub][8])
* **structlog**: structured logs that can be enriched with job IDs, run IDs, worker IDs—vital for debugging agent systems. ([GitHub][8])

### Task execution & orchestration

* **Celery**: async job execution engine with retries, routing, and concurrency. In MoonMind it’s explicitly positioned as the “background jobs” layer and is wired up with RabbitMQ in `docker-compose.job.yaml`. ([GitHub][1])
* **RabbitMQ** (container): broker that enables queue-based distribution, queue sharding, and operational introspection (management UI). ([GitHub][4])

Why Celery is valuable here (practically):

* Separates **slow/long-running** or **batch** workflows (indexing, spec workflows, orchestration plans) from the latency-sensitive API surface
* Enables horizontal scaling by queue type (e.g., Codex shards)
* Provides robust retry semantics for flaky external dependencies (LLMs, GitHub, Jira, Confluence, etc.)

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
* `@githubnext/spec-kit` (Spec Kit)
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

* **Scale workers horizontally** by runtime type (Codex vs Gemini vs Claude) according to demand; worker eligibility is enforced by capability/routing policy. ([GitHub][2])
* For Celery workloads, scale by **queue shard** (e.g., `celery-codex-0/1/2`) to isolate hotspots and reduce tail latency. ([GitHub][4])

### Security posture highlights

* Task queue design includes explicit rules to prevent leaking secrets into payloads and to avoid confusing user inputs with worker outputs. ([GitHub][2])
* Orchestrator/docker-outside-of-docker is powerful but sensitive; MoonMind recognizes this explicitly and recommends policy/safety measures around Docker socket access. ([GitHub][9])
* Optional OIDC via Keycloak exists for stronger identity management. ([GitHub][3])
