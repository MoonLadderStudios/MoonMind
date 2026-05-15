# Tasks: Frontend Input and Focus Contract

**Input**: Design documents from ./
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/frontend-input-focus-contract.md, quickstart.md

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement production code until they pass.

**Organization**: Tasks cover exactly one story: Frontend Input and Focus Contract for THOR-404.

**Source Traceability**: Original Jira issue THOR-404 and preset brief are preserved in `spec.md` `**Input**`. All plan `## Requirement Status` rows are `missing`, so each FR requires test-and-code coverage. The task phases reflect the explicit unit and integration coverage in `plan.md` `## Test Strategy`. The THOR paths below are concrete proposed target paths and must be confirmed against the actual THOR repository during setup.

**Test Commands**:

- Unit tests: `Run the THOR repository's standard unit/automation test command for Source/ThorTactics/Tests/Frontend/MenuInputFocusContractUnitTests.cpp`
- Integration tests: `Run the THOR repository's standard game automation command for Source/ThorTactics/Tests/Frontend/MenuInputFocusContractFlowAutomationTest.cpp`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel because it touches different files and has no dependency on incomplete work
- Every task includes a concrete target file path for the THOR Tactics repository
- Requirement, scenario, success criterion, or contract IDs are included where behavior is validated or implemented

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish target file layout and test harness hooks before writing story tests.

- [ ] T001 Confirm or create frontend source directory for this story in Source/ThorTactics/Frontend/
- [ ] T002 Confirm or create frontend test directory for this story in Source/ThorTactics/Tests/Frontend/
- [ ] T003 [P] Confirm or create menu coordinator test seam for activation parity assertions in Source/ThorTactics/Frontend/TacticsMenuCoordinator.cpp
- [ ] T004 [P] Add menu input/focus unit test file scaffold in Source/ThorTactics/Tests/Frontend/MenuInputFocusContractUnitTests.cpp
- [ ] T005 [P] Add menu input/focus flow automation test file scaffold in Source/ThorTactics/Tests/Frontend/MenuInputFocusContractFlowAutomationTest.cpp
- [ ] T006 Document the THOR repository unit and integration test commands for this story in specs/355-frontend-input-focus-contract/quickstart.md

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Define the shared menu input/focus extension points that story tests can target.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [ ] T007 Declare shared menu input contract defaults covering FR-001 in Source/ThorTactics/Frontend/TacticsMenuInputConfig.h
- [ ] T008 Create empty implementation shell for shared input contract defaults covering FR-001 in Source/ThorTactics/Frontend/TacticsMenuInputConfig.cpp
- [ ] T009 [P] Declare menu screen base activation and initial focus extension points covering FR-001, FR-003, and FR-005 in Source/ThorTactics/Frontend/TacticsMenuScreenBase.h
- [ ] T010 [P] Declare menu panel base activation and Back/Cancel extension points covering FR-001, FR-003, and FR-005 in Source/ThorTactics/Frontend/TacticsMenuPanelBase.h
- [ ] T011 [P] Declare generated action button focus and activation forwarding extension points covering FR-002 and FR-004 in Source/ThorTactics/Frontend/TacticsGeneratedMenuButton.h
- [ ] T012 [P] Declare focus return target storage and fallback selection extension points covering FR-006, FR-007, and FR-008 in Source/ThorTactics/Frontend/TacticsMenuCoordinator.h

**Checkpoint**: Foundation ready; red-first story tests can target concrete files.

---

## Phase 3: Story - Frontend Input and Focus Contract

**Summary**: As a controller or keyboard user, I want frontend menus to handle confirm, cancel/back, pointer, keyboard, and gamepad input consistently so fallback menu surfaces remain fully navigable.

**Independent Test**: Open Home, Play, and Options with native fallback widgets; confirm initial focus, activation parity, Back/Cancel behavior, and Home focus restoration with mouse, keyboard, and controller inputs.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, SCN-001, SCN-002, SCN-003, SCN-004, SCN-005, SCN-006, SCN-007, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, UI contract `contracts/frontend-input-focus-contract.md`

