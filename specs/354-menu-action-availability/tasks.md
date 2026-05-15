# Tasks: Menu Action Availability and Unavailable Presentation

**Input**: Design documents from `/work/agent_jobs/mm:77a58456-73de-47fa-bf11-8f027af0e7c3/repo/specs/354-menu-action-availability/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/menu-action-availability-contract.md, quickstart.md

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement production code until they pass.

**Organization**: Tasks cover exactly one story: Menu Action Availability and Unavailable Presentation for THOR-403.

**Source Traceability**: Original Jira issue THOR-403 and preset brief are preserved in `spec.md` `**Input**`. All plan `## Requirement Status` rows are `missing`, so each FR requires test-and-code coverage. The THOR paths below are concrete proposed target paths and must be confirmed against the actual THOR repository during setup.

**Test Commands**:

- Unit tests: `Run the THOR repository's standard unit/automation test command for Source/ThorTactics/Tests/Frontend/MenuActionAvailabilityUnitTests.cpp`
- Integration tests: `Run the THOR repository's standard game automation command for Source/ThorTactics/Tests/Frontend/MenuActionAvailabilityFlowAutomationTest.cpp`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel because it touches different files and has no dependency on incomplete work
- Every task includes a concrete target file path for the THOR Tactics repository
- Requirement, scenario, success criterion, or contract IDs are included where behavior is validated or implemented

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish target file layout and test harness hooks before writing story tests.

- [ ] T001 Confirm or create frontend source directory for this story in Source/ThorTactics/Frontend/
- [ ] T002 Confirm or create frontend test directory for this story in Source/ThorTactics/Tests/Frontend/
- [ ] T003 [P] Confirm or create online/session test seam for blocked Online Co-op side-effect assertions in Source/ThorTactics/Online/
- [ ] T004 [P] Add menu action availability unit test file scaffold in Source/ThorTactics/Tests/Frontend/MenuActionAvailabilityUnitTests.cpp
- [ ] T005 [P] Add menu action availability flow automation test file scaffold in Source/ThorTactics/Tests/Frontend/MenuActionAvailabilityFlowAutomationTest.cpp
- [ ] T006 Document the THOR repository unit and integration test commands for this story in specs/354-menu-action-availability/quickstart.md

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Define the shared action availability model and generated-button extension points that story tests can target.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [ ] T007 Declare menu action entry and eligibility outcome types for enabled, disabled-visible, and hidden-by-window behavior covering FR-001 and FR-002 in Source/ThorTactics/Frontend/TacticsMenuActionTypes.h
- [ ] T008 Create empty implementation shell for menu action eligibility helpers covering FR-001 and FR-009 in Source/ThorTactics/Frontend/TacticsMenuActionTypes.cpp
- [ ] T009 [P] Declare generated menu button state/render model extension point covering FR-003, FR-004, and FR-005 in Source/ThorTactics/Frontend/TacticsGeneratedMenuButton.h
- [ ] T010 [P] Declare shared generated menu panel action resolution extension point covering FR-008 in Source/ThorTactics/Frontend/TacticsGeneratedMenuPanel.h
- [ ] T011 [P] Confirm Online Co-op travel/session side-effect adapter or spy seam for FR-007 in Source/ThorTactics/Online/TacticsOnlineCoopActions.cpp

**Checkpoint**: Foundation ready; red-first story tests can target concrete files.

---

## Phase 3: Story - Menu Action Availability and Unavailable Presentation

**Summary**: As a player, I want unavailable menu actions to remain visible with clear disabled messaging so I understand what exists and why it cannot currently be used.

**Independent Test**: Render generated menu buttons for enabled, disabled-visible, hidden-by-window, and blocked Online Co-op actions, then confirm visible unavailable actions show reasons and blocked selection does not trigger travel or session side effects.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, SCN-001, SCN-002, SCN-003, SCN-004, SCN-005, SCN-006, SC-001, SC-002, SC-003, SC-004, SC-005, UI contract `contracts/menu-action-availability-contract.md`

**Test Plan**:

- Unit: eligibility outcomes, unavailable reason source/fallback, generated button state, hidden precedence, and blocked-selection side-effect guards.
- Integration: generated menu rendering across Play, Home navigation, Options, future-panel fixture, and Online Co-op blocked feedback with no travel/session effects.

### Unit Tests (write first)

> Write these tests FIRST. Run them, confirm they FAIL for the expected reason, then implement only enough code to make them pass.

