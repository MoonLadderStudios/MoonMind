# Implementation Plan: Canonical Task Run List Route

**Branch**: `run-jira-orchestrate-for-thor-370-canoni-ecf4f312` | **Date**: 2026-05-05 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `specs/298-canonical-task-run-list/spec.md`

**Note**: `.specify/scripts/bash/setup-plan.sh --json` could not be used because the current git branch is `run-jira-orchestrate-for-thor-370-canoni-ecf4f312`, which does not match the repository's Spec Kit feature-branch naming guard. This plan uses `.specify/feature.json` and the active feature directory as the source of truth.

## Summary

Complete the THOR-370 runtime story by making `/tasks/list` a fail-safe task-run list instead of an ordinary broad workflow browser. Current backend route redirects and task-list boot configuration are largely present, and the Temporal execution API has a task-scope query. The visible list UI still exposes raw Scope, Workflow Type, and Entry controls and can request broad workflow scopes from the normal page. Planned work is to keep the existing canonical route behavior, remove ordinary broad-workflow controls from `/tasks/list`, normalize legacy broad-workflow URLs to task-safe behavior or explicit non-list routing/messaging, preserve permission-gated diagnostics separation, and add focused backend, UI, and integration-style coverage.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `api_service/api/routers/task_dashboard.py` defines `/tasks/list`; `tests/unit/api/routers/test_task_dashboard.py` renders it. | Preserve route. | final regression |
| FR-002 | implemented_verified | `/tasks` redirects to `/tasks/list`; `/tasks/tasks-list` redirects to `/tasks/list`; covered by `test_root_route_renders_dashboard_shell` and `test_alias_routes_redirect_to_canonical_paths`. | Preserve redirects. | final regression |
| FR-003 | implemented_unverified | `/tasks/list` renders page key `tasks-list`, dashboard config, and `data_wide_panel=True`; tests only check dashboard config text, not full page identity/layout payload. | Add verification for boot payload page key, wide layout, and initial path. | unit |
| FR-004 | implemented_unverified | Frontend fetches `/api/executions`; route boot config points to MoonMind API paths. No test asserts absence of direct Temporal/provider URLs. | Add frontend/API-shell assertions that list loading uses MoonMind API paths only. | unit |
| FR-005 | partial | API supports `scope=tasks` as `WorkflowType="MoonMind.Run" AND mm_entry="run"`. UI defaults to tasks but changes to `scope=all`, `scope=system`, and workflow filters from ordinary controls. | Make ordinary `/tasks/list` requests remain task-run bounded, including legacy URL normalization. | unit + integration |
| FR-006 | partial | Backend task scope excludes non-run workflows. UI can request broad scopes and manifest entry from the normal page. | Remove or disable broad ordinary controls and prevent manifest/system rows from ordinary list requests. | unit + integration |
| FR-007 | missing | Current UI preserves `scope=all`, `scope=system`, and `entry=manifest` as ordinary list filters; tests expect raw all-workflows scope exposure. | Add fail-safe compatibility parser that preserves task visibility, routes manifest links to Manifests, routes authorized diagnostics when available, or shows recoverable copy. | unit + integration |
| FR-008 | missing | No ordinary-list diagnostics routing/messaging boundary for broad workflow compatibility URLs was found. | Define and implement permission-gated diagnostics handoff or recoverable message behavior for broad workflow URLs. | unit + integration |
| FR-009 | partial | Desktop table columns omit `Kind`, `Workflow Type`, and `Entry`, but top filters expose Scope, Workflow Type, and Entry; mobile cards include `workflowType` metadata. | Remove ordinary broad-workflow controls and broad metadata exposure from normal list table/cards. | unit |
| FR-010 | partial | API owner scoping exists for non-admin users; normal page can still send broad workflow scope params. No facet tests apply because facet UI is not implemented here. | Ensure normal URL/filter handling cannot widen visibility and add regression coverage for broad URL inputs. | unit + integration |
| FR-011 | implemented_unverified | THOR-370 and the original brief are preserved in `spec.md`; downstream artifacts not yet generated. | Preserve THOR-370 in plan, tasks, verification, commit text, and PR metadata. | final verify |
| SCN-001 | implemented_unverified | Route renders task-list shell with dashboard config. | Add stronger boot payload/page identity verification. | unit |
| SCN-002 | implemented_verified | Existing route redirect tests cover `/tasks` and `/tasks/tasks-list`. | Preserve. | final regression |
| SCN-003 | partial | Task scope exists; normal UI can request system workflows. | Add UI and API tests with ordinary task plus system workflow data. | unit + integration |
| SCN-004 | missing | Existing tests assert `scope=all` is exposed and `entry=manifest` remains in ordinary URL. | Replace with fail-safe broad URL behavior tests. | unit + integration |
| SCN-005 | missing | No diagnostics handoff or permission-gated broad workflow behavior from ordinary list compatibility URLs was found. | Add admin-vs-ordinary behavior for diagnostics route/message decision. | unit + integration |
| SCN-006 | partial | Table headers omit Kind/Workflow Type/Entry; controls and mobile card metadata still expose workflow concepts. | Add visual/semantic tests for no ordinary broad workflow controls or columns. | unit |
| SC-001 | implemented_unverified | Route and alias coverage exists; page key coverage is incomplete. | Add 100% canonical/legacy route verification. | unit |
| SC-002 | partial | API task scope can exclude system rows; ordinary UI can request broad scopes. | Verify mixed task/system/manifest data yields zero non-task rows in ordinary list. | integration |
| SC-003 | missing | Broad compatibility URL fail-safe behavior is absent. | Cover at least four broad URL cases. | unit + integration |
| SC-004 | partial | Table columns pass; ordinary controls/card metadata fail the broader safety intent. | Verify zero ordinary broad workflow columns/controls. | unit |
| SC-005 | implemented_unverified | Fetch path appears MoonMind-owned; no explicit regression. | Add assertion for `/api/executions` only. | unit |
| SC-006 | implemented_unverified | THOR-370 preserved in `spec.md`; later artifacts pending. | Preserve traceability in all generated artifacts. | final verify |
| DESIGN-REQ-002 | partial | Route/redirect/boot config mostly present. | Add stronger verification for task-list identity and MoonMind-owned browser data access. | unit |
| DESIGN-REQ-003 | partial | Product stance is partially violated by ordinary scope/workflow controls. | Remove ordinary broad workflow browsing from normal page. | unit + integration |
| DESIGN-REQ-004 | partial | Table omits forbidden columns; controls and mobile metadata expose broad workflow browsing concepts. | Remove broad workflow controls/metadata from ordinary list. | unit |
| DESIGN-REQ-008 | missing | Diagnostics escape hatch is not represented in normal list compatibility behavior. | Add diagnostics handoff/message behavior with permission boundary. | unit + integration |
| DESIGN-REQ-015 | missing | Old broad URLs are honored as ordinary filters today. | Implement fail-safe compatibility mapping for old scope/workflow/entry params. | unit + integration |
| DESIGN-REQ-022 | partial | Owner scoping exists; broad filter params still reach normal query. | Ensure URL filters cannot bypass ordinary task visibility and labels render as text. | unit + integration |

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React for Mission Control UI  
**Primary Dependencies**: FastAPI, Pydantic v2, Temporal Python SDK, React, TanStack Query, Vitest, Testing Library, pytest  
**Storage**: Existing Temporal visibility/search attributes and execution projection data only; no new persistent storage  
**Unit Testing**: `./tools/test_unit.sh` for final verification; targeted `pytest tests/unit/api/routers/test_task_dashboard.py tests/unit/api/routers/test_executions.py` and `npm run ui:test -- frontend/src/entrypoints/tasks-list.test.tsx` during iteration  
**Integration Testing**: Add focused hermetic integration coverage in `tests/integration/api/test_tasks_list_visibility.py` for mixed task/system/manifest visibility and broad compatibility URLs; run targeted pytest during iteration and `./tools/test_integration.sh` for required hermetic `integration_ci` validation when the new test is marked for CI  
**Target Platform**: Mission Control web UI served by the MoonMind API service in Linux containers  
**Project Type**: Web application with FastAPI backend and React frontend  
**Performance Goals**: Compatibility URL handling adds no extra list round trip for ordinary task-list loads; page load continues to use one list request per query state  
**Constraints**: Normal `/tasks/list` must not become a generic Temporal namespace browser; no direct browser calls to Temporal, GitHub, Jira, object storage, or runtime providers; no secrets in URL state; broad diagnostics must be permission-gated  
**Scale/Scope**: One Tasks List route story covering route aliases, ordinary list visibility, compatibility URL fail-safe behavior, and diagnostics separation

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Gate Result | Plan Alignment |
| --- | --- | --- |
| I. Orchestrate, Don't Recreate | PASS | Keeps Mission Control as an orchestration surface over existing execution data instead of recreating Temporal browsing behavior in the task list. |
| II. One-Click Agent Deployment | PASS | No new external service or setup prerequisite. |
| III. Avoid Vendor Lock-In | PASS | Uses MoonMind-owned task/execution surfaces; no provider-specific behavior. |
| IV. Own Your Data | PASS | Reads existing MoonMind execution data and preserves operator-controlled artifacts. |
| V. Skills Are First-Class and Easy to Add | PASS | Does not change skill runtime semantics. |
| VI. Replaceable Scaffolding | PASS | Maintains thin route/query contracts with explicit tests. |
| VII. Runtime Configurability | PASS | Respects existing dashboard runtime configuration and feature flags. |
| VIII. Modular and Extensible Architecture | PASS | Keeps changes in task dashboard route, execution list boundary, and Tasks List UI. |
| IX. Resilient by Default | PASS | Broad or stale URLs fail safely instead of leaking rows. |
| X. Facilitate Continuous Improvement | PASS | Recoverable messages and verification evidence make outcomes observable. |
| XI. Spec-Driven Development | PASS | Plan follows the single-story THOR-370 spec and preserves traceability. |
| XII. Canonical Documentation Separation | PASS | Runtime work and rollout notes stay in `specs/298-canonical-task-run-list`; canonical docs remain desired-state references. |
| XIII. Pre-Release Velocity | PASS | Internal broad-list affordances may be removed cleanly instead of preserved as aliases. |
| Product and Operational Constraints | PASS | No secrets are introduced; Mission Control remains the operator-facing route. |

Post-design re-check: PASS. The Phase 1 artifacts preserve the same boundaries and introduce no constitution violations.

## Project Structure

### Documentation (this feature)

```text
specs/298-canonical-task-run-list/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── tasks-list-visibility-contract.md
└── checklists/
    └── requirements.md
```

### Source Code (repository root)

```text
api_service/api/routers/
├── task_dashboard.py
└── executions.py

frontend/src/entrypoints/
├── tasks-list.tsx
└── tasks-list.test.tsx

tests/unit/api/routers/
├── test_task_dashboard.py
└── test_executions.py

tests/e2e/
└── test_mission_control_react_mount_browser.py
```

**Structure Decision**: Use the existing task dashboard router for canonical route and boot payload behavior, the existing executions router for task-scope list boundaries, and the existing Tasks List React entrypoint for ordinary-list URL/control behavior. Add no new storage or package boundary.

## Complexity Tracking

No constitution violations or justified complexity exceptions.
