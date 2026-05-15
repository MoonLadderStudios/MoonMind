# Implementation Plan: Preserve Slash Command Fidelity Across Edit, Rerun, Details, and Audit

**Branch**: `run-jira-orchestrate-for-mm-687-preserve-f39343e0` | **Date**: 2026-05-15 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `/work/agent_jobs/mm:e1afde4a-fc92-48d9-811d-6ca6df9c1b32/repo/specs/357-preserve-slash-command-fidelity/spec.md`

## Summary

MM-687 requires MoonMind to preserve and present historical slash-command meaning across edit mode, exact rerun, edit-for-rerun, task details, and audit surfaces. Repo analysis found backend snapshot generation and frontend draft reconstruction already carry `runtimeCommand` metadata in several paths, but task details do not expose command interpretation, audit events for command detection/render/pass-through are not yet surfaced as first-class operator evidence, and tests do not cover the full historical-fidelity story. The plan is to extend existing task input snapshot, Create page edit/rerun, task detail, and observability surfaces with verification-first tests, adding implementation where current behavior is partial or missing.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `moonmind/workflows/tasks/task_contract.py` builds `runtimeCommand` metadata in authoritative snapshots; schema tests cover runtime command fields. | Verify durable snapshot carries all required fields and add implementation if any fields are dropped in execution artifacts. | unit + integration |
| FR-002 | partial | Snapshot builder stores `instructions` separately from `runtimeCommand`; frontend draft reconstruction preserves both when present. | Extend surface-level validation so authored text and interpretation remain distinct through edit, rerun, details, and audit. | unit + integration |
| FR-003 | implemented_unverified | `frontend/src/lib/temporalTaskEditing.ts` restores `taskInstructions`; existing tests cover rerun/edit draft reconstruction. | Add slash-command-specific edit-mode verification; implementation contingency if edit mode uses current preview over snapshot text. | unit |
| FR-004 | partial | `frontend/src/lib/temporalTaskEditing.ts` restores task and step `runtimeCommand`; tests cover basic draft preservation. | Add absent-metadata historical case and ensure re-detection remains preview-only without mutating raw instructions. | unit |
| FR-005 | partial | `moonmind/workflows/temporal/service.py` has exact rerun recovery and frontend exact rerun payload tests exist. | Verify exact rerun preserves runtime command metadata and catalog versions from source snapshot/parameters. | unit + integration |
| FR-006 | missing | No current evidence of changed capability or hint-version warnings tied to preserved historical metadata. | Add visible warning/model state for version drift while keeping source metadata immutable. | unit + integration |
| FR-007 | partial | Edit-for-rerun mode and recovery provenance exist in frontend and Temporal service tests. | Add runtime-command immutability checks for source run when edit-for-rerun recomputes warnings. | unit + integration |
| FR-008 | partial | Task detail surfaces existing task information, but no `runtimeCommand` rendering evidence was found. | Add original authored instructions display for slash-command task details where missing. | unit |
| FR-009 | missing | Search found no task-detail `runtimeCommand` rendering. | Add task detail command interpretation display: command, runtime, render mode, and status. | unit |
| FR-010 | partial | Runtime render metadata is stored under MoonMind execution metadata; observability surfaces exist, but named command audit events are not present. | Emit/surface `runtime_command.detected`, `runtime_command.rendered`, and `runtime_command.passthrough` audit events without secrets. | unit + integration |
| FR-011 | partial | Runtime rendering failure tests use redacted diagnostics; command text is treated as untrusted in runtime rendering. | Extend audit/detail sanitization tests for command names, args, instruction bodies, and metadata. | unit + integration |
| FR-012 | missing | Existing tests cover isolated normalization/rendering, not the full edit/rerun/detail/audit historical-fidelity story. | Add focused unit tests and a hermetic integration path covering all MM-687 surfaces. | unit + integration |
| FR-013 | implemented_unverified | `spec.md` preserves MM-687 and the original Jira preset brief. | Preserve traceability through plan, research, contracts, quickstart, tasks, implementation notes, verification, commit, and PR metadata. | final verify |
| SCN-001 | implemented_unverified | Draft reconstruction can carry instructions and `runtimeCommand`; slash-specific edit scenario not proven. | Add edit-mode scenario test before implementation changes. | unit |
| SCN-002 | missing | No absent-metadata historical slash edit test found. | Add historical no-metadata edit test and preview-only behavior. | unit |
| SCN-003 | partial | Exact rerun payload tests exist but do not assert runtime command versions. | Add exact rerun metadata preservation test. | unit + integration |
| SCN-004 | partial | Edit-for-rerun tests exist for provenance, not runtime command warning immutability. | Add edit-for-rerun drift warning/source immutability test. | unit + integration |
| SCN-005 | missing | No task detail command interpretation surface found. | Add task detail display and tests. | unit |
| SCN-006 | partial | Runtime render metadata exists; named audit events are missing. | Add audit event production/surfacing and sanitization tests. | unit + integration |
| SC-001 | implemented_unverified | Existing draft preservation evidence is not slash-specific enough. | Verify with slash-command edit-mode tests. | unit |
| SC-002 | missing | No no-metadata historical edit test found. | Add no-metadata snapshot test. | unit |
| SC-003 | partial | Rerun mechanics exist; runtime command catalog version preservation unproven. | Add exact rerun version preservation test. | unit + integration |
| SC-004 | partial | Edit-for-rerun mechanics exist; source-run metadata immutability with warnings unproven. | Add drift-warning test. | unit + integration |
| SC-005 | missing | No task-detail runtime command display evidence found. | Add task detail display tests. | unit |
| SC-006 | partial | Sanitized render failure diagnostics exist; audit event contract incomplete. | Add command audit event and redaction coverage. | unit + integration |
| SC-007 | implemented_unverified | MM-687 preserved in `spec.md` and this plan. | Preserve through downstream artifacts and final verification. | final verify |
| DESIGN-REQ-002 | partial | Snapshot metadata includes recognition mode and catalog versions. | Verify storage and surfacing across historical workflows. | unit + integration |
| DESIGN-REQ-003 | partial | Edit draft reconstruction preserves metadata when present. | Add edit-mode preview-only absent metadata behavior. | unit |
| DESIGN-REQ-014 | partial | Rerun flow exists; catalog version preservation unproven. | Add exact rerun runtime command version test. | unit + integration |
| DESIGN-REQ-015 | missing | Task detail command interpretation and named audit events are missing. | Add details and audit surfaces. | unit + integration |
| DESIGN-REQ-018 | partial | Renderer treats command text as untrusted and redacts failure diagnostics. | Extend secret-safe audit/detail handling and validation coverage. | unit + integration |

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React for Mission Control UI  
**Primary Dependencies**: FastAPI, SQLAlchemy async ORM, Pydantic v2, Temporal Python SDK, React, TanStack Query, existing Temporal artifact and task editing helpers  
**Storage**: Existing Temporal execution records, artifact-backed task input snapshots, Temporal metadata/search/memo fields, existing observability/control-event artifact surfaces; no new persistent tables planned  
**Unit Testing**: `./tools/test_unit.sh` for final unit verification; focused Python pytest and Vitest during iteration  
**Integration Testing**: `./tools/test_integration.sh` for hermetic integration_ci coverage; focused integration tests under `tests/integration` for Temporal/artifact/API boundaries  
**Target Platform**: MoonMind local/operator deployment with Mission Control web UI and Temporal-backed workflow execution  
**Project Type**: Web application plus orchestration service and managed-runtime workflow system  
**Performance Goals**: Historical task detail, edit, and rerun views remain normal interactive UI operations; added metadata should be compact and bounded  
**Constraints**: Preserve secret hygiene; do not mutate historical source-run evidence; keep workflow/activity payload changes compatible for in-flight runs or explicitly version/cut over; do not introduce internal compatibility aliases  
**Scale/Scope**: One runtime story covering slash-command metadata fidelity across edit, rerun, task details, and audit surfaces for task-level and step-level instructions

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Gate Result | Notes |
| --- | --- | --- |
| I. Orchestrate, Don't Recreate | PASS | Preserves runtime command metadata around existing runtime adapters without rebuilding agent behavior. |
| II. One-Click Agent Deployment | PASS | Uses existing local-first stack and test runners; no new required external service. |
| III. Avoid Vendor Lock-In | PASS | Treats command interpretation as runtime-neutral metadata and avoids provider-specific UI hardcoding. |
| IV. Own Your Data | PASS | Uses operator-owned task snapshots, artifacts, and observability surfaces. |
| V. Skills Are First-Class and Easy to Add | PASS | Does not alter active skill runtime behavior; preserves MoonSpec traceability for skill-driven workflow. |
| VI. Replaceable AI Scaffolding | PASS | Adds explicit contracts and tests around volatile command interpretation surfaces. |
| VII. Powerful Runtime Configurability | PASS | Preserves runtime capability and hint catalog versions as observed metadata rather than hardcoding behavior into historical views. |
| VIII. Modular and Extensible Architecture | PASS | Planned work stays inside task snapshot, task editing, task detail, and observability boundaries. |
| IX. Resilient by Default | PASS | Historical evidence remains immutable and rerun behavior is explicit; workflow boundary tests are planned. |
| X. Facilitate Continuous Improvement | PASS | Audit/detail surfaces make operator diagnosis easier without raw workflow history parsing. |
| XI. Spec-Driven Development | PASS | `spec.md`, `plan.md`, and design artifacts preserve MM-687 traceability before tasks/implementation. |
| XII. Desired-State Documentation | PASS | Migration and implementation details remain in feature artifacts, not canonical docs. |
| XIII. Delete, Don't Deprecate | PASS | No compatibility aliases or wrappers are planned; superseded internal behavior should be replaced cleanly if found. |
| Security / Secret Hygiene | PASS | Audit/detail outputs must exclude secrets and treat authored command text as untrusted. |

