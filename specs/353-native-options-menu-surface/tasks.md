# Tasks: Native Options Menu Surface

**Input**: Design documents from `/work/agent_jobs/mm:0b69a150-35ed-4afe-bcc4-511f6d02cb47/repo/specs/353-native-options-menu-surface/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/options-menu-ui-contract.md, quickstart.md

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement production code until they pass.

**Organization**: Tasks cover exactly one story: Native Options Menu Surface for THOR-402.

**Source Traceability**: Original Jira issue THOR-402 and preset brief are preserved in `spec.md` `**Input**`. All plan `## Requirement Status` rows are `missing`, so each FR requires test-and-code coverage. The THOR paths below are concrete proposed target paths and must be confirmed against the actual THOR repository during setup.

**Test Commands**:

- Unit tests: `Run the THOR repository's standard unit/automation test command for Source/ThorTactics/Tests/Frontend/OptionsMenuUnitTests.cpp`
- Integration tests: `Run the THOR repository's standard game automation command for Source/ThorTactics/Tests/Frontend/OptionsMenuFlowAutomationTest.cpp`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel because it touches different files and has no dependency on incomplete work
- Every task includes a concrete target file path for the THOR Tactics repository
- Requirement, scenario, success criterion, or contract IDs are included where behavior is validated or implemented

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish the target file layout and test harness hooks before writing story tests.

- [ ] T001 Confirm or create frontend source directory for this story in Source/ThorTactics/Frontend/
- [ ] T002 Confirm or create frontend test directory for this story in Source/ThorTactics/Tests/Frontend/
- [ ] T003 [P] Add Options menu unit test file scaffold in Source/ThorTactics/Tests/Frontend/OptionsMenuUnitTests.cpp
- [ ] T004 [P] Add Options menu flow automation test file scaffold in Source/ThorTactics/Tests/Frontend/OptionsMenuFlowAutomationTest.cpp
- [ ] T005 Document the THOR repository unit and integration test commands for this story in specs/353-native-options-menu-surface/quickstart.md

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Define the minimal shared menu identifiers and data shapes that story tests can target.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [ ] T006 Create Options menu type declarations for OptionsNavigationAction, OptionsSurfaceState, and OptionsCategory covering FR-001, FR-002, FR-008 in Source/ThorTactics/Frontend/ThorOptionsMenuTypes.h
- [ ] T007 Create empty implementation shell for Options menu type helpers in Source/ThorTactics/Frontend/ThorOptionsMenuTypes.cpp
- [ ] T008 [P] Declare Home menu Options action extension point for FR-001 and FR-007 in Source/ThorTactics/Frontend/ThorHomeMenuWidget.h
- [ ] T009 [P] Declare baseline Options surface extension point for FR-003, FR-004, FR-005, FR-006 in Source/ThorTactics/Frontend/ThorOptionsMenuWidget.h

**Checkpoint**: Foundation ready; red-first story tests can target concrete files.

---

## Phase 3: Story - Native Options Menu Surface

**Summary**: As a player, I want an Options menu surface reachable from Home so settings categories remain available before the final presentation layer is authored.

**Independent Test**: Navigate Home -> Options -> Back using only the baseline surface, confirm Video/Audio/Input category actions render, and confirm focus returns to the Home Options action.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, SCN-001, SCN-002, SCN-003, SCN-004, SCN-005, SC-001, SC-002, SC-003, SC-004, UI contract `contracts/options-menu-ui-contract.md`

**Test Plan**:

- Unit: stable identifiers, authored/fallback category resolution, no-persistence boundary, Back/Cancel state, and return focus target.
- Integration: Home -> Options -> Back/Cancel player-visible flow with authored Options presentation assets and authored category data absent.

### Unit Tests (write first)

> Write these tests FIRST. Run them, confirm they FAIL for the expected reason, then implement only enough code to make them pass.

