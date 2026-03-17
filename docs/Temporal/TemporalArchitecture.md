# MoonMind Temporal Architecture

**Status:** Draft (migration-oriented)  
**Owner:** MoonMind Platform  
**Last updated:** 2026-03-16  
**Audience:** backend, infra, dashboard, workflow authors

## 1. Purpose

This document defines MoonMind's **Temporal migration architecture**:

- what MoonMind runs **today**
- what MoonMind is moving **toward**
- which design decisions are already **locked**
- how current task/orchestrator flows map into Temporal-native workflow execution

This is intentionally a **bridge document**. It does not pretend the current runtime is already fully Temporal-backed, and it does not duplicate every low-level decision from the more specific Temporal docs.

## 2. Related docs

- `docs/Temporal/TemporalPlatformFoundation.md`
- `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md`
- `docs/Temporal/ActivityCatalogAndWorkerTopology.md`
- `docs/Temporal/WorkflowArtifactSystemDesign.md`
- `docs/MoonMindArchitecture.md`
- `docs/OrchestratorArchitecture.md`
- `docs/OrchestratorTaskRuntime.md`

## 3. Current state and target state

### 3.1 Current state

MoonMind currently operates with:

- a FastAPI control plane
- Postgres-backed task/run state
- runtime-specific workers for Codex, Gemini, and Claude
- an agent/task queue model
- Celery + RabbitMQ automation for spec workflow and orchestrator paths
- `mm-orchestrator` for repo mutation, compose build/restart, verification, and rollback

This is the current documented and implemented direction in the repository, not legacy trivia.

### 3.2 Target state

MoonMind is migrating to **Temporal as the primary durable workflow manager and scheduling system** for workflow-driven automation.

In the target state:

- Temporal **Workflow Executions** are the durable orchestration primitive
- Temporal **Activities** perform all side effects
- Temporal **Visibility** becomes the list/query/count source for Temporal-managed work
- Temporal **Schedules** replace cron/beat-style scheduling for Temporal-managed flows
- MoonMind keeps only domain concepts Temporal does not provide directly:
  - **Skill**
  - **Plan**
  - **Artifact**

### 3.3 Non-goals

- Claiming Celery, the agent queue, or orchestrator task records are already gone
- Rewriting all public APIs around Temporal terms in one step
- Exposing Temporal task queues as a user-visible queue product with ordering guarantees

## 4. Locked platform decisions

The following are already locked by newer Temporal design docs and should be treated as canonical here:

- **Deployment mode:** self-hosted only, not Temporal Cloud
- **Deployment runtime:** Docker Compose
- **Temporal persistence + visibility:** PostgreSQL
- **Default artifact backend:** MinIO / S3-compatible object storage
- **Default worker versioning behavior:** Auto-Upgrade
- **Task queue posture:** routing only, not product semantics
- **Default queue set:** start small; add subqueues only when isolation or scaling demands it

This document should not re-open those choices.

## 5. Vocabulary and compatibility model

### 5.1 User-facing and internal terms

MoonMind needs two layers of vocabulary during migration:

- **User-facing term:** `task`
- **Temporal term:** `workflow execution`

Rationale:

- MoonMind's current UI and API direction remains task-oriented
- Temporal's runtime model is workflow-oriented
- forcing an immediate full rename would conflict with current product contracts and migration specs

### 5.2 Recommended wording

- Use **task** in current dashboard and compatibility APIs
- Use **workflow execution** in Temporal implementation docs and internal runtime contracts
- Use **task queue** only for Temporal plumbing, never as a user-visible ordering promise

### 5.3 Identifier compatibility

During migration, the system may expose more than one identifier:

| Identifier | Meaning | Status |
| --- | --- | --- |
| `taskId` | Current MoonMind task handle used by UI and compatibility APIs | Required during migration |
| `runId` | Legacy orchestrator compatibility identifier | Transitional only |
| `workflowId` | Temporal Workflow ID | Canonical for Temporal-managed executions |
| `temporalRunId` | Temporal run instance identifier | Detail/debug use only |

Rules:

- Public task APIs may return both `taskId` and `workflowId` during migration
- Legacy orchestrator APIs may still require `runId` compatibility for a period
- `workflowId` is the durable Temporal identity
- `temporalRunId` is not the primary product handle

## 6. Architecture overview