**Test Plan**:

- Unit: shared input defaults, generated button focusability, initial focus target selection, activation routing parity, previous-state return, and fallback focus target selection.
- Integration: native fallback Home, Play, and Options flows for initial focus, pointer/keyboard/controller confirm activation, Back/Cancel, Play-to-Home focus restoration, and Options-to-Home focus restoration.

### Unit Tests (write first)

> Write these tests FIRST. Run them, confirm they FAIL for the expected reason, then implement only enough code to make them pass.

- [ ] T013 Add failing unit tests for shared default confirm and cancel/back input behavior covering FR-001 in Source/ThorTactics/Tests/Frontend/MenuInputFocusContractUnitTests.cpp
- [ ] T014 Add failing unit tests for generated action button focusability covering FR-002 in Source/ThorTactics/Tests/Frontend/MenuInputFocusContractUnitTests.cpp
- [ ] T015 Add failing unit tests for initial focus target selection with one action, multiple actions, and no valid action covering FR-003 and SC-001 in Source/ThorTactics/Tests/Frontend/MenuInputFocusContractUnitTests.cpp
- [ ] T016 Add failing unit tests proving mouse click, keyboard confirm, and controller confirm forward to the same coordinator action covering FR-004 and SC-002 in Source/ThorTactics/Tests/Frontend/MenuInputFocusContractUnitTests.cpp
- [ ] T017 Add failing unit tests for Back/Cancel previous-state handling and root-surface valid-focus behavior covering FR-005 and SC-003 in Source/ThorTactics/Tests/Frontend/MenuInputFocusContractUnitTests.cpp
- [ ] T018 Add failing unit tests for Play and Options focus return target selection plus unavailable-target fallback behavior covering FR-006, FR-007, FR-008, SC-004, and SC-005 in Source/ThorTactics/Tests/Frontend/MenuInputFocusContractUnitTests.cpp

### Integration Tests (write first)

- [ ] T019 Add failing automation test for Home native fallback initial focus covering FR-003, FR-009, FR-010, SCN-001, SC-001, and SC-006 in Source/ThorTactics/Tests/Frontend/MenuInputFocusContractFlowAutomationTest.cpp
- [ ] T020 Add failing automation test for generated action activation parity across mouse click, keyboard confirm, and controller confirm covering FR-004, FR-010, SCN-003, SC-002, and SC-006 in Source/ThorTactics/Tests/Frontend/MenuInputFocusContractFlowAutomationTest.cpp
- [ ] T021 Add failing automation test for Back/Cancel returning from Play to Home and restoring Play focus covering FR-005, FR-006, FR-010, SCN-004, SCN-005, SC-003, SC-004, and SC-006 in Source/ThorTactics/Tests/Frontend/MenuInputFocusContractFlowAutomationTest.cpp
- [ ] T022 Add failing automation test for Back/Cancel returning from Options to Home and restoring Options focus covering FR-005, FR-007, FR-010, SCN-004, SCN-006, SC-003, SC-005, and SC-006 in Source/ThorTactics/Tests/Frontend/MenuInputFocusContractFlowAutomationTest.cpp
- [ ] T023 Add failing automation test for fallback focus selection when a previous Home return target is unavailable covering FR-008 and FR-010 in Source/ThorTactics/Tests/Frontend/MenuInputFocusContractFlowAutomationTest.cpp
- [ ] T024 Add failing automation test with authored presentation assets absent proving native fallback widgets satisfy initial focus, confirm activation, cancel/back, and focus restoration covering FR-009, FR-010, SCN-007, and SC-006 in Source/ThorTactics/Tests/Frontend/MenuInputFocusContractFlowAutomationTest.cpp

### Red-First Confirmation

