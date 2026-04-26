# Tasks: Grid UI Marker Baseline

**Input**: Design documents from `/specs/267-grid-ui-marker-baseline/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks cover exactly one story: `Baseline Grid UI Marker Lifecycle`.

**Source Traceability**: `MM-525`, FR-001 through FR-007, SCN-001 through SCN-005, SC-001 through SC-006, and DESIGN-REQ-001 through DESIGN-REQ-007.

**Test Commands**:

- Unit tests: target Tactics frontend unit/controller test command for Grid UI marker lifecycle tests
- Integration tests: target Tactics frontend integration or controller-level lifecycle/diagnostic test command
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel when paths differ and dependencies are complete
- Each task includes a target path. `TARGET_PROJECT_ROOT` means the root of the Tactics frontend repository/workspace containing `Docs/TacticsFrontend/GridUiOverlaySystem.md`; this root is not present in the current MoonMind checkout.

## Phase 1: Setup

**Purpose**: Establish the target runtime workspace and preserve traceability before any story implementation.

- [ ] T001 Verify `TARGET_PROJECT_ROOT/Docs/TacticsFrontend/GridUiOverlaySystem.md` exists and records source sections 1, 4, 18, 20, and 21 for MM-525. (DESIGN-REQ-001, DESIGN-REQ-007)
- [ ] T002 Verify `TARGET_PROJECT_ROOT` contains Grid UI marker/decal runtime source with the named mutation APIs before editing implementation files. (FR-001, SC-001)
- [ ] T003 Verify target unit/controller test command for Grid UI marker lifecycle tests and record it in `specs/267-grid-ui-marker-baseline/quickstart.md`. (FR-003, FR-004)
- [ ] T004 Verify target integration or controller-level diagnostic/lifecycle test command and record it in `specs/267-grid-ui-marker-baseline/quickstart.md`. (FR-004, FR-005)
- [ ] T005 Preserve `MM-525` and the canonical Jira preset brief in any target implementation notes or delivery metadata. (FR-007, SC-006)

---

## Phase 2: Foundational

**Purpose**: Build the inventory and test harness prerequisites that block story implementation.

**CRITICAL**: No story implementation work can begin until T001 through T009 are complete.

- [ ] T006 Run `rg -w -n "SpawnTileMarkers|SpawnTileMarkersFromIndexes|QueueSpawnTileMarkers|QueueSpawnTileMarkersFromIndexes|ClearTileMarkers|ClearAllTileMarkers|SpawnDecalsAtLocations|ClearSpecifiedDecals" TARGET_PROJECT_ROOT` and save the complete result set in `TARGET_PROJECT_ROOT/Docs/TacticsFrontend/GridUiMarkerMutationInventory.md`. (FR-001, DESIGN-REQ-002)
- [ ] T007 Create the checked-in mutation inventory table in `TARGET_PROJECT_ROOT/Docs/TacticsFrontend/GridUiMarkerMutationInventory.md` with source path, source location, invoked API, marker/decal type, operation category, producer role, and notes. (FR-001, FR-002, SC-001, SC-002)
- [ ] T008 Classify every inventory row in `TARGET_PROJECT_ROOT/Docs/TacticsFrontend/GridUiMarkerMutationInventory.md` with exactly one role from selected movement, hover movement, attack targeting, ability preview, focus/selection, path/ghost path, phase clear, teardown clear, or debug/demo utility. (FR-002, DESIGN-REQ-003)
- [ ] T009 Identify or create target test fixture locations for Movement overlay producers, lifecycle/idempotence behavior, and diagnostic evidence in `TARGET_PROJECT_ROOT/Tests/GridUi/`. (FR-003, FR-004, FR-005)

**Checkpoint**: Inventory and target test harness locations are ready; red-first story tests can begin.

---

## Phase 3: Story - Baseline Grid UI Marker Lifecycle

**Summary**: As a tactics frontend maintainer, I want the current Grid UI marker and decal mutation lifecycle inventoried and regression-covered so that later ownership changes can be made against a known, observable baseline.

**Independent Test**: Review the checked-in inventory and run target unit/controller plus integration diagnostics tests to prove call-site coverage, producer classifications, Movement overlay interference coverage, lifecycle/idempotence preservation, and diagnostic event detail.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, SCN-001, SCN-002, SCN-003, SCN-004, SCN-005, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-007.

**Test Plan**:

- Unit: inventory completeness validation, producer-role validation, Movement overlay interference regression, diagnostic event field validation.
- Integration: lifecycle/idempotence preservation and producer-vs-renderer diagnostic evidence across the target Grid UI marker lifecycle.

### Unit Tests (write first)

- [ ] T010 [P] Add failing unit/controller test for inventory completeness against direct named API uses in `TARGET_PROJECT_ROOT/Tests/GridUi/GridUiMarkerInventoryTests.*`. (FR-001, SCN-001, SC-001, DESIGN-REQ-002)
- [ ] T011 [P] Add failing unit/controller test for exactly-one producer role per inventory row in `TARGET_PROJECT_ROOT/Tests/GridUi/GridUiMarkerInventoryTests.*`. (FR-002, SCN-002, SC-002, DESIGN-REQ-003)
- [ ] T012 [P] Add failing unit/controller regression test for two Movement overlay producers where clearing one producer can erase another producer's overlay in `TARGET_PROJECT_ROOT/Tests/GridUi/GridUiMovementOverlayInterferenceTests.*`. (FR-003, SCN-003, SC-003, DESIGN-REQ-004)
- [ ] T013 [P] Add failing unit/controller test for diagnostic event fields in `TARGET_PROJECT_ROOT/Tests/GridUi/GridUiMarkerDiagnosticsTests.*`. (FR-005, SCN-005, SC-005, DESIGN-REQ-006)
- [ ] T014 Run the target unit/controller test command for T010 through T013 and confirm each new test fails for the expected baseline gap before production or inventory implementation. (FR-001, FR-002, FR-003, FR-005)

### Integration Tests (write first)

- [ ] T015 [P] Add failing integration or controller-level lifecycle/idempotence test in `TARGET_PROJECT_ROOT/Tests/GridUi/GridUiMarkerLifecycleIntegrationTests.*`. (FR-004, SCN-004, SC-004, DESIGN-REQ-005)
- [ ] T016 [P] Add failing integration or controller-level diagnostic churn test that distinguishes producer churn from renderer churn in `TARGET_PROJECT_ROOT/Tests/GridUi/GridUiMarkerDiagnosticsIntegrationTests.*`. (FR-005, SCN-005, SC-005, DESIGN-REQ-006)
- [ ] T017 Run the target integration test command for T015 and T016 and confirm each new test fails for the expected baseline gap before implementation. (FR-004, FR-005)

### Implementation

- [ ] T018 Complete the source-location inventory in `TARGET_PROJECT_ROOT/Docs/TacticsFrontend/GridUiMarkerMutationInventory.md` until T010 passes. (FR-001, SC-001, DESIGN-REQ-002)
- [ ] T019 Complete producer-role classifications in `TARGET_PROJECT_ROOT/Docs/TacticsFrontend/GridUiMarkerMutationInventory.md` until T011 passes. (FR-002, SC-002, DESIGN-REQ-003)
- [ ] T020 Add the minimum baseline test fixtures or instrumentation in the target Grid UI marker runtime source under `TARGET_PROJECT_ROOT/Source/` needed for T012 without changing ownership semantics. (FR-003, FR-006, DESIGN-REQ-004, DESIGN-REQ-007)
- [ ] T021 Preserve or update equivalent lifecycle/idempotence assertions in `TARGET_PROJECT_ROOT/Tests/GridUi/GridUiMarkerLifecycleIntegrationTests.*` until T015 passes. (FR-004, DESIGN-REQ-005)
- [ ] T022 Add or expose diagnostic evidence fields in the target Grid UI marker diagnostics path under `TARGET_PROJECT_ROOT/Source/` until T013 and T016 pass. (FR-005, DESIGN-REQ-006)
- [ ] T023 Run the target unit/controller test command and fix failures without expanding scope beyond MM-525. (FR-001, FR-002, FR-003, FR-005, FR-006)
- [ ] T024 Run the target integration test command and fix failures without expanding scope beyond MM-525. (FR-004, FR-005, FR-006)

**Checkpoint**: The story is functional in the target project, red-first tests now pass, and ownership semantics have not been migrated.

---

## Phase 4: Story Validation And Polish

**Purpose**: Validate the completed single story and preserve delivery traceability.

- [ ] T025 Run the quickstart validation in `specs/267-grid-ui-marker-baseline/quickstart.md` against `TARGET_PROJECT_ROOT`. (SC-001, SC-002, SC-003, SC-004, SC-005)
- [ ] T026 Verify `TARGET_PROJECT_ROOT/Docs/TacticsFrontend/GridUiMarkerMutationInventory.md` covers 100% of named direct API uses reported by `rg`. (FR-001, SC-001)
- [ ] T027 Verify all final target test outputs preserve or reference `MM-525` where project conventions support traceability. (FR-007, SC-006)
- [ ] T028 Run `/moonspec-verify` for `specs/267-grid-ui-marker-baseline/spec.md` and record the final verdict. (FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: Must confirm the target Tactics frontend workspace exists.
- **Foundational (Phase 2)**: Depends on Setup and blocks story work.
- **Story (Phase 3)**: Depends on Foundational and must follow red-first testing.
- **Validation And Polish (Phase 4)**: Depends on story implementation and passing target tests.

### Within The Story

- T010 through T013 must be written and fail before T018 through T022.
- T015 and T016 must be written and fail before T021 and T022.
- T018 and T019 depend on T006 through T008.
- T020 depends on T012 and must not migrate ownership semantics.
- T022 depends on `contracts/diagnostic-evidence.md`.
- T028 is last.

### Parallel Opportunities

- T003 and T004 can run in parallel after T001 and T002.
- T010, T011, T012, and T013 can be authored in parallel because they target distinct test concerns.
- T015 and T016 can be authored in parallel.
- T018 and T019 can run in parallel after inventory source locations are stable.

## Parallel Example: Story Phase

```text
Task: "Add failing unit/controller test for inventory completeness in TARGET_PROJECT_ROOT/Tests/GridUi/GridUiMarkerInventoryTests.*"
Task: "Add failing unit/controller regression test for Movement overlay interference in TARGET_PROJECT_ROOT/Tests/GridUi/GridUiMovementOverlayInterferenceTests.*"
Task: "Add failing integration diagnostic churn test in TARGET_PROJECT_ROOT/Tests/GridUi/GridUiMarkerDiagnosticsIntegrationTests.*"
```

## Implementation Strategy

1. Stop immediately if `TARGET_PROJECT_ROOT` is unavailable; the current MoonMind checkout does not contain the target runtime source.
2. Complete setup and foundational inventory tasks.
3. Write red-first unit/controller tests and confirm expected failures.
4. Write red-first integration/controller tests and confirm expected failures.
5. Complete the inventory, classifications, baseline fixtures, and diagnostics until tests pass.
6. Run target unit and integration suites.
7. Run quickstart validation.
8. Run `/moonspec-verify`.

## Notes

- This task list covers one story only.
- Do not run `moonspec-breakdown`; `MM-525` is already a single-story runtime request.
- Do not change Grid UI marker ownership semantics in this story.
- Do not map target runtime implementation onto the MoonMind repository; use the Tactics frontend workspace that contains the named APIs.
