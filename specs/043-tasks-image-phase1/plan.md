# Implementation Plan: Tasks Image Attachments Phase 1 (Runtime Alignment)

**Branch**: `037-tasks-image-phase1` | **Date**: 2026-03-01 | **Spec**: `/work/agent_jobs/aadac5e6-61ee-4a2f-80d5-8b6eab40c7d9/repo/specs/037-tasks-image-phase1/spec.md`  
**Input**: Feature specification from `/work/agent_jobs/aadac5e6-61ee-4a2f-80d5-8b6eab40c7d9/repo/specs/037-tasks-image-phase1/spec.md`

## Summary

Align `037-tasks-image-phase1` to the MoonMind runtime strategy by treating this feature as **runtime mode** work, not docs-only work.  
Plan output preserves already-landed attachment APIs and focuses implementation/verification on remaining runtime surfaces: worker prepare attachment orchestration, prompt injection ordering, dashboard create/detail attachment UX, and required validation execution via `./tools/test_unit.sh`.

## Current-State Assessment

### Already Present in Repository

- Queue/service attachment ingestion and validation flow for create-with-attachments.
- User and worker attachment list/download endpoints with authorization checks.
- Reserved `inputs/` namespace protections for attachment artifacts.
- Vision settings/config surface in runtime settings.

### Runtime Alignment Focus for This Feature

- Worker prepare-stage download pipeline (`.moonmind/inputs`, manifest, context, events).
- Runtime instruction composition ordering (`INPUT ATTACHMENTS` before `WORKSPACE`).
- Dashboard runtime config and UI plumbing for create/detail attachment workflows.
- Required validation execution via `./tools/test_unit.sh` in completion criteria.

## Technical Context

**Language/Version**: Python 3.11 target (repo supports `>=3.10,<3.14`), plus browser JavaScript for dashboard UI  
**Primary Dependencies**: FastAPI, Pydantic Settings, SQLAlchemy/asyncpg, Celery/RabbitMQ, Starlette streaming responses, Tailwind build pipeline for dashboard CSS  
**Storage**: PostgreSQL for queue/job metadata; filesystem artifact storage under `var/artifacts/agent_jobs`; local worker runtime artifacts under `.moonmind/`  
**Testing**: Pytest-based suites executed via `./tools/test_unit.sh` (mandatory wrapper)  
**Target Platform**: Linux containers via Docker Compose (`api`, `celery-worker`, `rabbitmq`, optional `orchestrator`)  
**Project Type**: Monorepo backend + static dashboard frontend served by API service  
**Performance Goals**: Preserve existing queue execution behavior while enforcing deterministic attachment persistence-before-claim and prepare-stage artifact generation  
**Constraints**: Enforce attachment type/count/size limits; preserve reserved `inputs/` namespace protections; no compatibility transforms affecting runtime model/effort/publish semantics; runtime mode requires production code changes (docs-only is non-compliant)  
**Scale/Scope**: Phase 1 image attachments only; max attachment count/bytes controlled by `AGENT_JOB_ATTACHMENT_*`; captions explicitly deferred/fail-fast

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Research Gate

- **I. One-Click Deployment with Smart Defaults**: PASS. Feature uses existing compose services and config defaults.
- **II. Powerful Runtime Configurability**: PASS. Attachment and vision behavior stays env-configured (`AGENT_JOB_ATTACHMENT_*`, `MOONMIND_VISION_*`) with deterministic precedence.
- **III. Modular and Extensible Architecture**: PASS. Changes remain in existing module boundaries (queue service/router, worker, dashboard view model/UI).
- **IV. Avoid Exclusive Proprietary Vendor Lock-In**: PASS. Vision path remains provider-configurable and produces portable text/json artifacts.
- **V. Self-Healing by Default**: PASS. Prepare-stage workflow emits explicit events and keeps deterministic failure behavior on validation/download errors.
- **VI. Facilitate Continuous Improvement**: PASS. Artifacts/events/logs remain inspectable for operator debugging and follow-up improvements.
- **VII. Spec-Driven Development Is the Source of Truth**: PASS. `spec.md`, `plan.md`, `tasks.md` and traceability mapping are maintained together.
- **VIII. Skills Are First-Class and Easy to Add**: PASS. Execution remains skill-driven with explicit artifacts and validation requirements.

### Post-Design Re-Check

- PASS (all principles). No new constitution violations introduced by Phase 0/1 artifacts.  
- Runtime/docs mode alignment is explicit: feature intent and tasks require runtime code + tests; docs-only completion is rejected.