- [ ] T025 Run the THOR unit test command for Source/ThorTactics/Tests/Frontend/MenuInputFocusContractUnitTests.cpp and confirm T013-T018 fail for missing menu input/focus contract behavior
- [ ] T026 Run the THOR integration automation command for Source/ThorTactics/Tests/Frontend/MenuInputFocusContractFlowAutomationTest.cpp and confirm T019-T024 fail for missing native fallback input/focus behavior

### Implementation

- [ ] T027 Implement shared default confirm and cancel/back input behavior covering FR-001 in Source/ThorTactics/Frontend/TacticsMenuInputConfig.cpp
- [ ] T028 Implement active screen input setup, initial focus assignment, and root Back/Cancel valid-focus behavior covering FR-001, FR-003, and FR-005 in Source/ThorTactics/Frontend/TacticsMenuScreenBase.cpp
- [ ] T029 Implement active panel input setup, initial focus assignment, child Back/Cancel dismissal, and previous-state return behavior covering FR-001, FR-003, and FR-005 in Source/ThorTactics/Frontend/TacticsMenuPanelBase.cpp
- [ ] T030 Implement generated action button focusability and activation forwarding covering FR-002 and FR-004 in Source/ThorTactics/Frontend/TacticsGeneratedMenuButton.cpp
- [ ] T031 Implement menu coordinator activation routing, focus return target storage, Play/Options restoration, and unavailable-target fallback covering FR-004, FR-006, FR-007, and FR-008 in Source/ThorTactics/Frontend/TacticsMenuCoordinator.cpp
- [ ] T032 Wire Home generated navigation actions into focus return targets covering FR-006 and FR-007 in Source/ThorTactics/Frontend/TacticsHomeMenuPanel.cpp
- [ ] T033 Wire Play native fallback surface activation and Back/Cancel return behavior covering FR-005, FR-006, and FR-009 in Source/ThorTactics/Frontend/TacticsPlayMenuPanel.cpp
- [ ] T034 Wire Options native fallback surface activation and Back/Cancel return behavior covering FR-005, FR-007, and FR-009 in Source/ThorTactics/Frontend/TacticsOptionsMenuPanel.cpp
- [ ] T035 Run the THOR unit test command for Source/ThorTactics/Tests/Frontend/MenuInputFocusContractUnitTests.cpp and fix failures in Source/ThorTactics/Frontend/TacticsMenuInputConfig.cpp, Source/ThorTactics/Frontend/TacticsMenuScreenBase.cpp, Source/ThorTactics/Frontend/TacticsMenuPanelBase.cpp, Source/ThorTactics/Frontend/TacticsGeneratedMenuButton.cpp, and Source/ThorTactics/Frontend/TacticsMenuCoordinator.cpp
- [ ] T036 Run the THOR integration automation command for Source/ThorTactics/Tests/Frontend/MenuInputFocusContractFlowAutomationTest.cpp and fix failures in Source/ThorTactics/Frontend/TacticsHomeMenuPanel.cpp, Source/ThorTactics/Frontend/TacticsPlayMenuPanel.cpp, Source/ThorTactics/Frontend/TacticsOptionsMenuPanel.cpp, and shared frontend menu modules

### Story Validation

- [ ] T037 Validate initial focus, confirm activation parity, Back/Cancel, focus restoration, and native fallback widgets against FR-001 through FR-010 and SC-001 through SC-006 in Source/ThorTactics/Tests/Frontend/MenuInputFocusContractFlowAutomationTest.cpp

**Checkpoint**: Frontend Input and Focus Contract is functional, covered by unit and integration tests, and independently testable.

---

## Phase 4: Polish & Verification

**Purpose**: Strengthen the completed story without adding unrelated menu, settings persistence, travel, matchmaking, or session scope.

