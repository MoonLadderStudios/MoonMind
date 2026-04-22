# Implementation Plan: Remediation Create Links

**Branch**: `220-remediation-create-links` | **Date**: 2026-04-21 | **Spec**: `specs/220-remediation-create-links/spec.md`
**Input**: Single-story feature specification from `/specs/220-remediation-create-links/spec.md`

## Summary

Implement MM-431 by adding the create-time persistence slice for Task Remediation. The existing `MoonMind.Run` create path already validates and persists dependency edges; this story adds a separate remediation relationship that accepts `task.remediation`, validates the target execution, pins the current target run ID, writes a durable link table, and exposes service-level inbound/outbound lookup methods. Tests focus on the service boundary and task-shaped API normalization so behavior is proven before production changes.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | Direct `initialParameters` passes arbitrary `task` data; task-shaped router does not preserve remediation explicitly | Preserve `task.remediation` in task-shaped normalization and validate in service | unit |
| FR-002 | missing | No remediation target link exists | Resolve target source record and persist current target run ID | unit |
| FR-003 | missing | No remediation link table or model exists | Add DB model and migration | unit |
| FR-004 | missing | No remediation-specific validation exists | Add validation for missing/malformed/mismatched targets | unit |
| FR-005 | missing | No link persistence exists | Write link in same create transaction as canonical record | unit |
| FR-006 | missing | No remediation lookup methods exist | Add inbound and outbound service lookup methods | unit |
| FR-007 | implemented_unverified | Dependency logic is separate but no remediation regression coverage exists | Add test proving remediation does not write dependency edges | unit |
| DESIGN-REQ-001 | missing | `docs/Tasks/TaskRemediation.md` desired state only | Implement required target validation | unit |
| DESIGN-REQ-002 | missing | `docs/Tasks/TaskRemediation.md` desired state only | Persist pinned target run ID | unit |
| DESIGN-REQ-003 | missing | `docs/Tasks/TaskRemediation.md` desired state only | Persist relationship and lookup directions | unit |
| DESIGN-REQ-004 | implemented_verified | Out-of-scope in `spec.md` | No implementation | final verify |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: FastAPI, SQLAlchemy async ORM, Pydantic v2, Temporal Python SDK  
**Storage**: Existing SQLAlchemy/Alembic database; new remediation link table only  
**Unit Testing**: pytest via `./tools/test_unit.sh`  
**Integration Testing**: Existing hermetic integration runner; this slice is covered at service/router unit boundaries  
**Target Platform**: Linux server / Docker Compose deployment  
**Project Type**: FastAPI control plane plus Temporal workflow service  
**Performance Goals**: Create-time validation and link persistence are bounded to one target execution lookup and one insert  
**Constraints**: Do not embed evidence payloads in workflow history; do not change dependency semantics; preserve pre-release compatibility policy by using one canonical contract  
**Scale/Scope**: One remediation link per remediation execution in this slice

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. This adds orchestration metadata without changing agent behavior.
- II. One-Click Agent Deployment: PASS. Uses existing DB/migration patterns.
- III. Avoid Vendor Lock-In: PASS. No vendor-specific behavior.
- IV. Own Your Data: PASS. Link data is persisted locally.
- V. Skills Are First-Class and Easy to Add: PASS. No skill contract changes.
- VI. Replaceable Scaffolding: PASS. Thin service contract with tests.
- VII. Runtime Configurability: PASS. No hardcoded provider or environment behavior.
- VIII. Modular Architecture: PASS. Changes stay in execution persistence boundaries.
- IX. Resilient by Default: PASS. Create transaction prevents orphan links.
- X. Continuous Improvement: PASS. Enables durable follow-up remediation tasks.
- XI. Spec-Driven Development: PASS. This spec/plan/tasks set defines the story.
- XII. Canonical Docs vs Tmp: PASS. Desired-state docs remain unchanged; implementation artifacts live under `specs/`.
- XIII. Pre-Release Compatibility: PASS. Adds a canonical slice without aliases or compatibility transforms.

## Project Structure

### Documentation (this feature)

```text
specs/220-remediation-create-links/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── remediation-create-links.md
└── tasks.md
```

### Source Code

```text
api_service/
├── api/routers/executions.py
├── db/models.py
└── migrations/versions/

moonmind/
└── workflows/temporal/service.py

tests/
└── unit/
    ├── api/routers/test_executions.py
    └── workflows/temporal/test_temporal_service.py
```

## Complexity Tracking

None.
