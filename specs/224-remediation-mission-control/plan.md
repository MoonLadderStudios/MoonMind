# Implementation Plan: Remediation Mission Control Surfaces

**Branch**: `224-remediation-mission-control` | **Date**: 2026-04-22 | **Spec**: `specs/224-remediation-mission-control/spec.md`
**Input**: Single-story Jira Orchestrate request for MM-457 from `docs/tmp/jira-orchestration-inputs/MM-457-moonspec-orchestration-input.md`.

## Summary

Implement MM-457 by layering Mission Control UI and narrow API read/decision surfaces over the existing remediation create/link/context/evidence foundations. Existing backend work already accepts remediation create requests, persists remediation links, builds bounded context artifacts, and exposes typed evidence tools at service boundaries. Current implementation adds operator-visible task-detail panels and create/approval flows: target pages show remediation creation and inbound links, remediation pages show target/evidence/approval state, evidence artifact refs use the existing artifact presentation path, and approval decisions stay permission-aware and audit-backed. Verification is frontend-first with focused backend route/service coverage where Mission Control needs a read or decision contract.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `frontend/src/entrypoints/task-detail.tsx` adds eligible-target Create remediation task action; `frontend/src/entrypoints/task-detail.test.tsx` covers eligible submission | No new implementation; add ineligible-state coverage if expanding regression scope | UI unit |
| FR-002 | implemented_verified | `frontend/src/entrypoints/task-detail.tsx` posts target context to `POST /api/executions/{workflow_id}/remediation`; router regression remains in `tests/unit/api/routers/test_executions.py` | No new implementation | UI unit + router unit |
| FR-003 | implemented_verified | Task detail shows remediation mode, authority, action policy, pinned run, and evidence preview controls; UI tests cover chosen values in the submitted payload | No new implementation | UI unit |
| FR-004 | implemented_verified | `api_service/api/routers/executions.py` exposes inbound remediation link read route; `frontend/src/entrypoints/task-detail.tsx` renders Remediation Tasks; API/UI tests cover the panel | No new implementation | API unit + UI unit |
| FR-005 | implemented_verified | `api_service/api/routers/executions.py` exposes outbound remediation target read route; `frontend/src/entrypoints/task-detail.tsx` renders Remediation Target; API/UI tests cover the panel | No new implementation | API unit + UI unit |
| FR-006 | implemented_verified | `frontend/src/entrypoints/task-detail.tsx` groups remediation-prefixed artifacts; UI tests cover remediation evidence links | No new implementation | UI unit |
| FR-007 | implemented_verified | Evidence links route through existing artifact download/preview paths and tests cover rendered remediation artifact links without raw storage paths | No new implementation | UI unit |
| FR-008 | implemented_verified | `RemediationApprovalStateModel` and task-detail approval state rendering are present; API/UI tests cover approval state and decision submission | No new implementation | API unit + UI unit |
| FR-009 | implemented_verified | Approve/reject submission route and enabled UI path exist; read-only unauthorized approval state is rendered and covered by UI tests | No new implementation | API unit + UI unit |
| FR-010 | implemented_verified | Empty relationship/evidence states, missing evidence bundle, unavailable live-follow fallback, and approval read-only metadata are rendered and covered by UI tests | No new implementation | UI unit |
| FR-011 | implemented_verified | `.td-remediation-region`, `.td-remediation-list`, and remediation create/approval styles include focus, containment, and mobile safeguards covered by CSS assertions | No new implementation | UI unit |
| FR-012 | implemented_verified | Non-remediation task detail/create tests pass in focused and full unit runs | No new implementation | UI unit |
| FR-013 | implemented_verified | `spec.md` now preserves the original MM-457 preset brief and traceability checks find MM-457 plus source coverage IDs | No new implementation | final verify |
| DESIGN-REQ-001 | implemented_verified | Task detail create entrypoint exists and is tested for eligible remediation targets | No new implementation | UI unit |
| DESIGN-REQ-002 | implemented_verified | Task detail exposes remediation mode, authority, action policy, pinned run, and evidence preview controls before submission | No new implementation | UI unit |
| DESIGN-REQ-003 | implemented_verified | Inbound API and target Remediation Tasks panel exist with compact status/action/lock fields | No new implementation | API unit + UI unit |
| DESIGN-REQ-004 | implemented_verified | Outbound API and Remediation Target panel exist with target/evidence/approval fields | No new implementation | API unit + UI unit |
| DESIGN-REQ-005 | implemented_verified | Remediation evidence grouping and artifact links are implemented in task detail and covered by UI tests | No new implementation | UI unit |
| DESIGN-REQ-006 | implemented_verified | Approval display, decision route, enabled controls, and unauthorized/read-only approval behavior are implemented and covered | No new implementation | API unit + UI unit |
| DESIGN-REQ-007 | implemented_verified | Missing relationship/evidence states, missing evidence bundle, live-follow fallback, and approval degraded states are implemented and covered | No new implementation | UI unit |
| DESIGN-REQ-008 | implemented_verified | Remediation CSS focus, containment, and mobile safeguards are implemented and covered by CSS assertions | No new implementation | UI unit |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; Python 3.12 for FastAPI route/read-model tests.  
**Primary Dependencies**: React, TanStack Query, existing Mission Control task-detail entrypoint, generated OpenAPI types, FastAPI, SQLAlchemy async ORM, existing Temporal execution/remediation services, pytest, Vitest and Testing Library.  
**Storage**: Existing remediation link table, artifact tables, and any existing control/audit event storage; no new persistent table planned unless approval decisions lack a current audit surface.  
**Unit Testing**: `./tools/test_unit.sh` for Python service/router tests and `./tools/test_unit.sh --dashboard-only --ui-args <paths>` for frontend-focused Vitest runs.
**Integration Testing**: React entrypoint rendering tests exercise task-detail integration behavior across query data, user actions, and rendered panels; focused FastAPI router tests exercise route-to-service contracts. No compose-backed `integration_ci` suite is required for this bounded UI/API story unless later work moves remediation approval decisions across a workflow or external service boundary.
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
- XI. Spec-Driven Development: PASS. This plan follows one MM-457 story with traceable requirements.
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
