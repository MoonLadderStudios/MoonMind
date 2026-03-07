# Tasks: Task UI Queue Layout Switching

**Input**: Design documents from `/specs/037-queue-layout-switch/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Required (spec demands production runtime changes plus automated validation).

**Organization**: Tasks grouped by user story to keep increments independently deliverable.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Capture fixtures and verification scaffolding before refactors.

- [X] T001 Create shared queue/manifests sample data module in `tests/task_dashboard/__fixtures__/queue_rows.js` for upcoming layout tests (covers DOC-REQ-007 for consistent DOM samples).
- [X] T002 [P] Add gzip measurement + responsive QA log template to `specs/037-queue-layout-switch/quickstart.md` so results can be recorded during later phases (DOC-REQ-008).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish shared field definitions so every layout consumes the same data.

- [X] T003 Implement `queueFieldDefinitions` plus `renderQueueFieldValue()` adjacent to `toQueueRows()` inside `api_service/static/task_dashboard/dashboard.js`, ensuring each entry has `{ key, label, render, tableSection }` metadata (DOC-REQ-002).
- [X] T004 Refactor the legacy queue table renderer into `renderQueueTable(rows)` that iterates `queueFieldDefinitions`, adds a `.queue-table-wrapper` container, and preserves the table empty-state copy in `api_service/static/task_dashboard/dashboard.js` (DOC-REQ-001, DOC-REQ-005).

---

## Phase 3: User Story 1 - Monitor Queue From Phone (Priority: P1) 🎯 MVP

**Goal**: Deliver mobile card layout for `/tasks/queue` without breaking filters or auto-refresh.
**Independent Test**: Load `/tasks/queue` at ≤414 px and confirm `.queue-card-list` entries mirror queue data + update with filters.

### Tests for User Story 1

- [X] T005 [P] [US1] Extend `tests/task_dashboard/test_queue_layouts.js` with fixture-driven snapshot/unit tests that fail until `renderQueueCards()` outputs header/meta/definition/action structure per DOC-REQ-002, DOC-REQ-004, DOC-REQ-007.
- [X] T006 [P] [US1] Add tests covering `renderQueueLayouts()` empty states and filter re-render hooks in `tests/task_dashboard/test_queue_layouts.js` so both cards and tables are asserted together (DOC-REQ-001, DOC-REQ-003).

### Implementation for User Story 1

- [X] T007 [US1] Implement `renderQueueCards(rows)` in `api_service/static/task_dashboard/dashboard.js` to emit semantic `<ul role="list">` markup with queue-only data, queue/skill metadata, timestamps, and CTA links (DOC-REQ-001, DOC-REQ-004, DOC-REQ-007).
- [X] T008 [US1] Build `renderQueueLayouts(rows)` wrapper plus wiring in `renderQueueListPage()` to insert combined `.queue-layouts` markup during initial render and auto-refresh callbacks inside `api_service/static/task_dashboard/dashboard.js` (DOC-REQ-001, DOC-REQ-003).
- [X] T009 [P] [US1] Update queue filter + sorting helpers (`renderQueueFilters`, `renderQueueList`, auto-refresh callbacks) to call `renderQueueLayouts(filteredRows)` so no duplicate fetch logic exists (DOC-REQ-003).
- [X] T010 [US1] Define `.queue-layouts`, `.queue-card-*`, and `.queue-table-wrapper` responsive classes in `api_service/static/task_dashboard/dashboard.tailwind.css`, showing cards below 768 px while hiding tables unless flagged (DOC-REQ-001, DOC-REQ-004, DOC-REQ-006, DOC-REQ-007).
- [ ] T011 [US1] Run manual mobile QA at 360–414 px on `/tasks/queue`, validating ARIA roles + filter refresh, then log findings in `specs/037-queue-layout-switch/quickstart.md` (DOC-REQ-001, DOC-REQ-004, DOC-REQ-007).

---

## Phase 4: User Story 2 - Preserve Desktop Density (Priority: P2)

**Goal**: Keep the trusted table layout for ≥768 px viewports and Active dashboard contexts.
**Independent Test**: Compare table headers/order with pre-change output at ≥1024 px to ensure zero regressions.

### Tests for User Story 2

- [X] T012 [P] [US2] Add assertions in `tests/task_dashboard/test_queue_layouts.js` that `renderQueueTable()` headers follow `queueFieldDefinitions` order and render all legacy columns (DOC-REQ-001, DOC-REQ-005).
- [X] T013 [P] [US2] Write tests covering `renderActivePage()` and mixed-source datasets to verify orchestrator/manifests rows remain table-only while queue subsets go through `renderQueueLayouts` (DOC-REQ-003, DOC-REQ-007).

### Implementation for User Story 2

- [X] T014 [US2] Wire `renderActivePage()` (and any shared queue subsets) in `api_service/static/task_dashboard/dashboard.js` to sort queue rows then feed them to `renderQueueLayouts`, keeping orchestrator/manifests renderers untouched (DOC-REQ-003).
- [X] T015 [P] [US2] Enhance `renderQueueLayouts()` logic so it sets `data-sticky-table` when non-queue rows are present, guaranteeing tables remain visible even on small screens (DOC-REQ-001, DOC-REQ-003).
- [X] T016 [US2] Extend desktop-specific CSS in `api_service/static/task_dashboard/dashboard.tailwind.css` so `.queue-card-list` hides at `min-width:768px` while `.queue-table-wrapper` retains density, including dark-mode tokens (DOC-REQ-001, DOC-REQ-006).
- [ ] T017 [US2] Perform ≥1024 px desktop QA on `/tasks/queue` and `/tasks/active`, confirming column parity + sticky table fallback, and record outcomes in `specs/037-queue-layout-switch/quickstart.md` (DOC-REQ-001, DOC-REQ-003, DOC-REQ-005).

---

## Phase 5: User Story 3 - Extend Queue Metadata Once (Priority: P3)

**Goal**: Make adding new queue fields a single change shared by cards and tables.
**Independent Test**: Append a temporary definition and confirm both layouts update with no other edits.

### Tests for User Story 3

- [X] T018 [P] [US3] Add regression test in `tests/task_dashboard/test_queue_layouts.js` that pushes a temporary field into `queueFieldDefinitions` and asserts both `renderQueueTable` + `renderQueueCards` output the new label/value (DOC-REQ-002, DOC-REQ-008).

### Implementation for User Story 3

- [X] T019 [US3] Document `queueFieldDefinitions` extension rules inline in `api_service/static/task_dashboard/dashboard.js` (comments + exported helper) so future fields require a single edit (DOC-REQ-002).
- [X] T020 [P] [US3] Expose `queueFieldDefinitions`/`renderQueueFieldValue`/`renderQueueLayouts` via `window.__queueLayoutTest` (or ES module export) to support downstream tooling/tests when new fields are added (DOC-REQ-002, DOC-REQ-003).
- [X] T021 [US3] Write a walkthrough section in `docs/TaskUiQueue.md` that explains how to append a field, rebuild assets, and validate both layouts, keeping spec + docs synchronized (DOC-REQ-002, DOC-REQ-008).

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Finalize docs, bundle metrics, and full-test coverage across all stories.

- [X] T022 [P] Update `docs/TaskDashboardStyleSystem.md` with the new `.queue-layouts` / `.queue-card-*` classes, breakpoint contract, and "no feature flag" rollout note (DOC-REQ-006, DOC-REQ-008).
- [ ] T023 [P] Rebuild dashboard CSS via `npm run dashboard:css:min`, capture gzip size with `gzip -c api_service/static/task_dashboard/dashboard.css | wc -c`, and log before/after numbers plus <3 KB delta inside `docs/TaskDashboardStyleSystem.md` and `specs/037-queue-layout-switch/quickstart.md` (DOC-REQ-006, DOC-REQ-008).
- [ ] T024 [P] Run `./tools/test_unit.sh` after all code changes, attach the passing summary (command + date) to `specs/037-queue-layout-switch/quickstart.md`, and ensure CI-critical tests cover new helpers (DOC-REQ-008).
- [ ] T025 [P] Final pass to confirm `docs/TaskUiQueue.md`, `specs/037-queue-layout-switch/spec.md`, and implemented code remain aligned (metadata, breakpoints, no feature flag) before handoff (DOC-REQ-008).

---

## Dependencies & Execution Order

- **Setup (Phase 1)**: No dependencies; required before foundational work so fixtures + logs exist.
- **Foundational (Phase 2)**: Depends on Setup. Blocks all user stories because shared field definitions underpin every layout.
- **User Story 1 (Phase 3)**: Depends on Foundational. Once complete, delivers MVP mobile cards.
- **User Story 2 (Phase 4)**: Depends on Foundational (and practically on US1 wiring) but can run in parallel with late-stage US1 CSS if coordination avoids file conflicts.
- **User Story 3 (Phase 5)**: Depends on Foundational. Can proceed once shared definitions exist; mostly documentation + helper exposure.
- **Polish (Phase 6)**: Depends on all prior phases. Captures bundle/test/documentation alignment.

### User Story Dependencies

- **US1 (P1)**: Unlocks MVP mobile experience; independent once Foundational tasks land.
- **US2 (P2)**: Independent of US3 but should verify US1 does not regress desktop.
- **US3 (P3)**: Builds on US1/US2 foundations but primarily documentation + helper exports; can overlap late in cycle.

### Parallel Opportunities

- [ ] `T005` + `T006` (US1 tests) can run while `T007` implementation begins, provided fixtures from T001 exist.
- [ ] `T010` (CSS) and `T016` (desktop CSS adjustments) can be split between developers once class names are finalized.
- [ ] `T022`–`T025` polish tasks can run concurrently after code stabilizes because they touch different docs/scripts.

---

## Implementation Strategy

### MVP First (User Story 1)

1. Execute Setup + Foundational phases (T001–T004).
2. Complete US1 tasks (T005–T011) to ship mobile cards as a standalone increment.
3. Validate via unit tests + manual mobile QA before proceeding.

### Incremental Delivery

1. Deploy MVP (US1) once cards + shared layouts pass tests.
2. Layer US2 desktop safeguards (T012–T017) to ensure no regressions before launch.
3. Finish US3 extensibility work (T018–T021) and run Polish tasks (T022–T025) for rollout comms + metrics.

### Parallel Team Strategy

- Developer A focuses on JS renderers (T003–T009, T014–T015).
- Developer B handles Tailwind/CSS + docs (T010, T016, T022–T023).
- Developer C drives tests + QA artifacts (T001, T005–T006, T012–T018, T024).