- [ ] T038 [P] Review and remove any temporary debug-only focus targets, input bypasses, or panel-specific activation shortcuts from Source/ThorTactics/Frontend/TacticsMenuCoordinator.cpp
- [ ] T039 [P] Review unit edge-case coverage for no valid focus target, unavailable return target, rapid pointer/keyboard/controller changes, and root Back/Cancel behavior in Source/ThorTactics/Tests/Frontend/MenuInputFocusContractUnitTests.cpp
- [ ] T040 [P] Review integration coverage for Home, Play, Options, and authored-assets-absent native fallback flows in Source/ThorTactics/Tests/Frontend/MenuInputFocusContractFlowAutomationTest.cpp
- [ ] T041 Run the quickstart validation sequence from specs/355-frontend-input-focus-contract/quickstart.md
- [ ] T042 Run `/moonspec-verify` for specs/355-frontend-input-focus-contract/spec.md after implementation and tests pass

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies; can start immediately.
- **Foundational (Phase 2)**: Depends on Phase 1; blocks story test and implementation work.
- **Story (Phase 3)**: Depends on Phase 2; tests must be authored and confirmed failing before implementation.
- **Polish (Phase 4)**: Depends on the story implementation and tests passing.

### Within The Story

- T013-T018 unit tests must be written before T025.
- T019-T024 integration tests must be written before T026.
- T025 and T026 red-first confirmations must complete before T027-T034 implementation tasks.
- T027 establishes shared input defaults before T028-T029 surface activation work.
- T030-T031 establish activation and focus return behavior before T032-T034 panel wiring.
- T035 and T036 must pass before T037 story validation.
- T042 final verification runs only after T041 quickstart validation.

### Parallel Opportunities

- T003-T005 can run in parallel after T001-T002.
- T009-T012 can run in parallel after T007-T008.
- T013-T018 should be authored serially because they modify the same unit test file.
- T019-T024 should be authored serially because they modify the same automation test file.
- T038-T040 can run in parallel after story validation.

---

## Parallel Example: Story Phase

```bash
# Unit and integration test authoring can be split by file:
Task: "Add failing unit tests for input defaults, focus selection, and activation parity in Source/ThorTactics/Tests/Frontend/MenuInputFocusContractUnitTests.cpp"
Task: "Add failing automation tests for native fallback Home, Play, and Options flows in Source/ThorTactics/Tests/Frontend/MenuInputFocusContractFlowAutomationTest.cpp"

# After red-first confirmation, shared model and panel wiring can be split carefully:
Task: "Implement shared input defaults and focus selection in Source/ThorTactics/Frontend/TacticsMenuInputConfig.cpp, TacticsMenuScreenBase.cpp, and TacticsMenuPanelBase.cpp"
Task: "Implement generated button activation forwarding and coordinator focus restoration in Source/ThorTactics/Frontend/TacticsGeneratedMenuButton.cpp and TacticsMenuCoordinator.cpp"
```

---

## Implementation Strategy

### Test-Driven Story Delivery

1. Complete Phase 1 and Phase 2 to establish the target files and coordinator/focus test seams.
2. Write all unit and integration tests first.
3. Run the targeted unit and integration commands and confirm the tests fail for missing behavior.
4. Implement shared input defaults, generated button focusability, initial focus, activation parity, Back/Cancel, focus restoration, and native fallback widget behavior.
5. Re-run unit tests until all unit behavior passes.
6. Re-run integration automation until Home, Play, Options, and native fallback flows pass.
7. Run quickstart validation and final `/moonspec-verify`.

### Requirement Status Coverage

- Code-and-test work: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006.
- Verification-only work: none.
- Conditional fallback work: none.
- Already verified work: none.

## Notes

- This task list targets the THOR Tactics repository. The current managed workspace is MoonMind and does not contain the target game files.
- Preserve THOR-404 traceability through final verification.
- Do not add unrelated menu panels, settings persistence, matchmaking behavior, travel behavior, or session lifecycle changes beyond the frontend input/focus contract.