- [ ] T012 Add failing unit tests for enabled, disabled-visible, and hidden-by-window eligibility outcomes covering FR-001, SC-001, SC-002, and SC-003 in Source/ThorTactics/Tests/Frontend/MenuActionAvailabilityUnitTests.cpp
- [ ] T013 Add failing unit tests for authored unavailable reason, eligibility-produced reason, and fallback reason covering FR-002 and FR-009 in Source/ThorTactics/Tests/Frontend/MenuActionAvailabilityUnitTests.cpp
- [ ] T014 Add failing unit tests for generated button states for enabled, disabled-visible, and hidden actions covering FR-003, FR-004, and FR-005 in Source/ThorTactics/Tests/Frontend/MenuActionAvailabilityUnitTests.cpp
- [ ] T015 Add failing unit tests for hidden-by-window precedence when an action is also ineligible, action state changes between enabled and disabled-visible while a panel is open, and multiple disabled-visible actions on one panel covering FR-001, FR-004, SC-002, SC-003, and edge-case coverage in Source/ThorTactics/Tests/Frontend/MenuActionAvailabilityUnitTests.cpp
- [ ] T016 Add failing unit tests proving blocked Online Co-op selection does not call travel, matchmaking, session creation, or session joining seams across supported pointer, keyboard, and controller activation paths covering FR-007 and SC-004 in Source/ThorTactics/Tests/Frontend/MenuActionAvailabilityUnitTests.cpp

### Integration Tests (write first)

- [ ] T017 Add failing automation test for an enabled generated action rendering enabled and executing selection covering FR-005, FR-010, SCN-001, and SC-001 in Source/ThorTactics/Tests/Frontend/MenuActionAvailabilityFlowAutomationTest.cpp
- [ ] T018 Add failing automation test for disabled-visible action rendering with unavailable copy covering FR-003, FR-010, SCN-002, and SC-002 in Source/ThorTactics/Tests/Frontend/MenuActionAvailabilityFlowAutomationTest.cpp
- [ ] T019 Add failing automation test for hidden-by-window action omission covering FR-004, FR-010, SCN-003, and SC-003 in Source/ThorTactics/Tests/Frontend/MenuActionAvailabilityFlowAutomationTest.cpp
- [ ] T020 Add failing automation test for blocked Online Co-op remaining visible with explicit unavailable feedback covering FR-006, FR-010, SCN-004, and SC-004 in Source/ThorTactics/Tests/Frontend/MenuActionAvailabilityFlowAutomationTest.cpp
- [ ] T021 Add failing automation test for blocked Online Co-op selection producing feedback and zero travel/session side effects across supported keyboard/controller and pointer activation paths covering FR-007, FR-010, SCN-005, and SC-004 in Source/ThorTactics/Tests/Frontend/MenuActionAvailabilityFlowAutomationTest.cpp
- [ ] T022 Add failing cross-panel automation coverage for Play, Home navigation, Options, and one future-panel-compatible generated-button fixture covering FR-008, SCN-006, and SC-005 in Source/ThorTactics/Tests/Frontend/MenuActionAvailabilityFlowAutomationTest.cpp

### Red-First Confirmation

- [ ] T023 Run the THOR unit test command for Source/ThorTactics/Tests/Frontend/MenuActionAvailabilityUnitTests.cpp and confirm T012-T016 fail for missing menu action availability behavior
- [ ] T024 Run the THOR integration automation command for Source/ThorTactics/Tests/Frontend/MenuActionAvailabilityFlowAutomationTest.cpp and confirm T017-T022 fail for missing generated-button behavior

### Implementation

- [ ] T025 Implement enabled, disabled-visible, and hidden-by-window eligibility outcomes covering FR-001 in Source/ThorTactics/Frontend/TacticsMenuActionTypes.cpp
- [ ] T026 Implement unavailable reason source selection and deterministic fallback copy covering FR-002 and FR-009 in Source/ThorTactics/Frontend/TacticsMenuActionTypes.cpp
- [ ] T027 Implement generated button state mapping for enabled, disabled-visible, and hidden actions covering FR-003, FR-004, and FR-005 in Source/ThorTactics/Frontend/TacticsGeneratedMenuButton.cpp
- [ ] T028 Implement shared generated menu panel action filtering and button creation covering FR-008 in Source/ThorTactics/Frontend/TacticsGeneratedMenuPanel.cpp
- [ ] T029 Wire Play menu generated actions to the shared rendering path and keep blocked Online Co-op visible covering FR-006 and FR-008 in Source/ThorTactics/Frontend/TacticsPlayMenuPanel.cpp
- [ ] T030 Wire Home navigation generated actions to the shared rendering path covering FR-008 in Source/ThorTactics/Frontend/TacticsHomeMenuPanel.cpp
- [ ] T031 Wire Options generated actions to the shared rendering path covering FR-008 in Source/ThorTactics/Frontend/TacticsOptionsMenuPanel.cpp
- [ ] T032 Implement blocked Online Co-op selection feedback and side-effect guard covering FR-006 and FR-007 in Source/ThorTactics/Online/TacticsOnlineCoopActions.cpp
- [ ] T033 Run the THOR unit test command for Source/ThorTactics/Tests/Frontend/MenuActionAvailabilityUnitTests.cpp and fix failures in Source/ThorTactics/Frontend/TacticsMenuActionTypes.cpp, Source/ThorTactics/Frontend/TacticsGeneratedMenuButton.cpp, and Source/ThorTactics/Online/TacticsOnlineCoopActions.cpp
- [ ] T034 Run the THOR integration automation command for Source/ThorTactics/Tests/Frontend/MenuActionAvailabilityFlowAutomationTest.cpp and fix failures in Source/ThorTactics/Frontend/TacticsGeneratedMenuPanel.cpp, Source/ThorTactics/Frontend/TacticsPlayMenuPanel.cpp, Source/ThorTactics/Frontend/TacticsHomeMenuPanel.cpp, Source/ThorTactics/Frontend/TacticsOptionsMenuPanel.cpp, and Source/ThorTactics/Online/TacticsOnlineCoopActions.cpp

