# Implementation Plan: Thin Dashboard Task UI

**Branch**: `017-thin-dashboard-ui` | **Date**: 2026-02-15 | **Spec**: `specs/017-thin-dashboard-ui/spec.md`  
**Input**: Feature specification from `/specs/017-thin-dashboard-ui/spec.md`

## Summary

Implement Strategy 1 with a thin dashboard hosted by the existing API service. Add dashboard routes, one HTML shell template, and static JavaScript/CSS that consume existing Agent Queue and Orchestrator REST endpoints for list, submit, and detail experiences with polling. Represent SpecKit launches as queue tasks selected by skill id and optional skill args.

## Technical Context

**Language/Version**: Python 3.11 (backend), vanilla JavaScript and HTML/CSS (dashboard client)  
**Primary Dependencies**: FastAPI, Jinja2 templates, existing MoonMind routers and auth providers  
**Storage**: Existing PostgreSQL/API stores and artifact storage (no new persistence for MVP)  
**Testing**: Unit/API tests executed via `./tools/test_unit.sh`  
**Target Platform**: Linux Docker deployment used by current MoonMind API service  
**Project Type**: Web dashboard served from existing backend service  
**Performance Goals**: Polling-based updates for active task views without full reload  
**Constraints**: Reuse existing REST endpoints; avoid worker-token usage in user UI flows; preserve partial rendering when one source fails  
**Scale/Scope**: MVP pages for consolidated monitoring, source lists, submit forms, and source detail pages

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- `.specify/memory/constitution.md` currently contains placeholders and no enforceable MUST/SHOULD constraints.
- Repository workflow constraints from `AGENTS.md` apply:
  - spec numbering follows global next prefix (`017`);
  - unit tests run via `./tools/test_unit.sh`.

**Gate Status**: PASS WITH NOTE.

## Project Structure

### Documentation (this feature)

```text
specs/017-thin-dashboard-ui/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── dashboard-routes-contract.md
│   └── dashboard-view-model-contract.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/
├── main.py
├── api/routers/
│   └── task_dashboard.py                # new dashboard page routes
├── templates/
│   └── task_dashboard.html              # new dashboard shell
└── static/
    └── task_dashboard/
        ├── dashboard.js                 # client-side routing + API integration
        └── dashboard.css                # dashboard styling

tests/unit/api/routers/
└── test_task_dashboard.py               # new router/template tests

tests/unit/api/routers/
└── test_task_dashboard_view_model.py    # normalization and polling helper tests
```

**Structure Decision**: Extend the current API service template/static stack to ship the MVP dashboard without adding a separate frontend build pipeline.

## Phase 0: Research Plan

1. Confirm current endpoint coverage and payload fields for queue/orchestrator list, detail, submit, events, and artifacts, plus queue skill metadata for SpecKit launch flows.
2. Define a normalized dashboard run model mapping source-specific statuses to common UI states.
3. Decide route handling strategy (server-rendered shell plus client-side path-driven rendering) that supports `/tasks/...` pages.
4. Define polling and partial-failure behavior for combined multi-source views.
5. Define auth boundary between user dashboard requests and worker-token-only mutation endpoints.

## Phase 1: Design Outputs

- `research.md`: key implementation decisions and alternatives.
- `data-model.md`: client-side view models and state transitions.
- `contracts/dashboard-routes-contract.md`: required route surface and page responsibilities.
- `contracts/dashboard-view-model-contract.md`: normalized model and status mapping contract.
- `quickstart.md`: developer run/test workflow and manual verification checklist.

## Post-Design Constitution Re-check

- Design remains within runtime implementation scope and includes explicit validation tests.
- No constitution-specific violations beyond placeholder constitution baseline.

**Gate Status**: PASS.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |
