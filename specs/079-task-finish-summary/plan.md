# Implementation Plan: Task Finish Summary System

**Branch**: `041-task-finish-summary` | **Date**: 2026-02-24 | **Spec**: `specs/041-task-finish-summary/spec.md`  
**Input**: Feature specification from `/specs/041-task-finish-summary/spec.md`

## Summary

Implement a runtime-grade Task Finish Summary system that introduces deterministic terminal outcome classification, structured finish summary persistence, and dashboard/operator visibility across worker, API, storage, and UI surfaces. The selected orchestration mode for this feature is runtime (not docs-only), so the plan is explicitly aligned to production code changes plus validation tests, while still producing the required planning artifacts.

## Technical Context

**Language/Version**: Python 3.11 services + vanilla JavaScript dashboard modules (Node-executed JS tests)  
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy/Alembic, Celery queue worker patterns, existing worker redaction utilities, browser-side dashboard rendering helpers  
**Storage**: PostgreSQL `agent_jobs` (new finish outcome + JSON summary columns), filesystem artifacts under `var/artifacts/.../reports/run_summary.json`  
**Testing**: `./tools/test_unit.sh` (Python unit suite + dashboard JS tests)  
**Target Platform**: Linux/Docker Compose MoonMind API + worker services and `/tasks/*` dashboard UI  
**Project Type**: Multi-service backend + static dashboard frontend  
**Performance Goals**: Queue list responses remain compact by default (omit `finishSummary` body); detail surfaces render finish metadata without artifact download; worker finalization writes summary artifacts best effort without blocking terminal transitions  
**Constraints**: Must use documented outcome/stage vocabularies, preserve existing proposal promotion semantics, redact secret-like content before persistence/artifact output, and keep runtime-vs-docs mode behavior aligned to runtime implementation intent  
**Scale/Scope**: All terminal queue outcomes (`PUBLISHED_PR`, `PUBLISHED_BRANCH`, `NO_CHANGES`, `PUBLISH_DISABLED`, `FAILED`, `CANCELLED`) across worker, API list/detail, and dashboard list/detail/proposals deep links

## Constitution Check

- `.specify/memory/constitution.md` is currently an unfilled template, so no enforceable named principles/gates can be computed from constitution text alone.
- Project-level guardrails from repository instructions still apply and are reflected in this plan: runtime code changes and validation tests are mandatory for this feature; docs/spec-only output is explicitly out of scope.
- Runtime-vs-docs alignment gate for this feature: **PASS** when implementation includes production worker/API/UI changes and `./tools/test_unit.sh` validation evidence; **FAIL** if output is limited to docs/spec edits.

## Project Structure

### Documentation (this feature)

```text
specs/041-task-finish-summary/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── task-finish-summary.openapi.yaml
│   └── requirements-traceability.md
└── tasks.md                # Generated later by /agentkit.tasks
```

### Source Code (repository root)

```text
moonmind/
├── agents/codex_worker/worker.py
├── schemas/agent_queue_models.py
└── workflows/
    ├── agent_queue/
    │   ├── models.py
    │   ├── repositories.py
    │   └── service.py
    └── task_proposals/
        ├── repositories.py
        └── service.py

api_service/
├── api/routers/
│   ├── agent_queue.py
│   └── task_proposals.py
├── migrations/versions/202602240001_task_finish_summary.py
└── static/task_dashboard/dashboard.js

tests/
├── unit/agents/codex_worker/test_worker.py
├── unit/api/routers/test_agent_queue.py
├── unit/api/routers/test_task_proposals.py
└── task_dashboard/test_queue_layouts.js
```

**Structure Decision**: Keep finish-summary logic in existing queue/worker modules and extend the current vanilla JS dashboard surfaces. Do not introduce new frameworks or sidecar services.

## Phase 0 – Research Summary

Research outputs in `specs/041-task-finish-summary/research.md` resolve:

