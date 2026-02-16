# Unified CLI Worker Architecture (Single Queue + Runtime via .env)

Status: Proposed  
Owners: MoonMind Eng  
Last Updated: 2026-02-16

## 1. Summary

This design unifies MoonMind worker execution around:

- One shared worker image that includes `codex`, `gemini`, `claude`, and `speckit`.
- One RabbitMQ queue for AI execution jobs (`moonmind.jobs`).
- Runtime selection through environment (`MOONMIND_WORKER_RUNTIME`) plus per-job target runtime.
- Canonical `type="task"` queue payloads that are runtime-neutral and policy-safe.
- Automatic pre/execute/publish wrapper stages for each claimed Task.

Key requirement: `speckit` remains bundled in the same Docker image as the other CLIs.

## 2. Current State (Repo-Scoped)

Current deployment still contains runtime-specific routing and legacy queue payload types (`codex_exec`, `codex_skill`) in some docs and workers.

This adds operational complexity and blocks a consistent typed Task UI contract.

## 3. Goals

- Keep a single queue for worker jobs.
- Allow runtime mode selection by `.env` per worker container.
- Keep one base image for all AI CLIs plus `speckit`.
- Execute canonical Task jobs across runtimes with capability matching.
- Preserve compatibility with legacy job types during migration.

## 4. Non-Goals

- Replacing Celery, RabbitMQ, or PostgreSQL.
- Changing Spec Kit workflow semantics (`specify/plan/tasks/analyze/implement`).
- Guaranteeing identical output across all runtime adapters.

## 5. Architecture Decisions

### 5.1 Single Shared Worker Image

Use `api_service/Dockerfile` as the build source for API and worker services. Required CLIs in one image:

- `codex`
- `gemini`
- `claude`
- `speckit`

### 5.2 Single Queue

Use one default queue:

- `MOONMIND_QUEUE=moonmind.jobs`

Celery target:

- `task_default_queue = "moonmind.jobs"`
- `worker_prefetch_multiplier = 1`
- `task_acks_late = true`

Runtime-specific queues are deprecated after migration.

### 5.3 Runtime Mode via Environment

Each worker service selects behavior with:

- `MOONMIND_WORKER_RUNTIME=codex|gemini|claude|universal`

Interpretation:

- `codex`, `gemini`, `claude`: execute only matching runtime Tasks.
- `universal`: can execute any runtime, honoring `payload.targetRuntime`.

### 5.4 Canonical Task Contract

New queue submissions should use:

- `type = "task"`
- `payload` matching `docs/TaskArchitecture.md`

Top-level policy fields:

- `repository`
- `requiredCapabilities`
- `targetRuntime`
- `auth` (optional secret references only: `repoAuthRef`, `publishAuthRef`)
- `task` object (`instructions`, `skill`, `runtime`, `git`, `publish`)

### 5.5 Capability Matching

Before claim/execution, workers enforce capability eligibility:

- include runtime capability (`codex` | `gemini` | `claude`)
- include `git` for all Tasks
- include `gh` when `publish.mode = pr`
- include skill-required extras when applicable

Workers advertise capabilities through worker token policy and/or runtime config.

## 6. Detailed Design

### 6.1 Worker Boot Mode

Startup path must:

- read `MOONMIND_WORKER_RUNTIME`
- fail fast on invalid mode
- register runtime/capability metadata for claim filtering

Runner abstraction:

- `IRunner.execute(task_context) -> RunResult`
- `CodexRunner`
- `GeminiRunner`
- `ClaudeRunner`
- `UniversalRunner`

### 6.2 Wrapper Stage Plan (Per Task)

Each claimed Task executes the same stage plan:

1. `moonmind.task.prepare`
2. `moonmind.task.execute`
3. `moonmind.task.publish` (when `publish.mode != none`)

Required prepare behavior:

- isolate workspace (`repo/`, `home/`, `skills_active/`, `artifacts/`)
- materialize `.agents/skills -> skills_active`
- materialize `.gemini/skills -> skills_active`
- resolve default branch and effective working branch
- emit `task_context.json`

Required execute behavior:

- run selected adapter/runtime
- apply selected skill when `skill.id != auto`
- capture stdout/stderr logs
- emit patch artifact when changes exist

Required publish behavior:

- deterministic commit/push/PR logic owned by system stage

### 6.3 Runtime Override Precedence

Per-task runtime config precedence:

1. `task.runtime.model`, `task.runtime.effort`
2. worker defaults (`MOONMIND_*` env/config)
3. CLI defaults

### 6.4 Compose Topology

Target topology in `docker-compose.yaml`:

- one image build (`api_service/Dockerfile`)
- worker services differ by env values and scaling
- all subscribe to `moonmind.jobs`

Supported modes:

1. Homogeneous fleet (`MOONMIND_WORKER_RUNTIME=codex` on all replicas)
2. Mixed fleet (`codex`, `gemini`, `claude`, optional `universal`)

## 7. Migration Plan

### Phase 1: Image and Runtime Unification

- keep `speckit` in shared image
- ensure `codex`, `gemini`, `claude`, `speckit` health checks

### Phase 2: Canonical Task Payload

- update UI and producer paths to submit `type="task"`
- derive capabilities and target runtime into top-level payload fields

### Phase 3: Queue Consolidation

- switch workers to `moonmind.jobs`
- remove runtime-specific queue routes
- keep compatibility handlers for `codex_exec` and `codex_skill`

### Phase 4: Legacy Deprecation

- retire legacy payload submission paths
- keep compatibility shims internal until all producers are migrated

## 8. Observability and Operations

Required telemetry:

- queue wait and execution duration by runtime
- stage-level duration (`prepare`, `execute`, `publish`)
- claim rejections by missing capability
- publish outcomes (branch push / PR create / no changes)

Required artifacts:

- `logs/prepare.log`
- `logs/execute.log`
- `logs/publish.log` (when publish enabled)
- `patches/changes.patch`
- `task_context.json`
- `publish_result.json` (when publish enabled)

## 9. Acceptance Criteria

- Single image from `api_service/Dockerfile` contains `codex`, `gemini`, `claude`, `speckit`.
- Workers are selectable through `MOONMIND_WORKER_RUNTIME` only.
- Queue jobs submitted from Task UI use `type="task"` and canonical payload shape.
- `prepare -> execute -> publish` events appear for Task jobs.
- Legacy `codex_exec`/`codex_skill` jobs still execute during migration window.

## 10. References

- `docs/TaskArchitecture.md`
- `docs/CodexTaskQueue.md`
- `docs/WorkerGitAuth.md`
- `docs/SecretStore.md`