### 6.1 Current deployment shape

MoonMind currently contains:

- API service
- Postgres
- Qdrant
- runtime workers
- Celery + RabbitMQ stack
- `mm-orchestrator`
- Temporal foundation services in Compose

### 6.2 Target Temporal shape

For Temporal-managed flows, the architecture becomes:

1. **MoonMind API**
   - authenticates callers
   - starts workflows
   - sends updates/signals/cancel requests
   - issues artifact upload/download grants
   - exposes task-oriented compatibility surfaces while Temporal adoption is in progress

2. **Temporal service**
   - stores workflow state/history
   - provides visibility for list/query/count
   - owns timers, retries, schedules, child workflow orchestration

3. **Artifact system**
   - stores large inputs/outputs outside workflow history
   - links artifacts to workflow executions
   - enforces retention, previews, and access control

4. **Temporal workers**
   - workflow workers run deterministic orchestration code
   - activity workers execute side-effecting work under capability and secret boundaries

5. **Compatibility adapters**
   - bridge current task/orchestrator APIs and UI surfaces onto Temporal-backed workflows where needed

### 6.3 Container reference

All Temporal-related containers are defined in `docker-compose.yaml`. The table below summarises every container, followed by detailed descriptions grouped by role.

#### Quick reference

| Container | Image | Always on? | Purpose |
| --- | --- | --- | --- |
| `temporal` | `temporalio/auto-setup` | Yes | Temporal server (history, matching, frontend, internal-frontend) |
| `temporal-namespace-init` | `temporalio/admin-tools` | Yes (run-once) | Creates/updates the `moonmind` namespace and registers custom search attributes |
| `temporal-visibility-rehearsal` | `temporalio/admin-tools` | No (`temporal-tools` profile) | Dry-runs a visibility-schema upgrade against Postgres |
| `temporal-admin-tools` | `temporalio/admin-tools` | No (`temporal-tools` profile) | Long-running shell for ad-hoc `temporal` / `tctl` commands |
| `temporal-ui` | `temporalio/ui` | No (`temporal-ui` profile) | Web dashboard for browsing workflows, activities, and schedules |
| `temporal-worker-workflow` | `moonmind` (app image) | Yes | Runs deterministic workflow code on `mm.workflow` |
| `temporal-worker-artifacts` | `moonmind` (app image) | Yes | Artifact I/O activities on `mm.activity.artifacts` |
| `temporal-worker-llm` | `moonmind` (app image) | Yes | LLM-call activities on `mm.activity.llm` |
| `temporal-worker-sandbox` | `moonmind` (app image) | Yes | Shell/repo/CLI activities on `mm.activity.sandbox` |
| `temporal-worker-agent-runtime` | `moonmind` (app image) | Yes | Agent orchestration activities on `mm.activity.agent_runtime` |
| `temporal-worker-integrations` | `moonmind` (app image) | Yes | External-service activities on `mm.activity.integrations` |
| `postgres` | `postgres:17` | Yes | Shared database (MoonMind tables + Temporal persistence + visibility) |
| `minio` | `minio/minio` | Yes | S3-compatible object store for workflow artifacts |

#### 6.3.1 Core Temporal infrastructure

##### `temporal`

The Temporal Server container, using the official `temporalio/auto-setup` image. On first start it auto-creates the Temporal and visibility databases in Postgres, then runs the server with all four internal services (history, matching, frontend, internal-frontend) in a single process.

Key configuration:

- **`DB=postgres12`** — selects the Postgres persistence plugin.
- **`POSTGRES_SEEDS=postgres`** — points at the shared Postgres container via the `temporal-db` network alias.
- **`DBNAME` / `VISIBILITY_DBNAME`** — separate logical databases (`temporal` / `temporal_visibility`) created by `init_db_scripts`.
- **`DYNAMIC_CONFIG_FILE_PATH`** — loads `services/temporal/dynamicconfig/development-sql.yaml`, which currently enables `UpdateWorkflowExecution` and disables secondary visibility reads.
- **`NUM_HISTORY_SHARDS=1`** — single-node development default.
- Exposes **port 7233** (gRPC) only on the Docker network via the `temporal-internal` alias.

Dependencies: `postgres` (healthy).

##### `postgres`

A single Postgres 17 instance shared across MoonMind, Temporal persistence, and Temporal visibility. The `init_db_scripts/` directory contains startup SQL that creates dedicated databases and roles:

