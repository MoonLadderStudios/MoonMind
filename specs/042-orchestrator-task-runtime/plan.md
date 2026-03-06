# Implementation Plan: Orchestrator Task Runtime Upgrade

**Branch**: `042-orchestrator-task-runtime` | **Date**: 2026-02-26 | **Spec**: `specs/042-orchestrator-task-runtime/spec.md`  
**Input**: Feature specification from `/specs/042-orchestrator-task-runtime/spec.md`

## Summary

Implement the Orchestrator Task Runtime upgrade as production runtime changes across API, persistence, dashboard, and worker execution paths, with validation coverage for aliases, unified task UX, step runtime behavior, and degraded-mode reconciliation. The selected orchestration mode is **runtime**; this plan keeps runtime-vs-docs behavior aligned by treating docs-only output as a failing outcome for this feature.

## Technical Context

**Language/Version**: Python 3.11 service code + vanilla JavaScript dashboard runtime (Node-driven JS tests)  
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy/Alembic, Agent Queue service contracts, orchestrator worker runtime, dashboard route/view-model infrastructure  
**Storage**: PostgreSQL orchestrator/queue tables and filesystem artifacts under `var/artifacts/workflow_runs/<run_id>`  
**Testing**: `./tools/test_unit.sh` (required), orchestrator integration flow via `docker compose -f docker-compose.test.yaml run --rm orchestrator-tests` (required for outage/resilience acceptance), and runtime scope validation via `validate-implementation-scope.sh` runtime checks  
**Target Platform**: Docker Compose MoonMind stack (`api`, `orchestrator`, `celery-worker`, `api-db`, `rabbitmq`)  
**Project Type**: Multi-service backend with static dashboard frontend  
**Performance Goals**: Unified task list/detail stays responsive with fan-out fetches; orchestrator execution continues through transient DB failures without dropping in-flight progress  
**Constraints**: Preserve `/orchestrator/runs*` compatibility while introducing `/orchestrator/tasks*`; reject unsafe skill args; enforce auth parity; maintain backward compatibility for `orchestrator_run` jobs during rollout  
**Scale/Scope**: End-to-end orchestrator runtime workflow including authoring, dispatch, persistence, monitoring, retries, and outage reconciliation

## Constitution Check

### Pre-Phase 0 Gate

- **I. One-Click Deployment with Smart Defaults**: **PASS**. Uses existing Compose stack and current defaults; no new mandatory external prerequisites.
- **II. Powerful Runtime Configurability**: **PASS**. Runtime selection, worker config, and queue behavior remain env/config driven; runtime mode choice is explicit.
- **III. Modular and Extensible Architecture**: **PASS**. Changes are scoped to existing module boundaries (`routers`, `repositories`, `queue_worker`, `dashboard`).
- **IV. Avoid Exclusive Proprietary Vendor Lock-In**: **PASS**. Orchestrator skill/runtime behavior remains adapter-based and portable across configured runtimes.
- **V. Self-Healing by Default**: **PASS (with implementation follow-through required)**. State-sink fallback exists; reconciliation completion is planned explicitly in this feature.
- **VI. Facilitate Continuous Improvement**: **PASS**. Task outcomes/events/artifacts remain structured and observable across queue + orchestrator flows.
- **VII. Spec-Driven Development Is the Source of Truth**: **PASS**. This plan and generated artifacts map directly to spec FR/SC requirements.
- **VIII. Skills Are First-Class and Easy to Add**: **PASS**. Explicit step skill IDs/args and grouped discovery are central to design.

### Post-Phase 1 Re-Check

- All principles remain **PASS** after design artifacts were produced (`research.md`, `data-model.md`, `contracts/*`, `quickstart.md`).
- No constitutional violations require complexity exceptions.

## Project Structure

### Documentation (this feature)

```text
specs/042-orchestrator-task-runtime/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── orchestrator-task-runtime.openapi.yaml
│   └── requirements-traceability.md
└── tasks.md                      # Execution-ordered implementation + validation checklist
```

### Source Code (repository root)

```text
api_service/
├── api/routers/
│   ├── orchestrator.py
│   ├── task_dashboard.py
│   └── task_dashboard_view_model.py
├── db/models.py
├── migrations/versions/
│   └── *orchestrator*.py
└── static/task_dashboard/dashboard.js

moonmind/
└── workflows/
    ├── agent_queue/
    │   └── job_types.py
    └── orchestrator/
        ├── queue_dispatch.py
        ├── queue_worker.py
        ├── repositories.py
        ├── serializers.py
        ├── services.py
        ├── skill_executor.py
        └── state_sink.py

tests/
├── contract/test_orchestrator_api.py
├── task_dashboard/
│   ├── test_queue_layouts.js
│   └── test_submit_runtime.js
└── unit/workflows/orchestrator/test_queue_worker.py
```

**Structure Decision**: Implement within existing orchestrator, queue, API, and dashboard modules. Do not introduce a separate orchestration service or new frontend framework.

