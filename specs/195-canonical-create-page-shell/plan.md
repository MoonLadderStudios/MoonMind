# Implementation Plan: Canonical Create Page Shell

**Branch**: `195-canonical-create-page-shell` | **Date**: 2026-04-17 | **Spec**: `specs/195-canonical-create-page-shell/spec.md` 
**Input**: Single-story feature specification from `specs/195-canonical-create-page-shell/spec.md`

## Summary

Implement MM-376 by making the existing Create page shell explicitly expose the canonical task-first section model while preserving the existing server route, boot payload, create/edit/rerun composition flow, and MoonMind REST-only boundary. The technical approach is to add stable section metadata around existing Create page controls without changing task submission semantics, and to extend focused backend and frontend tests for route hosting, section order, edit/rerun reuse, REST endpoint use, and optional-integration absence.

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI, Python 3.12 for FastAPI route tests 
**Primary Dependencies**: React, FastAPI, existing boot payload helpers, existing task dashboard router, Vitest, pytest 
**Storage**: No new persistent storage 
**Unit Testing**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_task_dashboard.py` and `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx` 
**Integration Testing**: Existing UI request-shape tests exercise the browser-to-MoonMind REST boundary; no new compose dependency is required for this shell story 
**Target Platform**: Mission Control browser UI served by FastAPI 
**Project Type**: Web UI plus FastAPI shell route 
**Performance Goals**: No additional network requests and no extra render-blocking data dependencies 
**Constraints**: Keep `/tasks/new` canonical, keep compatibility routes redirect-only, keep optional integrations optional, keep browser calls behind MoonMind REST endpoints, and preserve edit/rerun reuse of the task composition surface 
**Scale/Scope**: One Create page shell and its existing route/tests

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. Preserves existing task orchestration routes and does not introduce a competing runtime.
- II. One-Click Agent Deployment: PASS. No new services, secrets, or deployment dependencies.
- III. Avoid Vendor Lock-In: PASS. Browser calls stay behind MoonMind APIs instead of direct provider calls.
- IV. Own Your Data: PASS. Task inputs and artifacts continue through MoonMind-controlled endpoints.
- V. Skills Are First-Class and Easy to Add: PASS. The task-first form remains compatible with existing skill and preset selection.
- VI. Replaceable Scaffolding: PASS. Adds contract tests around the UI shell rather than coupling to volatile implementation internals.
- VII. Runtime Configurability: PASS. Runtime configuration remains server-generated and passed through the boot payload.
- VIII. Modular Architecture: PASS. Route hosting remains in the dashboard router; UI shell grouping remains in the Create page entrypoint.
- IX. Resilient by Default: PASS. Optional integrations remain non-blocking and manual authoring stays available.
- X. Continuous Improvement: PASS. Verification evidence will be recorded in `verification.md`.
- XI. Spec-Driven Development: PASS. Runtime changes follow this one-story Moon Spec.
- XII. Canonical Documentation Separation: PASS. Desired-state docs remain canonical; implementation evidence stays under `specs/` and `local-only handoffs`.
- XIII. Pre-Release Compatibility Policy: PASS. No compatibility alias is added; existing redirect behavior is tested and preserved.

## Project Structure

### Documentation (this feature)

```text
specs/195-canonical-create-page-shell/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│ └── create-page-shell.md
├── tasks.md
└── verification.md
```

### Source Code (repository root)

```text
api_service/api/routers/
├── task_dashboard.py
└── task_dashboard_view_model.py

frontend/src/entrypoints/
├── task-create.tsx
└── task-create.test.tsx

tests/unit/api/routers/
└── test_task_dashboard.py
```

**Structure Decision**: Preserve the existing server route and Create page entrypoint. Add explicit canonical section metadata in the React shell, and extend existing route and UI tests rather than introducing new modules.

## Complexity Tracking

No constitution violations.