- [ ] T010 Add failing unit tests for stable identifiers `frontend.nav.options`, `frontend.options.video`, `frontend.options.audio`, and `frontend.options.input` covering FR-001, FR-002, SC-002 in Source/ThorTactics/Tests/Frontend/OptionsMenuUnitTests.cpp
- [ ] T011 Add failing unit tests for authored category data rendering and required category preservation covering FR-003, SCN-002 in Source/ThorTactics/Tests/Frontend/OptionsMenuUnitTests.cpp
- [ ] T012 Add failing unit tests for empty and partial authored data fallback category entries covering FR-004, SCN-003, SC-003 in Source/ThorTactics/Tests/Frontend/OptionsMenuUnitTests.cpp
- [ ] T013 Add failing unit tests for Back and Cancel state transitions covering FR-006, SCN-004 in Source/ThorTactics/Tests/Frontend/OptionsMenuUnitTests.cpp
- [ ] T014 Add failing unit tests for restoring focus to the Home Options action, including repeated enter/exit state, covering FR-007, SC-004 and repeated-entry edge case in Source/ThorTactics/Tests/Frontend/OptionsMenuUnitTests.cpp
- [ ] T015 Add failing unit test that category actions do not require saved settings state covering FR-008, SCN-005 in Source/ThorTactics/Tests/Frontend/OptionsMenuUnitTests.cpp

### Integration Tests (write first)

- [ ] T016 Add failing automation test for Home activating `frontend.nav.options` and opening Options covering FR-001, FR-005, SCN-001, SC-001 in Source/ThorTactics/Tests/Frontend/OptionsMenuFlowAutomationTest.cpp
- [ ] T017 Add failing automation test that Video, Audio, and Input category actions are visible without authored category data covering FR-002, FR-004, FR-005, SCN-003, SC-002, SC-003 in Source/ThorTactics/Tests/Frontend/OptionsMenuFlowAutomationTest.cpp
- [ ] T018 Add failing automation test for Back returning to Home and restoring focus covering FR-006, FR-007, SCN-004, SC-004 in Source/ThorTactics/Tests/Frontend/OptionsMenuFlowAutomationTest.cpp
- [ ] T019 Add failing automation test for Cancel returning to Home and restoring focus covering FR-006, FR-007, SCN-004, SC-004 in Source/ThorTactics/Tests/Frontend/OptionsMenuFlowAutomationTest.cpp
- [ ] T020 Add contract-focused automation assertions and repeated Home -> Options -> Back coverage for `contracts/options-menu-ui-contract.md` covering FR-009 and repeated-entry edge case in Source/ThorTactics/Tests/Frontend/OptionsMenuFlowAutomationTest.cpp

### Red-First Confirmation

- [ ] T021 Run the THOR unit test command for Source/ThorTactics/Tests/Frontend/OptionsMenuUnitTests.cpp and confirm T010-T015 fail for missing Options menu behavior
- [ ] T022 Run the THOR integration automation command for Source/ThorTactics/Tests/Frontend/OptionsMenuFlowAutomationTest.cpp and confirm T016-T020 fail for missing Home -> Options -> Back behavior

### Implementation

- [ ] T023 Implement stable Options navigation and category identifiers covering FR-001, FR-002 in Source/ThorTactics/Frontend/ThorOptionsMenuTypes.cpp
- [ ] T024 Implement authored category normalization and required baseline category preservation covering FR-003, FR-002 in Source/ThorTactics/Frontend/ThorOptionsMenuTypes.cpp
- [ ] T025 Implement fallback Video, Audio, and Input category entries for missing or partial authored data covering FR-004, SC-002, SC-003 in Source/ThorTactics/Frontend/ThorOptionsMenuTypes.cpp
- [ ] T026 Implement Home Options action binding and focus return target storage covering FR-001, FR-007 in Source/ThorTactics/Frontend/ThorHomeMenuWidget.cpp
- [ ] T027 Implement baseline Options surface rendering from resolved category actions covering FR-003, FR-004, FR-005 in Source/ThorTactics/Frontend/ThorOptionsMenuWidget.cpp
- [ ] T028 Implement Back and Cancel handling from Options to Home covering FR-006, FR-007 in Source/ThorTactics/Frontend/ThorOptionsMenuWidget.cpp
- [ ] T029 Guard the story scope so Options category display does not save, load, or require persisted settings covering FR-008 in Source/ThorTactics/Frontend/ThorOptionsMenuWidget.cpp
- [ ] T030 Wire Home -> Options -> Back automation hooks for baseline widgets covering FR-009, SC-001, SC-004 in Source/ThorTactics/Frontend/ThorHomeMenuWidget.cpp
- [ ] T031 Run the THOR unit test command for Source/ThorTactics/Tests/Frontend/OptionsMenuUnitTests.cpp and fix failures in Source/ThorTactics/Frontend/ThorOptionsMenuTypes.cpp, Source/ThorTactics/Frontend/ThorHomeMenuWidget.cpp, and Source/ThorTactics/Frontend/ThorOptionsMenuWidget.cpp
- [ ] T032 Run the THOR integration automation command for Source/ThorTactics/Tests/Frontend/OptionsMenuFlowAutomationTest.cpp and fix failures in Source/ThorTactics/Frontend/ThorHomeMenuWidget.cpp and Source/ThorTactics/Frontend/ThorOptionsMenuWidget.cpp