## Phase 0 – Research Summary

`research.md` resolves all plan unknowns and records these selected directions:

1. Task-first naming with run compatibility aliases.
2. Unified list/detail via dashboard composition and normalized row contract.
3. Deterministic detail source resolution.
4. Runtime=orchestrator authoring with explicit steps + skills.
5. Grouped skill discovery for worker/orchestrator domains.
6. Canonical task/step persistence migration strategy.
7. Dual queue job type compatibility during rollout.
8. State-sink fallback + reconciliation as degraded-mode reliability strategy.
9. Explicit queue terminal reconciliation after connectivity restoration.
10. Runtime-mode scope gate as hard acceptance requirement.

## Phase 1 – Design Outputs

- **Data model**: `data-model.md` defines canonical entities (`OrchestratorTask`, `OrchestratorTaskStep`, `UnifiedTaskRow`, `OrchestratorStateSnapshot`) and lifecycle/validation rules.
- **API contract**: `contracts/orchestrator-task-runtime.openapi.yaml` defines task aliases, transitional IDs, step payloads, approvals/retry/artifacts, and grouped skill discovery.
- **Traceability matrix**: `contracts/requirements-traceability.md` maps `DOC-REQ-001` through `DOC-REQ-019` to FRs, implementation surfaces, and validation strategies.
- **Task coverage matrix**: `tasks.md` maintains deterministic `DOC-REQ-*` implementation + validation task mappings for runtime execution readiness.
- **Execution guide**: `quickstart.md` provides runtime validation flow, outage simulation, and required test commands.

## Implementation Strategy

### 1. Terminology migration + alias parity

- Keep `/orchestrator/runs*` active.
- Keep `/orchestrator/tasks*` as task-first canonical API entry points.
- Return both `taskId` and `runId` during migration window.
- Align dashboard labels/routes to task vocabulary.

### 2. Unified dashboard list/detail behavior

- Use `/tasks/list` and `/tasks/:taskId` as canonical routes.
- Maintain compatibility redirects from `/tasks/queue*` and `/tasks/orchestrator*`.
- Normalize queue + orchestrator rows into one shared rendering contract.
- Resolve detail source deterministically (`source` hint first, then queue probe, then orchestrator probe).

### 3. Orchestrator authoring parity with runtime-aware steps + skills

- Runtime=orchestrator submit path accepts ordered explicit `steps[]`.
- Each step requires stable `id`, `instructions`, and explicit `skill.id` (no `auto`).
- Keep queue capability fields available in shared submit UX; do not regress worker-mode payload behavior.
- Serve grouped skills from `/api/tasks/skills` (`worker` + `orchestrator`).

### 4. Persistence migration to task/step runtime model

- Evolve from legacy run-centric storage to canonical task/step model with arbitrary step counts.
- Preserve legacy compatibility surfaces during migration and define a deterministic retirement path for legacy run tables.
- Ensure per-step status/attempt/artifact linkage is queryable and stable.

### 5. Dispatch/worker compatibility and degraded execution

- Keep dispatch/worker support for both `orchestrator_task` and `orchestrator_run` payloads.
- Record state via DB-first sink with artifact fallback snapshots.
- Add/complete reconciliation routines that import artifact-backed state after DB recovery.
- Add/complete terminal queue-state retry/reconciliation after heartbeat/lease failure windows.

### 6. Security/auth parity

- Preserve approval-token gates and expiry semantics.
- Keep skill execution guardrails that reject arbitrary command-style args.
- Align orchestrator route auth dependency behavior with unified Mission Control expectations.

### 7. Validation and release gates

- Required command: `./tools/test_unit.sh`.
- Required command: `docker compose -f docker-compose.test.yaml run --rm orchestrator-tests`.
- Contract/dashboard/worker coverage must include aliases, unified routes, explicit-step execution, and degraded-mode resilience.
- Runtime scope gates must pass in runtime mode:
  - `validate-implementation-scope.sh --check tasks --mode runtime`
  - `validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`
- `DOC-REQ-*` gate: every `DOC-REQ-*` must keep at least one implementation task and one validation task mapped in `tasks.md`.

## Runtime vs Docs Mode Alignment

- Selected mode: **runtime**.
- Required deliverable class: production runtime code changes + validation tests.
- Rejected completion mode: docs/spec-only edits without runtime/test evidence.

## Risks & Mitigations

- **Risk**: Partial migration leaves task/run semantics inconsistent.
  - **Mitigation**: Keep alias parity tests and transitional ID assertions in contract coverage.
- **Risk**: Outage fallback writes snapshots but never reconciles.
  - **Mitigation**: Implement idempotent reconciliation and add outage-recovery integration tests.
- **Risk**: Source resolution ambiguity for same identifier across sources.
  - **Mitigation**: Respect explicit `source`, then deterministic probe ordering, and test both paths.
- **Risk**: Security regressions from step skill args.
  - **Mitigation**: Reuse/extend skill-arg validation and approval/auth regression tests.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| _None_ | — | — |