## Phase 0 Research Output

`research.md` resolves technical questions and records decisions for:
- attachment storage model under reserved `inputs/` namespace,
- dedicated user/worker attachment APIs,
- prepare-stage download + manifest/context generation timing,
- vision context generation strategy,
- prompt block ordering (`INPUT ATTACHMENTS` before `WORKSPACE`).

Reference: `/work/agent_jobs/aadac5e6-61ee-4a2f-80d5-8b6eab40c7d9/repo/specs/037-tasks-image-phase1/research.md`

## Phase 1 Design Output

- Data model: `/work/agent_jobs/aadac5e6-61ee-4a2f-80d5-8b6eab40c7d9/repo/specs/037-tasks-image-phase1/data-model.md`
- API contracts: `/work/agent_jobs/aadac5e6-61ee-4a2f-80d5-8b6eab40c7d9/repo/specs/037-tasks-image-phase1/contracts/attachments.openapi.yaml`
- Requirements traceability: `/work/agent_jobs/aadac5e6-61ee-4a2f-80d5-8b6eab40c7d9/repo/specs/037-tasks-image-phase1/contracts/requirements-traceability.md`
- Validation quickstart: `/work/agent_jobs/aadac5e6-61ee-4a2f-80d5-8b6eab40c7d9/repo/specs/037-tasks-image-phase1/quickstart.md`

All `DOC-REQ-*` entries in `spec.md` are mapped and include planned validation.

## Implementation Surface

- `api_service/api/routers/agent_queue.py`
- `moonmind/workflows/agent_queue/task_contract.py`
- `moonmind/workflows/agent_queue/service.py`
- `moonmind/workflows/agent_queue/storage.py`
- `moonmind/agents/codex_worker/worker.py`
- `api_service/api/routers/task_dashboard_view_model.py`
- `api_service/static/task_dashboard/dashboard.js`
- `moonmind/vision/service.py`
- `tests/unit/api/routers/test_agent_queue.py`
- `tests/unit/api/routers/test_agent_queue_artifacts.py`
- `tests/unit/workflows/agent_queue/test_artifact_storage.py`
- `tests/unit/agents/codex_worker/test_worker.py`
- `tests/unit/api/routers/test_task_dashboard_view_model.py`

## Verification Gate

- Mandatory validation command for this feature: `./tools/test_unit.sh`.
- Runtime-mode completion is blocked until unit coverage for API, worker prepare, prompt ordering, and dashboard attachment behavior is passing via the wrapper script.

## Project Structure

### Documentation (this feature)

```text
specs/037-tasks-image-phase1/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── attachments.openapi.yaml
│   └── requirements-traceability.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/
├── api/routers/
│   ├── agent_queue.py
│   └── task_dashboard_view_model.py
└── static/task_dashboard/
    └── dashboard.js

moonmind/
├── agents/codex_worker/
│   └── worker.py
├── vision/
└── workflows/agent_queue/
    └── service.py

tests/
└── unit/
    ├── agents/codex_worker/test_worker.py
    ├── api/routers/test_agent_queue.py
    └── api/routers/test_task_dashboard_view_model.py
```

**Structure Decision**: Keep the existing MoonMind monorepo layout and implement attachment behavior in existing API/worker/dashboard modules instead of introducing new top-level projects.

## Prompt B Remediation Application (Step 12/16)

### Completed CRITICAL/HIGH remediations

- Added explicit Prompt B runtime scope controls in `tasks.md` so production runtime implementation tasks and validation tasks are auditable before implementation starts.
- Expanded `contracts/requirements-traceability.md` to include deterministic implementation-task and validation-task mappings for every `DOC-REQ-001` through `DOC-REQ-011`.
- Aligned implementation surfaces in this plan with the API, workflow service/storage, worker, vision, dashboard, and test modules referenced by the task plan.

### Completed MEDIUM/LOW remediations

- Synchronized runtime-mode wording and coverage guard language across `spec.md`, `plan.md`, and `tasks.md` to reduce ambiguity during later task regeneration.

### Residual risks

- Runtime delivery remains a broad cross-module change set (API, service, worker, dashboard) and can still surface hidden integration dependencies during implementation.
- Validation command coverage is defined, but evidence remains pending until `T028` executes and records final results in `quickstart.md`.

## Complexity Tracking

No constitution violations requiring mitigation were identified.
