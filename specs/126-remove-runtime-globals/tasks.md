# Tasks: remove-runtime-globals

**Input**: Design documents from `/specs/126-remove-runtime-globals/`
**Prerequisites**: plan.md, spec.md, research.md, quickstart.md

## Phase 1: Setup

- [X] T001 Confirm the remaining global runtime paths and record the feature artifacts in `specs/126-remove-runtime-globals/spec.md`, `specs/126-remove-runtime-globals/plan.md`, `specs/126-remove-runtime-globals/research.md`, `specs/126-remove-runtime-globals/quickstart.md`, and `specs/126-remove-runtime-globals/tasks.md`

## Phase 2: User Story 1 - Skills preview runs without template globals (Priority: P1)

**Goal**: Make the skills entrypoint own markdown parsing instead of relying on a template global.

**Independent Test**: The skills page still renders markdown previews safely, and the shared dashboard template no longer injects the parser CDN script.

- [X] T002 [US1] Add the packaged markdown parser dependency in `package.json` and `package-lock.json`
- [X] T003 [US1] Import the parser directly in `frontend/src/entrypoints/skills.tsx` and remove the `window.marked` fallback path
- [X] T004 [US1] Remove the marked CDN script from `api_service/templates/react_dashboard.html`
- [X] T005 [US1] Update `frontend/src/entrypoints/skills.test.tsx` so the skills preview tests exercise direct parser imports instead of a template global

## Phase 3: User Story 2 - Custom element registration is owned locally (Priority: P1)

**Goal**: Delete the stale global custom-element compatibility hook from the shared dashboard shell.

**Independent Test**: The shared dashboard shell renders without a `customElements.define` monkeypatch, and route tests confirm the removed globals stay removed.

- [X] T006 [US2] Remove the `mce-autosize-textarea` `customElements.define` monkeypatch from `api_service/templates/react_dashboard.html`
- [X] T007 [US2] Add dashboard shell assertions in `tests/unit/api/routers/test_task_dashboard.py` proving `react_dashboard.html` no longer injects the marked CDN script or custom-elements monkeypatch

## Phase 4: Validation

- [X] T008 Run `npm run ui:typecheck`, `npm run ui:test -- frontend/src/entrypoints/skills.test.tsx`, and `npm run ui:build:check`
- [X] T009 Run `./tools/test_unit.sh`
- [X] T010 Run `.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`