1. Deterministic outcome classification precedence and fallback behavior.
2. Canonical shared finish summary payload (`schemaVersion=v1`) used for DB + artifact parity.
3. Secret-redaction boundaries for finish summary/reason/error text.
4. Proposal finisher reporting and `originId` filtering contract.
5. Runtime-vs-docs orchestration mode alignment rules for this feature.

## Phase 1 – Design Outputs

- **Data Model**: `data-model.md` defines `FinishOutcome`, `FinishSummary`, `StageResult`, `PublishOutcome`, and `ProposalOutcome`, including validation/state rules.
- **API Contract**: `contracts/task-finish-summary.openapi.yaml` defines queue finish metadata and proposals filtering additions.
- **Traceability**: `contracts/requirements-traceability.md` maps each `DOC-REQ-*` item to FRs, implementation surfaces, implementation/validation owners, implementation task IDs, validation task IDs, and validation strategy.
- **Execution Guide**: `quickstart.md` captures runtime implementation and verification flow using `./tools/test_unit.sh`.

## Implementation Strategy

### 1. Worker Finalization + Outcome Classification

- Add/maintain a dedicated finalize stage in `moonmind/agents/codex_worker/worker.py` that tracks per-stage timing and always attempts finish summary construction.
- Determine outcome code/stage/reason deterministically using publish mode/results and failure/cancel context.
- Emit `reports/run_summary.json` best effort and optional compact `reports/errors.json` for failures.

### 2. Persistence + Queue Model/Service Updates

- Add `finish_outcome_code`, `finish_outcome_stage`, `finish_outcome_reason`, and `finish_summary_json` columns to `agent_jobs` via Alembic migration.
- Extend SQLAlchemy model, repository transitions, and service validation so terminal transitions can persist finish metadata for success/failure/cancel paths.
- Keep queue lifecycle semantics unchanged outside additive finish metadata.

### 3. API Schema and Endpoint Behavior

- Extend Pydantic request/response models for finish metadata on completion/failure/cancel-ack payloads.
- Ensure list endpoint includes outcome code/stage/reason while excluding large `finishSummary` payload by default.
- Ensure detail endpoint includes `finishSummary` and provide `/jobs/{id}/finish-summary` for explicit retrieval.

### 4. Dashboard Outcome and Detail UX

- Add queue list/active outcome badge rendering from `finishOutcomeCode` with stage/reason context.
- Add detail-level finish summary panel for outcome, publish summary, and proposals summary.
- Add deep link to proposals with `originSource=queue&originId=<jobId>` and corresponding filter parsing.

### 5. Proposal Linkage and Filtering

- Extend proposals API query filters to accept `originId` with `originSource` so queue detail deep links are exact-run scoped.
- Preserve existing proposal generation/submission semantics while surfacing resulting counts/errors in finish summary.

### 6. Validation and Runtime-Mode Gate

- Add/maintain worker/API/dashboard unit tests for each documented terminal outcome and finish metadata rendering/filter behavior.
- Run `./tools/test_unit.sh` as the authoritative validation command.
- Feature acceptance fails if deliverables stop at docs/spec artifacts without runtime/test coverage.

## Remediation Gates (Prompt B)

- Runtime mode must keep production runtime implementation tasks and explicit validation tasks in `tasks.md`; docs-only task sets are invalid.
- Every `DOC-REQ-*` must remain represented by at least one implementation task and one validation task, with deterministic task-ID mappings.
- `contracts/requirements-traceability.md` must keep one row per `DOC-REQ-*` and include explicit implementation and validation owners for accountability.

## Risks & Mitigations

- **Classification drift across runtimes**: keep outcome vocabulary centralized in shared contract/tests.
- **Secret leakage in summaries**: force finish reason/error text through redaction utilities before persistence and artifact writes.
- **List payload bloat**: exclude full `finishSummary` from list responses by default.
- **UI/backend contract skew**: use one shared schema and assert behavior in router + dashboard tests.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| _None_ | — | — |
