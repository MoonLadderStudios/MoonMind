# Unified CLI Worker Architecture (Single Queue + Runtime via .env)

Status: Proposed  
Owners: MoonMind Eng  
Last Updated: February 16, 2026

## 1. Summary

This design unifies MoonMind worker execution around:

- One shared worker image that includes `codex`, `gemini`, `claude`, and `speckit`.
- One RabbitMQ queue for AI execution jobs (`moonmind.jobs`).
- Runtime selection through environment (`MOONMIND_WORKER_RUNTIME`) instead of queue-level routing.
- A runtime-neutral job contract so any worker can execute any job without requeue thrash.

Key requirement: `speckit` remains bundled in the same Docker image as the other CLIs, not as a separate image or install path.

## 2. Current State (Repo-Scoped)

Current MoonMind deployment is split by runtime/queue:

- `docker-compose.yaml` defines separate worker services and queues (`speckit`, `codex`, `gemini`).
- `api_service/Dockerfile` already bundles `codex`, `gemini`, and `speckit`.
- No Claude Code CLI install path exists yet in the shared runtime image.

This causes operational complexity when scaling mixed fleets and adds queue-routing maintenance overhead.

## 3. Goals

- Keep a single queue for worker jobs.
- Allow runtime mode selection by `.env` per worker container.
- Make job payloads portable across runtimes by default.
- Keep one base image for all AI CLIs plus `speckit`.
- Preserve non-interactive automation and existing artifact persistence patterns.

## 4. Non-Goals

- Replacing Celery, RabbitMQ, or PostgreSQL.
- Changing Spec Kit workflow semantics (`specify/plan/tasks/analyze/implement`).
- Forcing targeted-runtime routing for all jobs.

## 5. Architecture Decisions

### 5.1 Single Shared Worker Image

Use `api_service/Dockerfile` as the single build source for API and worker services.  
Extend current CLI tooling stage to also install Claude Code CLI with pinned version args.

Required CLIs in one image:

- `codex`
- `gemini`
- `claude`
- `speckit`

Constraint:

- `speckit` installation remains in this Dockerfile and is never split into a second image.

### 5.2 Single Queue

Define one default queue for runtime-neutral jobs:

- `MOONMIND_QUEUE=moonmind.jobs`

Celery config target:

- `task_default_queue = "moonmind.jobs"`
- `worker_prefetch_multiplier = 1`
- `task_acks_late = true`

Dedicated runtime queues (`codex`, `gemini`) are deprecated after migration.

### 5.3 Runtime Mode via Environment

Each worker service selects behavior with:

- `MOONMIND_WORKER_RUNTIME=codex|gemini|claude|universal`

Interpretation:

- `codex`, `gemini`, `claude`: default runner for claimed jobs.
- `universal`: can dispatch per job using optional target runtime metadata.

### 5.4 Runtime-Neutral Job Contract

All jobs published to `moonmind.jobs` must be runtime-agnostic by default:

- describe objective, repo/ref, constraints, artifacts, and limits.
- avoid provider-specific command fields in the base payload.

Optional targeted execution (only when needed):

- `task.target_runtime` in payload.
- honored only by `universal` mode workers.

## 6. Detailed Design

### 6.1 Dockerfile Changes

File: `api_service/Dockerfile`

Add:

- build arg for Claude CLI version (for example `CLAUDE_CLI_VERSION`).
- install branch with fallback stub pattern matching existing Codex/Gemini/Speckit strategy.
- runtime copy/chmod/version checks for `claude`.

Keep:

- existing global install and verification for `speckit`.
- existing behavior where missing registries can fall back to explicit stub binaries.

Outcome:

- one reproducible worker image for all runtimes and `speckit`.

### 6.2 Worker Boot Mode

Introduce runtime selector in worker startup path (entrypoint or worker bootstrap module):

- read `MOONMIND_WORKER_RUNTIME`.
- map to selected runner implementation.
- fail fast on invalid mode values.

Runner abstraction:

- `IRunner.execute(job) -> RunResult`
- `CodexRunner`
- `GeminiRunner`
- `ClaudeRunner`
- `UniversalRunner` (delegates to one of the above when target runtime is provided)

### 6.3 Queue and Task Routing

Update Celery configuration under `moonmind/workflows/speckit_celery/` so producer and workers share:

- queue name: `moonmind.jobs`
- no runtime-specific task routes

Existing orchestration task chains remain; only queue/routing policy changes.

### 6.4 Compose Topology

File: `docker-compose.yaml`

Target topology:

- keep one image build (`api_service/Dockerfile`).
- worker services differ only by env values and scaling.
- all worker services subscribe to `moonmind.jobs`.

Two supported deployment modes:

1. Homogeneous fleet (`MOONMIND_WORKER_RUNTIME=codex` for all worker replicas).
2. Mixed fleet (`codex`, `gemini`, `claude`, and optional `universal` services together).

### 6.5 Auth and Secrets

Do not bake credentials into the image.

Mount runtime-specific auth/config volumes:

- Codex config directory
- Gemini config directory
- Claude config directory

Or provide secrets through env at runtime:

- `OPENAI_*`
- `GOOGLE_*`
- `ANTHROPIC_*`

## 7. Migration Plan

### Phase 1: Image Unification

- Add Claude CLI installation path to `api_service/Dockerfile`.
- Keep `speckit` in the same Dockerfile.
- Validate startup probes for all four CLIs.

### Phase 2: Runtime-Neutral Contract

- Introduce provider-neutral job payload schema.
- Implement runner abstraction and runtime selection by env.

### Phase 3: Queue Consolidation

- Switch workers to `moonmind.jobs`.
- Remove runtime-specific queue bindings from compose/config.
- Keep compatibility shim for legacy queued jobs during rollout window.

### Phase 4: Cleanup

- Retire deprecated queue env vars (`SPEC_WORKFLOW_CODEX_QUEUE`, `GEMINI_CELERY_QUEUE`) after stable cutover.

## 8. Observability and Operations

Required telemetry additions:

- job count/latency by selected runtime
- runner failures by error class
- queue wait time and execution duration
- fallback/stub CLI detection at startup

Required startup checks:

- `codex --version`
- `gemini --version`
- `claude --version`
- `speckit --version`

Workers should not accept jobs when required CLIs are missing or unhealthy.

## 9. Risks and Mitigations

- CLI package naming/version drift:
  - Mitigation: pin versions, enforce build-time checks, document fallback stubs.
- Runtime behavior drift for identical jobs:
  - Mitigation: shared runner contract and normalized artifact schema.
- Queue cutover regressions:
  - Mitigation: phased rollout with temporary compatibility routing.

## 10. Acceptance Criteria

- A single Docker image built from `api_service/Dockerfile` contains `codex`, `gemini`, `claude`, and `speckit`.
- `speckit` is not installed via a separate image pipeline.
- Workers can be switched between runtime modes using only `.env`.
- All AI jobs are consumable from one queue (`moonmind.jobs`) without requeue thrash.
- Mixed-runtime and homogeneous fleets both operate with the same queue and job schema.

