# Tasks: Unify Manifest Route And Navigation

**Input**: Design artifacts in `specs/211-unify-manifest-route-navigation/`

## Phase 1: Tests First

- [X] T001 Add router regression coverage for legacy `/tasks/manifests/new` redirect and single manifest navigation destination in `tests/unit/api/routers/test_task_dashboard.py`.
- [X] T002 Add frontend coverage proving the Manifests page renders run submission and recent runs together in `frontend/src/entrypoints/manifests.test.tsx`.
- [X] T003 Add frontend coverage for inline YAML and registry manifest submission through existing manifest APIs with in-place refresh in `frontend/src/entrypoints/manifests.test.tsx`.

## Phase 2: Implementation

- [X] T004 Redirect `/tasks/manifests/new` to `/tasks/manifests` and remove the legacy route from canonical 404 route guidance in `api_service/api/routers/task_dashboard.py`.
- [X] T005 Remove the dedicated `Manifest Submit` top-level navigation item in `api_service/templates/_navigation.html`.
- [X] T006 Merge manifest submission into the Manifests page and refresh recent runs after successful submit in `frontend/src/entrypoints/manifests.tsx`.
- [X] T007 Remove the unused standalone manifest submit entrypoint from `frontend/src/entrypoints/mission-control-app.tsx` and delete `frontend/src/entrypoints/manifest-submit.tsx`.

## Phase 3: Validation

- [X] T008 Run focused router tests.
- [X] T009 Run focused Manifests frontend tests.
- [X] T010 Run final unit verification or document any local blocker.
- [X] T011 Run final MoonSpec verification and record outcome.
