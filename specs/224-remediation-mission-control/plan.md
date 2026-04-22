# Implementation Plan: Remediation Mission Control Surfaces

**Branch**: `224-remediation-mission-control` | **Date**: 2026-04-22 | **Spec**: `specs/224-remediation-mission-control/spec.md`
**Input**: Single-story Jira Orchestrate request for MM-437 / STORY-007.

## Summary

Implement MM-437 by layering Mission Control UI and narrow API read/decision surfaces over the existing remediation create/link/context/evidence foundations. Existing backend work already accepts remediation create requests, persists remediation links, builds bounded context artifacts, and exposes typed evidence tools at service boundaries. This story adds operator-visible task-detail panels and create/approval flows: target pages show remediation creation and inbound links, remediation pages show target/evidence/approval state, evidence artifact refs use the existing artifact presentation path, and approval decisions stay permission-aware and audit-backed. Tests are frontend-first with focused backend route coverage where Mission Control needs a read or decision contract that does not already exist.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | missing | Task detail has generic status and action regions, but no remediation create action | Add eligible-target create action in task detail | UI unit |
| FR-002 | partial | `POST /api/executions/{workflow_id}/remediation` exists in `api_service/api/routers/executions.py`; no Mission Control prefill flow | Wire create action/form to canonical route with target context | UI unit + router unit |
| FR-003 | missing | No remediation create UI policy/evidence preview found | Add troubleshooting/admin choices and evidence preview | UI unit |
| FR-004 | partial | Service methods `list_remediations_for_target` exist; no API/UI read surface | Add/read inbound remediation link API and target panel | API unit + UI unit |
| FR-005 | partial | Service methods `list_remediation_targets` and context builder exist; no task-detail panel | Add/read outbound target API and remediation target panel | API unit + UI unit |
| FR-006 | partial | Remediation artifacts and context refs exist; task detail shows generic artifacts only | Add remediation evidence grouping and labels | UI unit |
| FR-007 | implemented_unverified | Artifact authorization/preview path exists; no remediation-specific no-raw-storage test | Verify evidence links use artifact refs only | UI unit |
| FR-008 | missing | Timeline can render approval rows; no remediation approval summary contract | Add approval-state read model and UI display | API unit + UI unit |
| FR-009 | missing | No remediation approval decision controls found | Add permission-aware approve/reject flow or read-only fallback | API unit + UI unit |
| FR-010 | missing | No remediation degraded/empty states | Add degraded states for missing links, context, evidence, live follow, approval | UI unit |
| FR-011 | implemented_unverified | MM-429 accessibility/fallback CSS exists; remediation-specific panels not covered | Add remediation panel accessibility/fallback assertions | UI unit |
| FR-012 | implemented_unverified | Existing task-detail/create/artifact tests pass in prior specs | Preserve and rerun route regression tests | UI unit |
| FR-013 | missing | This artifact set did not exist before MM-437 | Preserve traceability in specs/tasks/verification | final verify |
| DESIGN-REQ-001 | missing | Desired-state doc only for Mission Control create entrypoints | Implement create action entrypoints | UI unit |
| DESIGN-REQ-002 | missing | Desired-state doc only for remediation create choices | Implement create form/prefill choices | UI unit |
| DESIGN-REQ-003 | partial | Backend link table/service exists; no Mission Control panel | Implement inbound panel/API | API unit + UI unit |
| DESIGN-REQ-004 | partial | Backend outbound link/context exists; no Mission Control panel | Implement outbound panel/API | API unit + UI unit |
| DESIGN-REQ-005 | partial | Remediation artifacts exist as generic artifacts | Implement remediation evidence grouping | UI unit |
| DESIGN-REQ-006 | missing | No remediation approval handoff UI/API found | Implement approval display and decision path | API unit + UI unit |
| DESIGN-REQ-007 | missing | No remediation degraded UI states | Implement partial-evidence and missing-link states | UI unit |
| DESIGN-REQ-008 | implemented_unverified | Mission Control design tests exist but not for new remediation surfaces | Add accessibility/fallback coverage for remediation panels | UI unit |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; Python 3.12 for FastAPI route/read-model tests.  
**Primary Dependencies**: React, TanStack Query, existing Mission Control task-detail entrypoint, generated OpenAPI types, FastAPI, SQLAlchemy async ORM, existing Temporal execution/remediation services, pytest, Vitest and Testing Library.  
**Storage**: Existing remediation link table, artifact tables, and any existing control/audit event storage; no new persistent table planned unless approval decisions lack a current audit surface.  
**Unit Testing**: `./tools/test_unit.sh` for Python and `./tools/test_unit.sh --dashboard-only --ui-args <paths>` for frontend-focused runs.
**Integration Testing**: Rendered React entrypoint tests plus focused API/router tests; no compose-backed integration expected unless the approval decision path crosses an existing integration boundary.  
**Target Platform**: Browser Mission Control UI backed by FastAPI control-plane APIs.  
**Project Type**: Frontend runtime behavior with narrow backend API/read-model support.  
**Performance Goals**: Task detail loads remediation metadata with bounded list payloads and artifact refs only; no unbounded log or artifact body fetches during page render.  
**Constraints**: Preserve canonical remediation create contract; do not expose raw storage identifiers; preserve non-remediation task detail behavior; keep dense evidence panels matte/readable; do not change Temporal workflow payload shapes.  
**Scale/Scope**: One target execution may show a bounded list of remediation tasks; one remediation execution shows one target and grouped evidence/approval state.

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. The story surfaces existing remediation orchestration rather than changing agent cognition.
- II. One-Click Agent Deployment: PASS. No new required service dependency is planned.
- III. Avoid Vendor Lock-In: PASS. No provider-specific behavior is introduced.
- IV. Own Your Data: PASS. Remediation links, evidence, and approvals remain MoonMind-owned data/artifacts.
- V. Skills Are First-Class: PASS. No agent instruction bundle or executable skill contract change is needed.
- VI. Replaceable Scaffolding / Tests Anchor: PASS. UI/API contracts are covered by focused tests before implementation.
- VII. Runtime Configurability: PASS. Remediation authority and policies come from request/link data, not hardcoded provider behavior.
- VIII. Modular Architecture: PASS. Work stays in task-detail UI, remediation API/read-model boundaries, and tests.
- IX. Resilient by Default: PASS. Degraded states prevent missing evidence from breaking operator visibility.
- X. Continuous Improvement: PASS. Remediation evidence and approvals make follow-up outcomes reviewable.
- XI. Spec-Driven Development: PASS. This plan follows one MM-437 story with traceable requirements.
- XII. Canonical Documentation Separation: PASS. Desired-state docs remain canonical; implementation work stays under specs and source/tests.
- XIII. Pre-release Compatibility Policy: PASS. The story uses the canonical remediation contract and does not add compatibility aliases.

## Project Structure

### Documentation (this feature)

```text
specs/224-remediation-mission-control/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── remediation-mission-control.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code

```text
api_service/api/routers/executions.py
api_service/db/models.py
moonmind/workflows/temporal/service.py
frontend/src/entrypoints/task-detail.tsx
frontend/src/entrypoints/task-detail.test.tsx
frontend/src/entrypoints/task-create.tsx
frontend/src/entrypoints/task-create.test.tsx
frontend/src/generated/openapi.ts
frontend/src/styles/mission-control.css
tests/unit/api/routers/test_executions.py
tests/unit/workflows/temporal/test_temporal_service.py
```

**Structure Decision**: Implement the story in the existing task-detail and task-create Mission Control entrypoints, adding backend route/read-model support only where the frontend cannot consume existing remediation service data safely.

## Complexity Tracking

None.