### Story Validation

- [ ] T035 Validate enabled, disabled-visible, hidden-by-window, and blocked Online Co-op flows against FR-001 through FR-010 and SC-001 through SC-005 in Source/ThorTactics/Tests/Frontend/MenuActionAvailabilityFlowAutomationTest.cpp

**Checkpoint**: Menu Action Availability and Unavailable Presentation is functional, covered by unit and integration tests, and independently testable.

---

## Phase 4: Polish & Verification

**Purpose**: Strengthen the completed story without adding unrelated menu, travel, matchmaking, or session scope.

- [ ] T036 [P] Review and remove any temporary debug-only reason text, test-only action IDs, or panel-specific bypasses from Source/ThorTactics/Frontend/TacticsGeneratedMenuButton.cpp
- [ ] T037 [P] Review unit edge-case coverage for missing unavailable reasons, state changes while a panel is open, multiple disabled-visible actions, supported input modalities, and hidden precedence in Source/ThorTactics/Tests/Frontend/MenuActionAvailabilityUnitTests.cpp
- [ ] T038 [P] Review integration coverage for Play, Home navigation, Options, and future-panel fixture behavior in Source/ThorTactics/Tests/Frontend/MenuActionAvailabilityFlowAutomationTest.cpp
- [ ] T039 Run the quickstart validation sequence from specs/354-menu-action-availability/quickstart.md
- [ ] T040 Run `/moonspec-verify` for specs/354-menu-action-availability/spec.md after implementation and tests pass

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies; can start immediately.
- **Foundational (Phase 2)**: Depends on Phase 1; blocks story test and implementation work.
- **Story (Phase 3)**: Depends on Phase 2; tests must be authored and confirmed failing before implementation.
- **Polish (Phase 4)**: Depends on the story implementation and tests passing.

### Within The Story

- T012-T016 unit tests must be written before T023.
- T017-T022 integration tests must be written before T024.
- T023 and T024 red-first confirmations must complete before T025-T032 implementation tasks.
- T025-T026 establish action eligibility and unavailable copy before T027-T028 render generated buttons.
- T029-T032 wire panel and Online Co-op behavior after shared generated button behavior exists.
- T033 and T034 must pass before T035 story validation.
- T040 final verification runs only after T039 quickstart validation.

### Parallel Opportunities

- T003-T005 can run in parallel after T001-T002.
- T009-T011 can run in parallel after T007-T008.
- T012-T016 should be authored serially because they modify the same unit test file.
- T017-T022 should be authored serially because they modify the same automation test file.
- T036-T038 can run in parallel after story validation.

---

## Parallel Example: Story Phase

```bash
# Unit and integration test authoring can be split by file:
Task: "Add failing unit tests for eligibility outcomes and unavailable reasons in Source/ThorTactics/Tests/Frontend/MenuActionAvailabilityUnitTests.cpp"
Task: "Add failing automation tests for generated button visibility and Online Co-op blocked selection in Source/ThorTactics/Tests/Frontend/MenuActionAvailabilityFlowAutomationTest.cpp"

# After red-first confirmation, shared model and panel wiring can be split carefully:
Task: "Implement eligibility outcomes and unavailable reason resolution in Source/ThorTactics/Frontend/TacticsMenuActionTypes.cpp"
Task: "Implement generated button rendering and shared panel filtering in Source/ThorTactics/Frontend/TacticsGeneratedMenuButton.cpp and Source/ThorTactics/Frontend/TacticsGeneratedMenuPanel.cpp"
```

---

## Implementation Strategy

### Test-Driven Story Delivery

1. Complete Phase 1 and Phase 2 to establish the target files and side-effect test seams.
2. Write all unit and integration tests first.
3. Run the targeted unit and integration commands and confirm the tests fail for missing behavior.
4. Implement eligibility outcomes, unavailable reason resolution, generated button rendering, shared panel filtering, Online Co-op blocked feedback, and side-effect guards.
5. Re-run unit tests until all unit behavior passes.
6. Re-run integration automation until generated panel behavior and Online Co-op blocked selection pass.
7. Run quickstart validation and final `/moonspec-verify`.

### Requirement Status Coverage

- Code-and-test work: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, SC-001, SC-002, SC-003, SC-004, SC-005.
- Verification-only work: none.
- Conditional fallback work: none.
- Already verified work: none.

## Notes

- This task list targets the THOR Tactics repository. The current managed workspace is MoonMind and does not contain the target game files.
- Preserve THOR-403 traceability through final verification.
- Do not add unrelated menu panels, settings persistence, matchmaking behavior, travel behavior, or session lifecycle changes beyond blocking side effects while unavailable.
