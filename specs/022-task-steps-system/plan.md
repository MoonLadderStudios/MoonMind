# Implementation Plan: Task Steps System

**Branch**: `022-task-steps-system` | **Date**: 2026-02-17 | **Spec**: `specs/022-task-steps-system/spec.md`
**Input**: Feature specification from `/specs/022-task-steps-system/spec.md`

## Summary

Implement canonical `task.steps[]` support so one queue task job executes multiple ordered runtime invocations within a single prepare/execute/publish wrapper lifecycle. Extend task contract validation and required-capability derivation, add worker step-loop execution with step-level events/artifacts and cancellation boundary checks, and add queue submit UI step authoring while preserving default publish mode `pr`.

## Technical Context

**Language/Version**: Python 3.11 and browser JavaScript (existing dashboard)  
**Primary Dependencies**: Pydantic task contract models, queue worker runtime loop, Codex/Gemini/Claude command adapters, dashboard static JS form builder  
**Storage**: Existing queue payload JSON and artifact filesystem under worker `artifacts/` paths  
**Testing**: `./tools/test_unit.sh` with focused suites for task contract, worker, and dashboard config/UI behavior  
**Target Platform**: Linux containers via docker compose (api + worker), browser dashboard at `/tasks/queue/new`  
**Project Type**: Backend + worker daemon + frontend static assets  
**Performance Goals**: Deterministic sequential execution of configured step count with no additional queue-claim overhead  
**Constraints**: Preserve wrapper stage contract; maintain cooperative cancellation semantics; reject step-level runtime/publish/git overrides; first rollout rejects `task.steps` with container execution  
**Scale/Scope**: Canonical `type=task` jobs only; no parallel steps; no runtime mutation of step list after claim

## Constitution Check

- `.specify/memory/constitution.md` remains template-only and does not define ratified MUST principles.
- Runtime-orchestration guardrail from skill applies: include production runtime code changes and validation tests.

**Gate Status**: PASS WITH NOTE (no ratified constitution constraints yet).

## Project Structure

### Documentation (this feature)

```text
specs/022-task-steps-system/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── task-steps.openapi.yaml
│   └── requirements-traceability.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/
├── workflows/agent_queue/task_contract.py
└── agents/codex_worker/worker.py

api_service/
└── static/task_dashboard/dashboard.js

tests/
├── unit/workflows/agent_queue/test_task_contract.py
└── unit/agents/codex_worker/test_worker.py
```

**Structure Decision**: Extend existing queue task contract and worker execution path in place; keep API schema and queue storage unchanged by modeling steps inside payload normalization/execution.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| _None_ | — | — |
