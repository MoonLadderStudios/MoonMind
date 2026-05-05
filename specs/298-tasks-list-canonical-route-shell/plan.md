# Implementation Plan: Tasks List Canonical Route and Shell

**Branch**: `298-tasks-list-canonical-route-shell` | **Date**: 2026-05-05 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `specs/298-tasks-list-canonical-route-shell/spec.md`

## Summary

Implement the MM-585 runtime story by preserving `/tasks/list` as the canonical Tasks List route, redirecting legacy list entrypoints, serving the shared Mission Control React shell with server-generated dashboard configuration, and validating that the current Tasks List page retains one control deck, one wide data slab, live update controls, polling/disabled/page-size/pagination surfaces, and MoonMind API-only data loading. Repo inspection found the required implementation and tests already present; this MoonSpec run records traceable artifacts and reruns focused validation rather than regenerating already valid route behavior.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `api_service/api/routers/task_dashboard.py` defines `@router.get("/tasks/list")`; `tests/unit/api/routers/test_task_dashboard.py` renders `/tasks/list` | no code change | focused unit route tests |
| FR-002 | implemented_verified | `task_dashboard_root` redirects `/tasks` to `/tasks/list`; `task_tasks_list_route` redirects `/tasks/tasks-list` to `/tasks/list`; `test_root_route_renders_dashboard_shell` and `test_alias_routes_redirect_to_canonical_paths` cover redirects | no code change | focused unit route tests |
| FR-003 | implemented_verified | `/tasks/list` route calls `_render_react_page`; route tests assert React boot shell assets and root | no code change | focused unit route tests |
| FR-004 | implemented_verified | `/tasks/list` passes page key `tasks-list`; `frontend/src/entrypoints/tasks-list.test.tsx` uses the `tasks-list` boot payload; browser smoke covers `/tasks/list` content when enabled | no code change | focused unit/UI tests |
| FR-005 | implemented_verified | `/tasks/list` passes `initial_data={"dashboardConfig": build_runtime_config(list_path)}` and `data_wide_panel=True`; route tests assert dashboard config and `dataWidePanel:true` | no code change | focused unit route tests |
| FR-006 | implemented_verified | `frontend/src/entrypoints/tasks-list.test.tsx` asserts one `.task-list-control-deck` and one `.task-list-data-slab.panel--data` | no code change | focused UI tests |
| FR-007 | implemented_verified | `frontend/src/entrypoints/tasks-list.test.tsx` asserts Live updates and polling copy in the control deck | no code change | focused UI tests |
| FR-008 | implemented_verified | `frontend/src/entrypoints/tasks-list.test.tsx` covers disabled list state and recoverable shell behavior | no code change | focused UI tests |
| FR-009 | implemented_verified | `frontend/src/entrypoints/tasks-list.test.tsx` asserts page-size selector and next-page pagination behavior | no code change | focused UI tests |
| FR-010 | implemented_verified | `frontend/src/entrypoints/tasks-list.tsx` builds fetches from `payload.apiBase` to `/executions`; tests assert `/api/executions?...` request URLs | no code change | focused UI tests |
| FR-011 | implemented_verified | `spec.md`, this plan, `tasks.md`, and `verification.md` preserve `MM-585` | no code change | final verify evidence recorded |
| SC-001 | implemented_verified | route tests inspect `/tasks/list` boot payload and layout | no code change | focused unit route tests |
| SC-002 | implemented_verified | route tests inspect `/tasks` and `/tasks/tasks-list` redirects | no code change | focused unit route tests |
| SC-003 | implemented_verified | UI tests inspect one control deck and one data slab | no code change | focused UI tests |
| SC-004 | implemented_verified | UI tests cover live updates, polling copy, disabled notice, page size, and pagination | no code change | focused UI tests |
| SC-005 | implemented_verified | UI tests assert MoonMind API fetch URLs built from boot payload base | no code change | focused UI tests |
| SC-006 | implemented_verified | `verification.md` confirms MM-585 and source IDs are preserved and covered | no code change | final verify evidence recorded |
| DESIGN-REQ-001 | implemented_verified | same as FR-001 | no code change | focused unit route tests |
| DESIGN-REQ-002 | implemented_verified | same as FR-002 | no code change | focused unit route tests |
| DESIGN-REQ-003 | implemented_verified | same as FR-003 | no code change | focused unit route tests |
| DESIGN-REQ-004 | implemented_verified | same as FR-004 and FR-005 | no code change | focused unit route tests |
| DESIGN-REQ-006 | implemented_verified | same as FR-006 through FR-010 | no code change | focused UI tests |

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React for Mission Control UI  
**Primary Dependencies**: FastAPI, Pydantic v2 route/runtime config helpers, React, TanStack Query, Vitest, Testing Library, pytest  
**Storage**: No new persistent storage; existing dashboard runtime configuration and execution-list API calls only  
**Unit Testing**: pytest via `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`; focused backend route tests via `pytest tests/unit/api/routers/test_task_dashboard.py -q`  
**Integration Testing**: Vitest/Testing Library integration-style UI tests via `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx` in this managed shell; `npm run ui:test -- frontend/src/entrypoints/tasks-list.test.tsx` is the intended developer command when npm resolves local binaries; optional Playwright smoke remains gated by `RUN_E2E_TESTS=1`  
**Target Platform**: MoonMind API service and Mission Control frontend  
**Project Type**: FastAPI backend with React/Vite frontend entrypoints  
**Performance Goals**: No additional route work or browser requests beyond existing shell boot and bounded execution-list fetches  
**Constraints**: Runtime implementation workflow; no raw credentials; browser must use MoonMind API routes only; source design coverage remains under `specs/` rather than mutating canonical docs  
**Scale/Scope**: One route/shell story for the Tasks List page; later column-filter redesign work is out of scope

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I Orchestrate, Don't Recreate: PASS. Work stays inside existing dashboard route and React entrypoint boundaries.
- II One-Click Agent Deployment: PASS. No new services, secrets, or deployment prerequisites.
- III Avoid Vendor Lock-In: PASS. Browser access remains routed through MoonMind APIs rather than direct provider calls.
- IV Own Your Data: PASS. Runtime config and execution data remain MoonMind-owned control-plane data.
- V Skills Are First-Class: PASS. No skill runtime mutation.
- VI Replaceable Scaffolding: PASS. Existing route/UI contracts are anchored by tests.
- VII Runtime Configurability: PASS. The page consumes server-generated dashboard configuration.
- VIII Modular Architecture: PASS. Behavior remains scoped to dashboard router and Tasks List entrypoint.
- IX Resilient by Default: PASS. Disabled list configuration remains a recoverable shell state.
- X Continuous Improvement: PASS. Verification artifacts preserve outcome evidence.
- XI Spec-Driven Development: PASS. This plan follows the single-story spec.
- XII Canonical Documentation Separation: PASS. Implementation tracking is recorded under this feature spec, not in canonical docs.
- XIII Pre-release Compatibility Policy: PASS. The story preserves the explicitly required external route redirect only; no new internal compatibility shims are introduced.

## Project Structure

### Documentation (this feature)

```text
specs/298-tasks-list-canonical-route-shell/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── tasks-list-route-shell.md
├── checklists/
│   └── requirements.md
├── moonspec_align_report.md
├── tasks.md
└── verification.md
```

### Source Code (repository root)

```text
api_service/api/routers/task_dashboard.py
tests/unit/api/routers/test_task_dashboard.py
frontend/src/entrypoints/tasks-list.tsx
frontend/src/entrypoints/tasks-list.test.tsx
tests/e2e/test_mission_control_react_mount_browser.py
```

**Structure Decision**: Keep the canonical route and shell behavior in the existing FastAPI dashboard router and React entrypoint. This run does not require new production modules.

## Complexity Tracking

No constitution violations.