| Database | Owner role | Purpose |
| --- | --- | --- |
| `moonmind` | `postgres` | MoonMind API tables, migrations, Alembic history |
| `temporal` | `temporal` | Temporal workflow persistence (history, shards, tasks) |
| `temporal_visibility` | `temporal` | Temporal visibility store (search attributes, list/count queries) |
| `keycloak` | `keycloak` | Keycloak IdP state (when `keycloak` profile is active) |

The container exposes **5432** only on the Docker network and publishes two network aliases: `moonmind-api-db` (used by the API) and `temporal-db` (used by Temporal).

##### `minio`

MinIO provides the S3-compatible object store used by Temporal workflow artifacts. The `temporal-worker-artifacts` container connects to MinIO on **port 9000** for blob read/write. The MinIO Console is available on **port 9001** (mapped to host) for debugging.

#### 6.3.2 Init and tooling containers

##### `temporal-namespace-init` (run-once)

Runs `services/temporal/bootstrap-namespace.sh` on startup. This script:

1. Waits for the Temporal server to become healthy (up to 90 retries).
2. Creates the `moonmind` namespace if it does not exist, or updates retention if it does.
3. Derives retention days from `TEMPORAL_RETENTION_MAX_STORAGE_GB` and `TEMPORAL_RETENTION_ESTIMATED_GB_PER_DAY` unless an explicit `TEMPORAL_NAMESPACE_RETENTION_DAYS` is set.
4. Registers MoonMind custom search attributes (`mm_entry`, `mm_owner_id`, `mm_owner_type`, `mm_state`, `mm_updated_at`, `mm_repo`, `mm_integration`, `mm_continue_as_new_cause`).

Restart policy: `no` — exits after one successful run.

##### `temporal-visibility-rehearsal` (profile: `temporal-tools`)

A one-shot container that dry-runs a Temporal visibility schema upgrade against the Postgres visibility database. Used for upgrade planning and CI validation before applying schema changes in production.

Restart policy: `no`.

##### `temporal-admin-tools` (profile: `temporal-tools`)

A long-running container that sleeps indefinitely, providing a shell with `temporal` and `tctl` CLIs pre-installed. Operators can `docker exec` into it for ad-hoc namespace, schedule, search-attribute, and workflow management.

##### `temporal-ui` (profile: `temporal-ui`)

The official Temporal Web UI. Provides a browser dashboard for inspecting running and completed workflow executions, activity details, task queue status, and schedules. Mapped to **host port 8088** by default.

#### 6.3.3 Auth and workspace init containers

These lightweight Alpine containers initialise Docker volumes with correct ownership (`1000:1000`) and permissions (`0775`) so that worker containers running as a non-root user can write to them. All have restart policy `no`.

| Container | Volume | Purpose |
| --- | --- | --- |
| `agent-workspaces-init` | `agent_workspaces` | `/work/agent_jobs` — per-run workspaces for agent task execution |
| `codex-auth-init` | `codex_auth_volume` | Codex CLI auth credentials and config |
| `claude-auth-init` | `claude_auth_volume` | Claude CLI auth credentials and config |
| `gemini-auth-init` | `gemini_auth_volume` | Gemini CLI auth credentials and config |

The sandbox and agent-runtime workers depend on all four init containers completing before they start.

#### 6.3.4 Worker fleet

All workers share the same `moonmind` application image (`ghcr.io/moonladderstudios/moonmind:latest`), use the `services/temporal/scripts/start-worker.sh` entrypoint, and differ only in the **`TEMPORAL_WORKER_FLEET`** environment variable and the task queue they poll. Each worker exposes an HTTP health endpoint at `:8080/healthz`.

##### `temporal-worker-workflow`

- **Fleet:** `workflow`
- **Task queue:** `mm.workflow`
- **Concurrency:** 8 (default)
- **Role:** Runs deterministic Temporal workflow code. This worker executes the orchestration logic (state machines, child workflow spawning, signal/update handling) and must never perform side-effecting I/O directly.
- **Volumes:** Read-only `moonmind/` and `api_service/` source mounts.

##### `temporal-worker-artifacts`