Post-design re-check: PASS. Phase 1 artifacts keep the same boundaries, avoid new storage, and preserve security and traceability constraints.

## Project Structure

### Documentation (this feature)

```text
specs/357-preserve-slash-command-fidelity/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── slash-command-fidelity.md
└── checklists/
    └── requirements.md
```

### Source Code (repository root)

```text
moonmind/
├── schemas/
│   └── agent_runtime_models.py
└── workflows/
    ├── tasks/
    │   └── task_contract.py
    └── temporal/
        ├── service.py
        ├── runtime/
        │   └── launcher.py
        └── workflows/
            └── run.py

api_service/
└── api/
    └── routers/
        ├── executions.py
        ├── task_dashboard_view_model.py
        └── task_runs.py

frontend/src/
├── entrypoints/
│   ├── task-create.tsx
│   ├── task-create.test.tsx
│   ├── task-detail.tsx
│   └── task-detail.test.tsx
└── lib/
    ├── temporalTaskEditing.ts
    └── temporalTaskEditing.test.ts

tests/
├── unit/
│   ├── schemas/
│   └── workflows/
└── integration/
    ├── api/
    └── workflows/temporal/
```

**Structure Decision**: Use existing task contract, Temporal execution, Mission Control Create/Edit/Rerun, Task Detail, and observability boundaries. No new top-level package, service, or persistent table is planned.

## Complexity Tracking

No constitution violations requiring complexity exceptions.
