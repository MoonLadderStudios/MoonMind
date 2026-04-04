# Tasks: mission-control-single-entrypoint

**Input**: Design documents from `/specs/127-mission-control-single-entrypoint/`
**Prerequisites**: plan.md, spec.md, research.md, quickstart.md

## Phase 1: Setup

- [X] T001 Record the single-entry Mission Control contract in `specs/127-mission-control-single-entrypoint/spec.md`, `plan.md`, `research.md`, `quickstart.md`, and `tasks.md`

## Phase 2: User Story 1 - FastAPI routes boot one Mission Control bundle (Priority: P1)

**Goal**: Keep the existing FastAPI route map and boot payload while switching backend asset injection to one shared frontend bundle.

**Independent Test**: Backend route tests prove built-asset and dev-server mode now inject only the shared `mission-control` entrypoint.

- [X] T002 [US1] Update `frontend/vite.config.ts` to emit only the shared `mission-control` entrypoint
- [X] T003 [US1] Update `api_service/api/routers/task_dashboard.py`, `api_service/ui_assets.py`, `api_service/templates/react_dashboard.html`, and `api_service/test_ui_route.py` so Mission Control routes inject the shared bundle while keeping page-specific boot payloads
- [X] T004 [US1] Update backend asset/route tests in `tests/api_service/test_ui_assets.py`, `tests/api_service/test_ui_assets_strict.py`, `tests/api_service/test_task_dashboard_ui_assets_errors.py`, and `tests/unit/api/routers/test_task_dashboard.py`

## Phase 3: User Story 2 - One React root selects the page module lazily (Priority: P1)

**Goal**: Replace per-page boot files and the separate alerts root with one app shell that lazy-loads the requested page module from `payload.page`.

**Independent Test**: A frontend test proves the shared Mission Control entry renders alerts plus the requested page component, and it fails explicitly on unknown pages.

- [X] T005 [US2] Add the shared Mission Control app shell and lazy page registry in `frontend/src/entrypoints/mission-control.tsx`
- [X] T006 [US2] Refactor page modules in `frontend/src/entrypoints/*.tsx` so they export lazy-loadable components without `mountPage(...)` side effects, and fold `dashboard-alerts.tsx` into the shared shell
- [X] T007 [US2] Add frontend coverage in `frontend/src/entrypoints/mission-control.test.tsx` for page selection, alert-shell rendering, and unknown-page fallback

## Phase 4: User Story 3 - Manifest verification follows the shared entry contract (Priority: P2)

**Goal**: Reduce manifest verification and docs to the single-entry architecture.

**Independent Test**: The manifest verification script succeeds for a synthetic repo with one `mission-control` entry and fails when that shared entry is missing.

- [X] T008 [US3] Simplify `tools/verify_vite_manifest.py` and update `tests/tools/test_verify_vite_manifest.py` for the shared entrypoint contract
- [X] T009 [US3] Update canonical docs in `README.md`, `docs/UI/MissionControlArchitecture.md`, and `docs/UI/TypeScriptSystem.md` to describe the shared `mission-control` entrypoint and `payload.page` page selection

## Phase 5: Validation

- [X] T010 Run `SPECIFY_FEATURE=127-mission-control-single-entrypoint .specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
- [X] T011 Run `npm run ui:test -- frontend/src/entrypoints/mission-control.test.tsx`
- [X] T012 Run `./tools/test_unit.sh --python-only --no-xdist tests/api_service/test_ui_assets.py tests/api_service/test_ui_assets_strict.py tests/api_service/test_task_dashboard_ui_assets_errors.py tests/unit/api/routers/test_task_dashboard.py tests/tools/test_verify_vite_manifest.py`
- [X] T013 Run `npm run ui:build:check`
- [X] T014 Run `./tools/test_unit.sh`
