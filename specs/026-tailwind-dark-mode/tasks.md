# Tasks: Tailwind Style System Phase 3 Dark Mode

**Input**: Design documents from `/specs/026-tailwind-dark-mode/`  
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`

**Tests**: Validation tasks are included because the specification requires explicit runtime verification for persistence, no-flash first paint, readability, and accent hierarchy.

**Organization**: Tasks are grouped by user story so each story can be implemented and validated independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no direct dependency)
- **[Story]**: User story label (`US1`, `US2`, `US3`)
- `DOC-REQ-*` IDs are carried in task descriptions for traceability

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish a clean baseline and task-level traceability before changing runtime behavior.

- [X] T001 Confirm Phase 3 verification workflow in `specs/026-tailwind-dark-mode/quickstart.md` and align run commands with current repository scripts.
- [X] T002 [P] Capture the initial requirements coverage matrix skeleton in `specs/026-tailwind-dark-mode/contracts/requirements-traceability.md` for `DOC-REQ-001` through `DOC-REQ-010`.
- [X] T003 [P] Rebuild baseline dashboard CSS from `api_service/static/task_dashboard/dashboard.tailwind.css` to `api_service/static/task_dashboard/dashboard.css` with `npm run dashboard:css:min` before feature edits.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Implement shared theme infrastructure required by all user stories.

**⚠️ CRITICAL**: No user story work should start until this phase is complete.

- [X] T004 Add `viewport-fit=cover` viewport meta and initial no-flash bootstrap scaffold in `api_service/templates/task_dashboard.html` (`DOC-REQ-005`, `DOC-REQ-006`, `DOC-REQ-010`).
- [X] T005 Implement shared theme resolver/apply/persist helpers near the top of `api_service/static/task_dashboard/dashboard.js` (`DOC-REQ-003`, `DOC-REQ-004`, `DOC-REQ-010`).
- [X] T006 [P] Add full `.dark` token overrides and dark atmospheric background layer support in `api_service/static/task_dashboard/dashboard.tailwind.css` (`DOC-REQ-001`, `DOC-REQ-009`, `DOC-REQ-010`).
- [X] T007 Regenerate `api_service/static/task_dashboard/dashboard.css` from `api_service/static/task_dashboard/dashboard.tailwind.css` after foundational token/bootstrap changes.
- [X] T008 [P] Extend shell route assertions in `tests/unit/api/routers/test_task_dashboard.py` to validate viewport/meta and theme-toggle shell presence (`DOC-REQ-006`, `DOC-REQ-002`).
- [X] T009 Run foundational validation using `./tools/test_unit.sh` and log outcomes in `specs/026-tailwind-dark-mode/quickstart.md` (`DOC-REQ-002`, `DOC-REQ-006`, `DOC-REQ-010`).

**Checkpoint**: Theme infrastructure is in place and user-story implementation can proceed.

---

## Phase 3: User Story 1 - Operators control and persist dark mode (Priority: P1) 🎯 MVP

**Goal**: Operators can toggle theme from the dashboard shell and retain explicit preference across routes and reloads.

**Independent Test**: Open `/tasks`, change theme, navigate to `/tasks/queue` and `/tasks/orchestrator`, reload, and confirm explicit theme persists.

### Implementation for User Story 1

- [X] T010 [US1] Add an accessible theme toggle control in `api_service/templates/task_dashboard.html` masthead actions (`DOC-REQ-002`).
- [X] T011 [US1] Wire toggle interaction to explicit preference persistence and immediate theme apply in `api_service/static/task_dashboard/dashboard.js` (`DOC-REQ-002`, `DOC-REQ-003`, `DOC-REQ-010`).
- [X] T012 [US1] Ensure route render lifecycle keeps the explicit theme active across dashboard navigation in `api_service/static/task_dashboard/dashboard.js` (`DOC-REQ-003`).

### Validation for User Story 1

- [X] T013 [P] [US1] Add preference/toggle runtime smoke checks in `tests/task_dashboard/test_theme_runtime.js` (`DOC-REQ-002`, `DOC-REQ-003`).
- [X] T014 [US1] Execute `tests/task_dashboard/test_theme_runtime.js` and `./tools/test_unit.sh`, then record US1 evidence in `specs/026-tailwind-dark-mode/contracts/requirements-traceability.md` (`DOC-REQ-002`, `DOC-REQ-003`).

**Checkpoint**: User Story 1 is fully functional and independently testable.

---

## Phase 4: User Story 2 - Default theme follows system preference without flash (Priority: P1)

**Goal**: Unset preference sessions follow system theme (including live changes) and load without first-paint flash.

**Independent Test**: Clear saved theme, load `/tasks` under light/dark system settings, and verify initial paint plus runtime system-change behavior.

### Implementation for User Story 2

- [X] T015 [US2] Refine bootstrap resolution behavior only (preference precedence + first-frame mode) in `api_service/templates/task_dashboard.html` (`DOC-REQ-005`, `DOC-REQ-006`, `DOC-REQ-010`).
- [X] T016 [US2] Implement system-preference listener behavior gated by unset preference in `api_service/static/task_dashboard/dashboard.js` (`DOC-REQ-004`, `DOC-REQ-003`).
- [X] T017 [US2] Add invalid-preference fallback handling in `api_service/static/task_dashboard/dashboard.js` so unresolved values default deterministically (`DOC-REQ-004`, `DOC-REQ-005`).

### Validation for User Story 2

- [X] T018 [P] [US2] Add resolver/system-follow/no-flash runtime checks in `tests/task_dashboard/test_theme_runtime.js` (`DOC-REQ-004`, `DOC-REQ-005`, `DOC-REQ-010`).
- [X] T019 [US2] Execute a 40-run no-flash matrix (20 system-light and 20 system-dark with no saved preference) and capture pass/fail evidence in `specs/026-tailwind-dark-mode/contracts/requirements-traceability.md` (`DOC-REQ-004`, `DOC-REQ-005`, `DOC-REQ-010`).

**Checkpoint**: User Story 2 is fully functional and independently testable.

---

## Phase 5: User Story 3 - Dark mode stays readable and brand-consistent (Priority: P2)

**Goal**: Dark-mode surfaces remain readable and accent hierarchy stays purple-first with restrained warm highlights.

**Independent Test**: Run dark-mode visual sweep on `/tasks`, `/tasks/queue`, and `/tasks/orchestrator`, confirming readability and accent hierarchy.

### Implementation for User Story 3

- [X] T020 [P] [US3] Tune dark-mode table/form/live-output surface styles in `api_service/static/task_dashboard/dashboard.tailwind.css` (`DOC-REQ-007`, `DOC-REQ-009`, `DOC-REQ-001`).
- [X] T021 [US3] Refine dark-mode accent usage for nav/buttons/status chips in `api_service/static/task_dashboard/dashboard.tailwind.css` to keep purple-primary hierarchy (`DOC-REQ-008`, `DOC-REQ-009`).
- [X] T022 [US3] Regenerate `api_service/static/task_dashboard/dashboard.css` after US3 readability/accent updates.

### Validation for User Story 3

- [X] T023 [P] [US3] Add dark-surface token/readability assertions in `tests/task_dashboard/test_theme_runtime.js` for table/form/live-output selectors (`DOC-REQ-007`, `DOC-REQ-009`).
- [X] T024 [US3] Execute dark-mode readability/accent QA from `specs/026-tailwind-dark-mode/quickstart.md` and record evidence in `specs/026-tailwind-dark-mode/contracts/requirements-traceability.md` (`DOC-REQ-007`, `DOC-REQ-008`).

**Checkpoint**: User Story 3 is fully functional and independently testable.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final traceability closure, deterministic build checks, and release-gate validation.

- [X] T025 [P] Reconcile final `DOC-REQ-*` to task/evidence links in `specs/026-tailwind-dark-mode/contracts/requirements-traceability.md` (`DOC-REQ-001`, `DOC-REQ-002`, `DOC-REQ-003`, `DOC-REQ-004`, `DOC-REQ-005`, `DOC-REQ-006`, `DOC-REQ-007`, `DOC-REQ-008`, `DOC-REQ-009`, `DOC-REQ-010`).
- [X] T026 Run `npm run dashboard:css:min`, `./tools/test_unit.sh`, and `node tests/task_dashboard/test_theme_runtime.js`; confirm outputs referenced in `api_service/static/task_dashboard/dashboard.css` and `specs/026-tailwind-dark-mode/quickstart.md` (`DOC-REQ-010`).
- [X] T027 Update Phase 3 completion notes and validation checklist state in `docs/TailwindStyleSystem.md` to reflect shipped runtime behavior and remaining future phases (`DOC-REQ-010`).

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: Starts immediately.
- **Phase 2 (Foundational)**: Depends on Phase 1 and blocks all user stories.
- **Phase 3 (US1)**: Depends on Phase 2; provides MVP.
- **Phase 4 (US2)**: Depends on Phase 2 and should follow US1 to minimize concurrent edits in `api_service/static/task_dashboard/dashboard.js`.
- **Phase 5 (US3)**: Depends on Phase 2 and should follow US2 token/runtime finalization for stable dark-surface tuning.
- **Phase 6 (Polish)**: Depends on all targeted stories.

### User Story Dependencies

- **US1**: No dependency on other stories once foundational work is complete.
- **US2**: Uses shared theme resolver from foundational phase; functionally independent from US3.
- **US3**: Functionally independent after foundational work, but practically benefits from US2 completion to reduce merge churn.

### Within Each User Story

- Runtime implementation tasks precede validation tasks.
- CSS regeneration follows any `dashboard.tailwind.css` modifications.
- Traceability evidence updates happen after execution of validation commands.

### Parallel Opportunities

- Phase 1: `T002` and `T003` can run in parallel.
- Phase 2: `T006` and `T008` can run in parallel after `T004`/`T005` begin.
- US1: `T013` can be drafted in parallel while `T011`/`T012` are in progress.
- US2: `T018` can be drafted in parallel while `T016`/`T017` are in progress.
- US3: `T020` and `T023` can run in parallel once dark token foundation is stable.
- Phase 6: `T025` and `T027` can run in parallel before the final execution pass in `T026`.

---

## Parallel Example: User Story 1

```bash
# Parallelizable US1 work
Task: "T011 [US1] Wire toggle interaction in api_service/static/task_dashboard/dashboard.js"
Task: "T013 [US1] Add preference/toggle smoke checks in tests/task_dashboard/test_theme_runtime.js"
```

## Parallel Example: User Story 2

```bash
# Parallelizable US2 work
Task: "T016 [US2] Implement system-preference listener in api_service/static/task_dashboard/dashboard.js"
Task: "T018 [US2] Add resolver/system-follow checks in tests/task_dashboard/test_theme_runtime.js"
```

## Parallel Example: User Story 3

```bash
# Parallelizable US3 work
Task: "T020 [US3] Tune dark-mode readability surfaces in api_service/static/task_dashboard/dashboard.tailwind.css"
Task: "T023 [US3] Add dark-surface assertions in tests/task_dashboard/test_theme_runtime.js"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 (Setup).
2. Complete Phase 2 (Foundational).
3. Complete Phase 3 (US1).
4. Validate US1 independently before expanding scope.

### Incremental Delivery

1. Land US1 for operator-controlled persistence behavior.
2. Land US2 for system-follow and no-flash behavior.
3. Land US3 for dark-mode readability and accent hierarchy tuning.
4. Close with Phase 6 release-gate verification and documentation state updates.

### Parallel Team Strategy

1. Shared work: one contributor handles template/JS foundational setup while another drafts runtime smoke tests.
2. Story execution: after foundation, split ownership by story to reduce overlap (`US1` JS-heavy, `US2` bootstrap + listener, `US3` CSS-heavy).
3. Consolidation: final integrator runs full validation suite and final traceability closure.
