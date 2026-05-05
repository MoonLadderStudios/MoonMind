# Implementation Plan: Task-only Visibility and Diagnostics Boundary

**Branch**: `run-jira-orchestrate-for-mm-586-task-onl-9e439b1f` | **Date**: 2026-05-05 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story runtime feature specification from `specs/299-task-only-visibility-diagnostics/spec.md`

## Summary

Implement the MM-586 runtime story by removing ordinary workflow-kind browsing from the Tasks List UI, normalizing legacy workflow-scope URLs to task-run visibility, showing a recoverable notice when unsupported workflow-scope state is ignored, and hardening the source-temporal execution list boundary so broad `scope`, `workflowType`, or `entry` parameters cannot widen ordinary task-list results. Current repo inspection found the table already excludes `Kind`, `Workflow Type`, and `Entry`, but the UI still exposes Scope/Workflow Type/Entry controls and the source-temporal API still honors `scope=all`; this run requires tests and implementation.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `frontend/src/entrypoints/tasks-list.tsx` defaults to `scope=tasks` but can widen via controls/URL | force task-run request semantics | UI + backend unit |
| FR-002 | missing | UI renders `Scope`, `Workflow Type`, and `Entry` controls | remove ordinary workflow-kind controls | UI test |
| FR-003 | partial | old URL params initialize broad list state | normalize unsafe params and show notice | UI test |
| FR-004 | implemented_verified | table columns omit `Kind`, `Workflow Type`, and `Entry`; existing started-column regression nearby | preserve behavior, add focused assertion | UI test |
| FR-005 | implemented_unverified | Status and Repository controls exist but are coupled to broad controls | verify preserved task filters after broad-control removal | UI test |
| FR-006 | partial | `_normalize_temporal_list_scope()` supports all/user/system and source-temporal route appends broad filters | coerce ordinary source-temporal list to task scope and ignore widening filters | backend unit |
| FR-007 | partial | non-admin owner scoping exists; broad workflow scope can still list all user-owned workflow types | prevent normal query params from widening beyond task runs | backend unit |
| FR-008 | missing | no recoverable notice for ignored workflow-scope URL state | add notice when legacy workflow-scope params are ignored | UI test |
| FR-009 | partial | URL sync can emit `scope`, `workflowType`, and `entry` | remove ignored workflow-scope params from emitted URL | UI test |
| FR-010 | implemented_verified | React text interpolation renders labels/values as text | preserve behavior | final verify |
| FR-011 | implemented_unverified | this feature's artifacts preserve MM-586 | carry traceability through plan/tasks/verification | final verify |
| SC-001 | missing | no test covers broad URL normalization | add UI request assertions | UI test |
| SC-002 | missing | existing tests expect broad controls | replace with absence/preserved filter tests | UI test |
| SC-003 | missing | no test covers recoverable notice or URL rewrite | add UI test | UI test |
| SC-004 | implemented_unverified | columns currently exclude forbidden headers | add focused assertion tied to MM-586 | UI test |
| SC-005 | missing | existing backend test expects `scope=all` raw query | update/add backend tests for fail-safe task query | backend unit |
| SC-006 | implemented_unverified | artifacts in progress | final verification | final verify |
| DESIGN-REQ-005 | partial | normal page can still browse workflow kinds | remove broad controls and harden request boundary | UI + backend unit |
| DESIGN-REQ-008 | implemented_unverified | table columns are task-oriented | add focused assertion | UI test |
| DESIGN-REQ-009 | partial | system/manifest URL params can affect requests | normalize URL/API parameters safely | UI + backend unit |
| DESIGN-REQ-017 | partial | old `state`/`repo` work, broad params unsafe | preserve task filters and ignore broad params with notice | UI test |
| DESIGN-REQ-025 | partial | owner scoping exists, but filter params can widen workflow kinds | enforce task boundary and preserve text rendering | backend + UI test |

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React for Mission Control UI  
**Primary Dependencies**: FastAPI, Pydantic v2 response models, Temporal Python SDK client query surface, React, TanStack Query, Vitest, Testing Library, pytest  
**Storage**: No new persistent storage; existing execution list API and browser URL state only  
**Unit Testing**: `pytest tests/unit/api/test_executions_temporal.py -q` and final `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`  
**Integration Testing**: `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx`; final wrapper via `./tools/test_unit.sh --ui-args frontend/src/entrypoints/tasks-list.test.tsx`  
**Target Platform**: MoonMind API service and Mission Control Tasks List frontend  
**Project Type**: FastAPI backend with React/Vite frontend entrypoints  
**Performance Goals**: No additional list fetches beyond the existing execution-list request; URL normalization must happen during initial render without extra network round trips  
**Constraints**: Runtime implementation workflow; browser uses MoonMind APIs only; no raw credentials; diagnostics route creation is out of scope; unsupported broad workflow scope fails safe to task-run visibility  
**Scale/Scope**: One Tasks List visibility-boundary story for ordinary task runs and legacy URL compatibility

## Constitution Check

- I Orchestrate, Don't Recreate: PASS. Work stays inside existing dashboard and execution-list adapter boundaries.
- II One-Click Agent Deployment: PASS. No new services, secrets, or deployment prerequisites.
- III Avoid Vendor Lock-In: PASS. Browser access remains routed through MoonMind APIs and Temporal remains behind existing service/router boundaries.
- IV Own Your Data: PASS. Execution state remains MoonMind-owned control-plane data.
- V Skills Are First-Class: PASS. No skill runtime mutation.
- VI Replaceable Scaffolding: PASS. Behavior is anchored by focused tests.
- VII Runtime Configurability: PASS. Existing server dashboard configuration remains unchanged.
- VIII Modular Architecture: PASS. Changes are scoped to the execution router and Tasks List entrypoint/tests.
- IX Resilient by Default: PASS. Old URLs fail safe without exposing unauthorized workflow rows.
- X Continuous Improvement: PASS. Verification artifacts will record the outcome.
- XI Spec-Driven Development: PASS. This plan follows the single-story spec.
- XII Canonical Documentation Separation: PASS. Implementation tracking remains under `specs/299-task-only-visibility-diagnostics/`.
- XIII Pre-release Compatibility Policy: PASS. Superseded internal workflow-kind browsing behavior is removed rather than wrapped with compatibility aliases.

## Project Structure

### Documentation (this feature)

```text
specs/299-task-only-visibility-diagnostics/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── tasks-list-visibility-boundary.md
├── checklists/
│   └── requirements.md
├── moonspec_align_report.md
├── tasks.md
└── verification.md
```

### Source Code (repository root)

```text
api_service/api/routers/executions.py
tests/unit/api/test_executions_temporal.py
frontend/src/entrypoints/tasks-list.tsx
frontend/src/entrypoints/tasks-list.test.tsx
```

**Structure Decision**: Keep task-only UI behavior in the existing Tasks List entrypoint and enforce source-temporal list query safety in the existing executions router. No new route or persistent model is required.

## Complexity Tracking

No constitution violations.