- **Fleet:** `artifacts`
- **Task queue:** `mm.activity.artifacts`
- **Concurrency:** 8 (default)
- **Role:** Executes artifact lifecycle activities — upload, download, list, delete, and retention enforcement against MinIO.
- **Key env:** `TEMPORAL_ARTIFACT_BACKEND=s3`, S3 endpoint/bucket/credentials pointing at the `minio` container.
- **Depends on:** `minio` in addition to Temporal.

##### `temporal-worker-llm`

- **Fleet:** `llm`
- **Task queue:** `mm.activity.llm`
- **Concurrency:** 4 (default)
- **Role:** Executes LLM API call activities. Holds API keys for OpenAI, Gemini/Google, and Anthropic.
- **Key env:** `OPENAI_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `ANTHROPIC_API_KEY`.
- **Note:** Provider-specific sub-queues (e.g. `mm.activity.llm.codex`) are not configured by default; add them only when isolation or separate scaling is needed.

##### `temporal-worker-sandbox`

- **Fleet:** `sandbox`
- **Task queue:** `mm.activity.sandbox`
- **Concurrency:** 2 (default)
- **Role:** Executes shell commands, repo checkouts, and agent CLI invocations (Codex, Claude, Gemini). This is the primary worker for running external code-generation agents against checked-out repositories.
- **Volumes:** `codex_auth_volume`, `claude_auth_volume`, `gemini_auth_volume` (CLI credentials), `agent_workspaces` (per-job working directories).
- **Depends on:** All four auth/workspace init containers plus Temporal.

##### `temporal-worker-agent-runtime`

- **Fleet:** `agent_runtime`
- **Task queue:** `mm.activity.agent_runtime`
- **Concurrency:** 4 (default)
- **Role:** Executes higher-level agent orchestration activities — task dispatch, status polling, result collection — that coordinate the sandbox worker's lower-level CLI execution.
- **Volumes:** Same auth and workspace volumes as the sandbox worker.
- **Depends on:** Same auth and workspace init containers as the sandbox worker.

##### `temporal-worker-integrations`

- **Fleet:** `integrations`
- **Task queue:** `mm.activity.integrations`
- **Concurrency:** 4 (default)
- **Role:** Executes activities for external service integrations (GitHub, Jules, webhooks).
- **Key env:** `JULES_ENABLED`, `JULES_API_URL`, `JULES_API_KEY`.

#### 6.3.5 Dependency and startup order

```
postgres (healthy)
├── temporal (started)
│   └── temporal-namespace-init (completed)
│       ├── temporal-worker-workflow
│       ├── temporal-worker-artifacts  (+ minio started)
│       ├── temporal-worker-llm
│       ├── temporal-worker-sandbox    (+ auth/workspace inits completed)
│       ├── temporal-worker-agent-runtime (+ auth/workspace inits completed)
│       ├── temporal-worker-integrations
│       ├── temporal-ui               (profile: temporal-ui)
│       ├── temporal-admin-tools       (profile: temporal-tools)
│       └── temporal-visibility-rehearsal (profile: temporal-tools)
└── init-db (completed) → api
```

#### 6.3.6 Networking

All Temporal containers join the `local-network` Docker network. Internal service discovery uses container names and network aliases:

| Alias | Target | Used by |
| --- | --- | --- |
| `temporal-internal` | `temporal` | All workers, API, namespace-init, admin tools, UI |
| `temporal-db` | `postgres` | `temporal` (auto-setup persistence connection) |
| `moonmind-api-db` | `postgres` | `api`, `init-db` |

No Temporal ports are published to the host by default. The `temporal-ui` container optionally publishes **8088→8080**.

## 7. Migration phases

### Phase 0: Current production path

- Queue tasks, Celery automation, and orchestrator task records remain canonical for current runtime features
- Temporal foundation may be present but is not yet the primary execution substrate

### Phase 1: Temporal foundation

- Stand up self-hosted Temporal in Docker Compose
- Register namespace and retention policy
- Validate Postgres visibility and upgrade playbooks
- Establish artifact store and worker topology contracts

### Phase 2: Temporal-backed workflow introduction

- Introduce first workflow types behind MoonMind APIs
- preserve current task-oriented UI and compatibility API surfaces
- map task actions to workflow start/update/signal/cancel operations

### Phase 3: Temporal as primary workflow engine

- new durable orchestration flows start on Temporal by default
- Temporal Visibility becomes the source of truth for Temporal-managed list/detail surfaces
- compatibility adapters remain for legacy queue/orchestrator records still in flight

### Phase 4: Legacy decommission

Only after parity is proven:

- remove Celery paths that Temporal has replaced
- remove duplicated queue leasing/state logic where Temporal is authoritative
- retire transitional identifier and route compatibility

## 8. Concept mapping: current MoonMind to Temporal

| Current concept | Temporal-aligned concept | Notes |
| --- | --- | --- |
| Queue task / orchestrator task | Workflow Execution plus MoonMind compatibility row | User may still see `task` while runtime is a workflow |
| `ActionPlan` | `Plan` artifact plus workflow orchestration logic | Plan stays a MoonMind domain concept |
| Step | Plan node, activity call, or child workflow | Depends on isolation and retry boundary |
| `prepare -> execute -> publish` | workflow phases reflected in `mm_state` and artifacts | Keep phase meaning, change substrate |
| Approval token / approval gate | Signal or Update plus workflow policy check | Approval remains a product policy concept |
| Worker runtime selection | Activity routing and worker capability binding | Not a new workflow taxonomy |
| DB state sink snapshot | Artifact-backed fallback plus reconciliation | Relevant during migration and degraded mode |

## 9. Workflow model

### 9.1 Root-level workflow types

MoonMind should keep **few** workflow types and avoid rebuilding legacy taxonomies inside Temporal.

Initial catalog:

- `MoonMind.Run`
- `MoonMind.ManifestIngest`

`MoonMind.Run` is the general entry point for:

- direct skill execution
- plan-driven execution
- external integrations
- long-lived waiting and callback handling

`MoonMind.ManifestIngest` exists only because manifest-driven orchestration usually introduces:

- graph compilation
- fan-out / fan-in
- result aggregation
- explicit failure policy

### 9.2 What not to do

Do not create separate workflow type families just for:

- Codex vs Gemini vs Claude
- queue task vs orchestrator task
- worker brand or provider choice

Those are routing and execution concerns, not root orchestration categories.

## 10. Activity model and worker topology

### 10.1 Core rule

All side effects belong in **Activities**:

- LLM calls
- filesystem and repo operations
- shell/sandbox execution
- artifact IO
- GitHub and other integrations
- callback verification and external polling

Workflow code remains deterministic.

### 10.2 Minimal task queue set

Start with a small queue topology:

- `mm.workflow`
- `mm.activity.artifacts`
- `mm.activity.llm`
- `mm.activity.sandbox`
- `mm.activity.integrations`

Provider-specific queues such as `mm.activity.llm.codex` are **deferred**, not default. Add them only when operations require stronger isolation, separate scaling, or distinct secrets/egress.

### 10.3 Routing model

Routing is by capability and security boundary, not by legacy nouns:

- LLM workers for model/provider activity execution
- sandbox workers for command and repo operations
- integration workers for GitHub/Jules/webhook interactions
- artifact workers for object-store lifecycle work

## 11. Payloads and artifacts

### 11.1 Payload discipline

Temporal payloads and history should remain small.

Use:

- `ArtifactRef` values for large inputs/outputs
- small JSON for workflow parameters, patches, and summaries

Do not put these into workflow history:

- prompts
- manifests
- diffs
- generated files
- logs
- large command output

### 11.2 Artifact system baseline

The default MoonMind artifact path for Temporal-managed work is:

- MinIO / S3-compatible blob storage for bytes
- Postgres metadata/index for artifact records and execution linkage

### 11.3 Manifest processing best practice

Manifest workflows should exchange **refs**, not blobs:

1. `artifact.read(manifest_ref)` only inside an activity boundary
2. `manifest.parse(manifest_ref) -> parsed_ref`
3. `manifest.validate(parsed_ref) -> validated_ref`
4. `manifest.compile_to_plan(validated_ref) -> plan_ref`
5. execute inline or spawn child workflows using `plan_ref`

That keeps workflow state small and makes retries safer.

## 12. Visibility and UI model

### 12.1 Current product reality

MoonMind's product surface is still task-oriented. Near-term list/detail experiences may need to unify:

- queue-backed tasks
- orchestrator-backed tasks
- Temporal-backed workflow executions

### 12.2 Target Temporal model

For Temporal-managed work, Temporal Visibility is the list/query/count source of truth.

Canonical search attributes and memo fields should align with the newer workflow lifecycle doc:

**Search Attributes**

- `mm_owner_id`
- `mm_state`
- `mm_updated_at`
- `mm_entry`
- optional bounded fields such as `mm_repo` or `mm_integration`

**Memo**

- `title`
- `summary`
- optional input or manifest refs when safe

### 12.3 UI contract during migration

- Keep `/tasks/*` user flows where required by active product work
- allow a task row to map to a Temporal workflow execution behind the scenes
- avoid inventing queue-order semantics for list sorting or status interpretation
- use Temporal page tokens and count semantics only for Temporal-backed queries

## 13. Public API posture

### 13.1 During migration

Public APIs should remain compatible with current task/orchestrator contracts where active product specs require that compatibility.

That includes:

- `/tasks/*` list/detail flows
- `/orchestrator/tasks*` compatibility routes
- `/orchestrator/runs*` transitional support where still required

### 13.2 Temporal-backed operations

When a task is backed by Temporal, MoonMind should map public actions to Temporal-native controls:

- create/start -> start workflow execution
- edit -> update
- approval or webhook event -> signal or update, depending on response needs
- cancel -> workflow cancellation
- rerun -> continue-as-new or explicit new workflow start, depending on semantics

### 13.3 Internal versus external API

If MoonMind later adds a direct `/executions` API surface, it should be treated as either:

- an internal adapter API first, or
- a future public API after compatibility needs are retired

This document does not assume that `/executions` is already the primary public product surface.

## 14. Edits, approvals, and external events

### 14.1 Updates

Preferred workflow updates include:

- `UpdateInputs`
- `SetTitle`
- `RequestRerun`

Use Updates when the caller needs a request/response result and acceptance decision.

### 14.2 Signals

Use Signals for asynchronous events such as:

- approval arrival
- GitHub/Jules/webhook callbacks
- pause/resume requests
- external completion notifications

### 14.3 Approval policy

Approval remains a MoonMind policy concern, not a Temporal-native concept. Temporal provides the transport and durability; MoonMind workflows still enforce:

- who may approve
- when approval is required
- what happens on expiry or rejection

## 15. Scheduling and long-lived monitoring

For Temporal-managed flows:

- use Temporal Timers for deterministic waiting
- use Temporal Schedules for recurring starts
- prefer callback-first integration patterns
- use timer-based polling only as a fallback when callbacks are unavailable

The legacy DB-polling `moonmind-scheduler` has been removed. Temporal Schedules and Timers are now the authoritative mechanism for all recurring and time-based workflow starts.

## 16. Reliability and security

### 16.1 Reliability rules

- activity retries are the default recovery mechanism
- side-effecting activities must be idempotent or keyed for safe retry
- use Continue-As-New to control workflow history growth
- keep large payloads out of workflow history

### 16.2 Security and isolation

- Temporal is self-hosted and private-network only
- worker fleets are segmented by capability and secret boundary
- sandbox execution is isolated separately from LLM and integration workers
- artifact access is mediated through MoonMind authorization and short-lived grants

## 17. Decommission criteria for legacy systems

Do not remove Celery, the agent queue, or orchestrator persistence just because a Temporal design exists on paper.

Retirement should require all of the following for a given flow:

1. Temporal implementation is production-ready
2. Observability, retries, artifacts, and approvals have parity
3. UI and API compatibility behavior is preserved or intentionally retired
4. Degraded-mode and rollback behavior are verified
5. Migration of in-flight and historical records is either complete or intentionally scoped out

## 18. Open decisions to lock next

1. Which MoonMind flows migrate to Temporal first after the foundation work
2. Whether task list/detail stays a unified multi-source surface through the full transition or gains Temporal-first views earlier
3. Which approval paths should use Updates versus Signals
4. When child workflows are preferred over inline orchestration for manifest fan-out
5. When, if ever, provider-specific LLM task queues become operationally necessary

## 19. Summary

MoonMind is not yet a purely Temporal product, but it is intentionally moving in that direction. The correct architecture stance is:

- acknowledge the current task/orchestrator runtime honestly
- adopt Temporal-native workflow, activity, visibility, and schedule concepts where they are now the target
- keep public compatibility layers explicit during migration
- avoid reintroducing MoonMind-specific abstractions where Temporal already provides the right primitive
