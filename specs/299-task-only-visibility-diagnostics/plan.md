# Implementation Plan: Task-only Visibility and Diagnostics Boundary

**Branch**: `run-jira-orchestrate-for-mm-586-task-onl-9e439b1f` | **Date**: 2026-05-05 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story runtime feature specification from `specs/299-task-only-visibility-diagnostics/spec.md`

## Summary

Implement the MM-586 runtime story by keeping ordinary workflow-kind browsing out of the Tasks List UI, normalizing legacy workflow-scope URLs to task-run visibility, showing a recoverable notice when unsupported workflow-scope state is ignored, and hardening the source-temporal execution list boundary so broad `scope`, `workflowType`, or `entry` parameters cannot widen ordinary task-list results. Current repo inspection confirms the implementation and tests are now present: the table excludes `Kind`, `Workflow Type`, and `Entry`, the UI no longer exposes Scope/Workflow Type/Entry controls, legacy broad URL state is normalized, and the source-temporal API fails safe to task-run query semantics.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `frontend/src/entrypoints/tasks-list.tsx` always sends `scope=tasks`; `frontend/src/entrypoints/tasks-list.test.tsx` asserts default and legacy request URLs | no new implementation | UI + backend unit |
| FR-002 | implemented_verified | `frontend/src/entrypoints/tasks-list.tsx` renders only Status and Repository filters; UI tests assert `Scope`, `Workflow Type`, and `Entry` are absent | no new implementation | UI test |
| FR-003 | implemented_verified | `hasUnsupportedWorkflowScopeState()` and URL sync normalize broad legacy params; UI test covers broad URL state | no new implementation | UI test |
| FR-004 | implemented_verified | `TABLE_COLUMNS` omits `Kind`, `Workflow Type`, and `Entry`; UI tests assert forbidden headers are absent | no new implementation | UI test |
| FR-005 | implemented_verified | Status and Repository controls remain in `tasks-list.tsx`; UI tests cover status and repo request behavior | no new implementation | UI test |
| FR-006 | implemented_verified | `_normalize_temporal_list_scope()` returns task scope for recognized broad scopes; backend tests cover `scope=all` and system workflow params | no new implementation | backend unit |
| FR-007 | implemented_verified | source-temporal query construction ignores broad workflow/entry params while preserving ordinary owner scoping; backend tests assert task-run query | no new implementation | backend unit |
| FR-008 | implemented_verified | `ignoredWorkflowScopeState` renders recoverable notice; UI legacy URL test asserts notice | no new implementation | UI test |
| FR-009 | implemented_verified | URL sync omits `scope`, `workflowType`, and `entry`; UI legacy URL test asserts normalized URL state | no new implementation | UI test |
| FR-010 | implemented_verified | React text interpolation renders labels/values as text | preserve behavior | final verify |
| FR-011 | implemented_verified | `spec.md`, `plan.md`, `tasks.md`, and `verification.md` preserve MM-586 | no new implementation | final verify |
| SC-001 | implemented_verified | UI tests assert default and legacy request URLs remain task scoped | no new implementation | UI test |
| SC-002 | implemented_verified | UI tests assert broad controls are absent and task filters remain | no new implementation | UI test |
| SC-003 | implemented_verified | UI legacy URL test asserts recoverable notice and URL rewrite | no new implementation | UI test |
| SC-004 | implemented_verified | UI tests assert forbidden headers are absent | no new implementation | UI test |
| SC-005 | implemented_verified | backend tests assert `scope=all` and system workflow params fail safe to task-run query | no new implementation | backend unit |
| SC-006 | implemented_verified | `verification.md` records source-design and MM-586 coverage | no new implementation | final verify |
| DESIGN-REQ-005 | implemented_verified | UI control removal plus task-scoped request tests | no new implementation | UI + backend unit |
| DESIGN-REQ-008 | implemented_verified | `TABLE_COLUMNS` and header assertions prove task-oriented columns | no new implementation | UI test |
| DESIGN-REQ-009 | implemented_verified | frontend legacy URL normalization and backend broad-param fail-safe tests | no new implementation | UI + backend unit |
| DESIGN-REQ-017 | implemented_verified | old broad URL handling preserves task filters and shows notice | no new implementation | UI test |
| DESIGN-REQ-025 | implemented_verified | backend task boundary plus JSX text rendering evidence | no new implementation | backend + UI test |

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