### Story Validation

- [ ] T033 Validate the full Home -> Options -> Back flow against FR-001 through FR-009 and SC-001 through SC-004 in Source/ThorTactics/Tests/Frontend/OptionsMenuFlowAutomationTest.cpp

**Checkpoint**: Native Options Menu Surface is functional, covered by unit and integration tests, and independently testable.

---

## Phase 4: Polish & Verification

**Purpose**: Strengthen the completed story without adding settings persistence or unrelated menu scope.

- [ ] T034 [P] Review and remove any temporary debug-only labels, routes, or flags from Source/ThorTactics/Frontend/ThorOptionsMenuWidget.cpp
- [ ] T035 [P] Review unit edge-case coverage for repeated enter/exit focus state and partial authored category data in Source/ThorTactics/Tests/Frontend/OptionsMenuUnitTests.cpp
- [ ] T036 [P] Review integration coverage for both Back and Cancel return paths in Source/ThorTactics/Tests/Frontend/OptionsMenuFlowAutomationTest.cpp
- [ ] T037 Run the quickstart validation sequence from specs/353-native-options-menu-surface/quickstart.md
- [ ] T038 Run `/moonspec-verify` for specs/353-native-options-menu-surface/spec.md after implementation and tests pass

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies; can start immediately.
- **Foundational (Phase 2)**: Depends on Phase 1; blocks story test and implementation work.
- **Story (Phase 3)**: Depends on Phase 2; tests must be authored and confirmed failing before implementation.
- **Polish (Phase 4)**: Depends on the story implementation and tests passing.

### Within The Story

- T010-T015 unit tests must be written before T021.
- T016-T020 integration tests must be written before T022.
- T021 and T022 red-first confirmations must complete before T023-T030 implementation tasks.
- T023-T025 establish category resolution before T027 renders category actions.
- T026 establishes Home navigation and return focus before T028 and T030 wire full navigation.
- T031 and T032 must pass before T033 story validation.
- T038 final verification runs only after T037 quickstart validation.

### Parallel Opportunities

- T003 and T004 can run in parallel after T001-T002.
- T008 and T009 can run in parallel after T006-T007.
- T010-T015 should be authored serially because they modify the same unit test file.
- T016-T020 should be authored serially because they modify the same automation test file.
- T034-T036 can run in parallel after story validation.

---

## Parallel Example: Story Phase

```bash
# Unit and integration test authoring can be split by file:
Task: "Add failing unit tests for stable identifiers and fallback categories in Source/ThorTactics/Tests/Frontend/OptionsMenuUnitTests.cpp"
Task: "Add failing automation tests for Home -> Options -> Back in Source/ThorTactics/Tests/Frontend/OptionsMenuFlowAutomationTest.cpp"

# After red-first confirmation, category logic and UI shell work can be split carefully:
Task: "Implement category identifiers and fallback resolution in Source/ThorTactics/Frontend/ThorOptionsMenuTypes.cpp"
Task: "Implement baseline Options surface rendering in Source/ThorTactics/Frontend/ThorOptionsMenuWidget.cpp"
```

---

## Implementation Strategy

### Test-Driven Story Delivery

1. Complete Phase 1 and Phase 2 to establish the target files.
2. Write all unit and integration tests first.
3. Run the targeted unit and integration commands and confirm the tests fail for missing behavior.
4. Implement category identifiers, category resolution, Home navigation, Options rendering, Back/Cancel, focus restoration, and non-persistence guardrails.
5. Re-run unit tests until all unit behavior passes.
6. Re-run integration automation until Home -> Options -> Back passes without authored Options assets/data.
7. Run quickstart validation and final `/moonspec-verify`.

### Requirement Status Coverage

- Code-and-test work: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, SC-001, SC-002, SC-003, SC-004.
- Verification-only work: none.
- Conditional fallback work: none.
- Already verified work: none.

## Notes

- This task list targets the THOR Tactics repository. The current managed workspace is MoonMind and does not contain the target game files.
- Keep settings persistence out of scope.
- Preserve THOR-402 traceability through final verification.
- Do not add additional settings categories beyond what is required to keep authored/fallback category handling valid.
