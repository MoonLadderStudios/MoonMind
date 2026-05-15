# Tasks: Layered Modal Recovery Surfaces

**Input**: Design documents from `/work/agent_jobs/mm:f936f8ea-012f-4100-a03c-3cb6f1e7cdf6/repo/specs/356-layered-modal-recovery-surfaces/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/modal-recovery-ui-contract.md, quickstart.md

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement production code until they pass.

**Organization**: Tasks cover exactly one story: Layered Modal Recovery Surfaces for THOR-405.

**Source Traceability**: Original Jira issue THOR-405 and preset brief are preserved in `spec.md` `**Input**`. All plan `## Requirement Status` rows are `missing`, so each FR requires test-and-code coverage. The THOR paths below are concrete proposed target paths and must be confirmed against the actual THOR repository during setup.

**Test Commands**:

- Unit tests: `Run the THOR repository's standard unit/automation test command for Source/ThorTactics/Tests/Frontend/ModalRecoveryUnitTests.cpp`
- Integration tests: `Run the THOR repository's standard game automation command for Source/ThorTactics/Tests/Frontend/ModalRecoveryFlowAutomationTest.cpp`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel because it touches different files and has no dependency on incomplete work
- Every task includes a concrete target file path for the THOR Tactics repository
- Requirement, scenario, success criterion, or contract IDs are included where behavior is validated or implemented

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish the target file layout and test harness hooks before writing story tests.

- [ ] T001 Confirm or create frontend modal source directory for this story in Source/ThorTactics/Frontend/
- [ ] T002 Confirm or create frontend modal test directory for this story in Source/ThorTactics/Tests/Frontend/
- [ ] T003 [P] Add modal recovery unit test file scaffold in Source/ThorTactics/Tests/Frontend/ModalRecoveryUnitTests.cpp
- [ ] T004 [P] Add modal recovery flow automation test file scaffold in Source/ThorTactics/Tests/Frontend/ModalRecoveryFlowAutomationTest.cpp
- [ ] T005 Document the THOR repository unit and integration test commands for this story in specs/356-layered-modal-recovery-surfaces/quickstart.md

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Define the minimal modal types, layer controller boundary, and native base hooks that story tests can target.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [ ] T006 Create modal type declarations for modal kind, modal state, recovery action, dismiss destination, and confirmation outcome covering FR-001 through FR-009 in Source/ThorTactics/Frontend/TacticsModalTypes.h
- [ ] T007 Create empty implementation shell for modal type helpers in Source/ThorTactics/Frontend/TacticsModalTypes.cpp
- [ ] T008 Declare modal layer controller extension point for push, replace, dismiss, and top-modal queries covering FR-001 and FR-011 in Source/ThorTactics/Frontend/TacticsModalLayerController.h
- [ ] T009 Create empty implementation shell for modal layer controller behavior in Source/ThorTactics/Frontend/TacticsModalLayerController.cpp
- [ ] T010 [P] Declare native modal base extension point for shared action, dismiss, and fallback behavior covering FR-002 and FR-010 in Source/ThorTactics/Frontend/TacticsNativeModalBase.h
- [ ] T011 [P] Create empty implementation shell for native modal base behavior in Source/ThorTactics/Frontend/TacticsNativeModalBase.cpp

**Checkpoint**: Foundation ready; red-first story tests can target concrete files.

---

## Phase 3: Story - Layered Modal Recovery Surfaces

**Summary**: As a player, I want progress, error, retry, dismiss, and confirmation modals to behave consistently through the frontend layer stack so failure recovery is predictable.

**Independent Test**: Trigger progress, blocking error, retry, dismiss, and confirmation modal flows through the frontend runtime, confirm each modal appears through the modal layer, executes recovery or navigation outcomes correctly, works through native fallback, and can be pushed and dismissed through the layer stack.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, FR-012, SCN-001, SCN-002, SCN-003, SCN-004, SCN-005, SCN-006, SCN-007, SCN-008, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, SC-007, UI contract `contracts/modal-recovery-ui-contract.md`

**Test Plan**:

- Unit: modal layer routing, shared modal behavior, progress blocking, retry action execution, missing recovery guardrails, dismiss destination resolution, confirmation outcome routing, and stack invariants.
- Integration: production modal presentation through the modal layer, native fallback progress/error/retry/dismiss/confirmation flows, and layer push/dismiss behavior with authored modal subclasses absent.

### Unit Tests (write first)

> Write these tests FIRST. Run them, confirm they FAIL for the expected reason, then implement only enough code to make them pass.

- [ ] T012 Add failing unit tests for routing progress, blocking error, retry, dismiss, and confirmation states through the modal layer covering FR-001, SCN-001, SCN-002 in Source/ThorTactics/Tests/Frontend/ModalRecoveryUnitTests.cpp
- [ ] T013 Add failing unit tests for shared native modal base behavior across required modal states covering FR-002, FR-010 in Source/ThorTactics/Tests/Frontend/ModalRecoveryUnitTests.cpp
- [ ] T014 Add failing unit tests for progress modal interaction blocking covering FR-003, SC-001 in Source/ThorTactics/Tests/Frontend/ModalRecoveryUnitTests.cpp
- [ ] T015 Add failing unit tests for blocking error acknowledgement or recovery behavior covering FR-004, SC-002 in Source/ThorTactics/Tests/Frontend/ModalRecoveryUnitTests.cpp
- [ ] T016 Add failing unit tests for captured recovery action execution exactly once per Retry selection covering FR-005, SCN-003, SC-003 in Source/ThorTactics/Tests/Frontend/ModalRecoveryUnitTests.cpp
- [ ] T017 Add failing unit tests for missing recovery action guardrails covering FR-006 and missing-recovery edge case in Source/ThorTactics/Tests/Frontend/ModalRecoveryUnitTests.cpp
- [ ] T018 Add failing unit tests for Dismiss destination selection to Home and explicit prior state covering FR-007, FR-008, SCN-004, SCN-005 in Source/ThorTactics/Tests/Frontend/ModalRecoveryUnitTests.cpp
- [ ] T019 Add failing unit tests for confirmation outcome routing and modal close behavior covering FR-009, SCN-006 in Source/ThorTactics/Tests/Frontend/ModalRecoveryUnitTests.cpp
- [ ] T020 Add failing unit tests for modal add, replace, top-modal, and dismiss stack invariants covering FR-011, SC-007 in Source/ThorTactics/Tests/Frontend/ModalRecoveryUnitTests.cpp

### Integration Tests (write first)

- [ ] T021 Add failing automation test for progress modal layer presentation and interaction blocking covering FR-001, FR-003, SCN-001, SC-001 in Source/ThorTactics/Tests/Frontend/ModalRecoveryFlowAutomationTest.cpp
- [ ] T022 Add failing automation test for blocking error modal layer presentation and shared behavior covering FR-001, FR-002, FR-004, SCN-002, SC-002 in Source/ThorTactics/Tests/Frontend/ModalRecoveryFlowAutomationTest.cpp
- [ ] T023 Add failing automation test for retryable failure executing captured recovery action covering FR-005, FR-006, SCN-003, SC-003 in Source/ThorTactics/Tests/Frontend/ModalRecoveryFlowAutomationTest.cpp
- [ ] T024 Add failing automation test for Dismiss returning to Home without explicit prior state covering FR-007, SCN-004, SC-004 in Source/ThorTactics/Tests/Frontend/ModalRecoveryFlowAutomationTest.cpp
- [ ] T025 Add failing automation test for Dismiss returning to configured prior state covering FR-008, SCN-005, SC-005 in Source/ThorTactics/Tests/Frontend/ModalRecoveryFlowAutomationTest.cpp
- [ ] T026 Add failing automation test for confirmation confirm and cancel outcomes closing the modal covering FR-009, SCN-006 in Source/ThorTactics/Tests/Frontend/ModalRecoveryFlowAutomationTest.cpp
- [ ] T027 Add failing automation test for native fallback progress, error, retry, dismiss, and confirmation modals with authored subclasses absent covering FR-010, SCN-007, SC-006 in Source/ThorTactics/Tests/Frontend/ModalRecoveryFlowAutomationTest.cpp
- [ ] T028 Add failing automation test for modal layer push/dismiss cleanup covering FR-011, FR-012, SCN-008, SC-007 in Source/ThorTactics/Tests/Frontend/ModalRecoveryFlowAutomationTest.cpp
- [ ] T029 Add contract-focused automation assertions for `contracts/modal-recovery-ui-contract.md` covering FR-012 in Source/ThorTactics/Tests/Frontend/ModalRecoveryFlowAutomationTest.cpp

### Red-First Confirmation

- [ ] T030 Run the THOR unit test command for Source/ThorTactics/Tests/Frontend/ModalRecoveryUnitTests.cpp and confirm T012-T020 fail for missing modal recovery behavior
- [ ] T031 Run the THOR integration automation command for Source/ThorTactics/Tests/Frontend/ModalRecoveryFlowAutomationTest.cpp and confirm T021-T029 fail for missing modal layer and fallback behavior

### Implementation

- [ ] T032 Implement modal kind, state, recovery action, dismiss destination, and confirmation outcome helpers covering FR-001 through FR-009 in Source/ThorTactics/Frontend/TacticsModalTypes.cpp
- [ ] T033 Implement modal layer controller push, replace, top-modal, and dismiss behavior covering FR-001 and FR-011 in Source/ThorTactics/Frontend/TacticsModalLayerController.cpp
- [ ] T034 Implement shared native modal base action registration, dismiss handling, and fallback rendering hooks covering FR-002 and FR-010 in Source/ThorTactics/Frontend/TacticsNativeModalBase.cpp
- [ ] T035 Implement progress modal behavior that routes through the modal layer and blocks conflicting interaction covering FR-003, SC-001 in Source/ThorTactics/Frontend/TacticsProgressModalWidget.cpp
- [ ] T036 Implement blocking error modal acknowledgement and recovery behavior covering FR-004, SC-002 in Source/ThorTactics/Frontend/TacticsErrorModalWidget.cpp
- [ ] T037 Implement retry modal recovery action execution and absent-action guardrails covering FR-005 and FR-006 in Source/ThorTactics/Frontend/TacticsErrorModalWidget.cpp
- [ ] T038 Implement dismiss routing to Home and explicit prior state covering FR-007 and FR-008 in Source/ThorTactics/Frontend/TacticsFrontendCoordinator.cpp
- [ ] T039 Implement confirmation modal outcome routing and close behavior covering FR-009 in Source/ThorTactics/Frontend/TacticsConfirmationModalWidget.cpp
- [ ] T040 Wire native fallback modal creation for progress, blocking error, retry, dismiss, and confirmation flows covering FR-010 in Source/ThorTactics/Frontend/TacticsNativeModalBase.cpp
- [ ] T041 Wire frontend coordinator modal presentation through `UI.Layer.Modal` or the target repository's equivalent modal layer covering FR-001, FR-011, and contract `contracts/modal-recovery-ui-contract.md` in Source/ThorTactics/Frontend/TacticsFrontendCoordinator.cpp
- [ ] T042 Run the THOR unit test command for Source/ThorTactics/Tests/Frontend/ModalRecoveryUnitTests.cpp and fix failures in Source/ThorTactics/Frontend/TacticsModalTypes.cpp, Source/ThorTactics/Frontend/TacticsModalLayerController.cpp, Source/ThorTactics/Frontend/TacticsNativeModalBase.cpp, Source/ThorTactics/Frontend/TacticsErrorModalWidget.cpp, and Source/ThorTactics/Frontend/TacticsFrontendCoordinator.cpp
- [ ] T043 Run the THOR integration automation command for Source/ThorTactics/Tests/Frontend/ModalRecoveryFlowAutomationTest.cpp and fix failures in Source/ThorTactics/Frontend/TacticsProgressModalWidget.cpp, Source/ThorTactics/Frontend/TacticsErrorModalWidget.cpp, Source/ThorTactics/Frontend/TacticsConfirmationModalWidget.cpp, Source/ThorTactics/Frontend/TacticsNativeModalBase.cpp, and Source/ThorTactics/Frontend/TacticsFrontendCoordinator.cpp

### Story Validation

- [ ] T044 Validate the full modal recovery story against FR-001 through FR-012 and SC-001 through SC-007 in Source/ThorTactics/Tests/Frontend/ModalRecoveryFlowAutomationTest.cpp

**Checkpoint**: Layered Modal Recovery Surfaces are functional, covered by unit and integration tests, and independently testable.

---

## Phase 4: Polish & Verification

**Purpose**: Strengthen the completed story without adding unrelated menu or presentation scope.

- [ ] T045 [P] Review and remove temporary debug-only labels, routes, or flags from Source/ThorTactics/Frontend/TacticsModalLayerController.cpp
- [ ] T046 [P] Review unit edge-case coverage for missing recovery action, invalid prior state, repeated retry failure, and stacked modal replacement in Source/ThorTactics/Tests/Frontend/ModalRecoveryUnitTests.cpp
- [ ] T047 [P] Review integration coverage for authored-subclass-absent fallback behavior in Source/ThorTactics/Tests/Frontend/ModalRecoveryFlowAutomationTest.cpp
- [ ] T048 Run the quickstart validation sequence from specs/356-layered-modal-recovery-surfaces/quickstart.md
- [ ] T049 Run `/moonspec-verify` for specs/356-layered-modal-recovery-surfaces/spec.md after implementation and tests pass

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies; can start immediately.
- **Foundational (Phase 2)**: Depends on Phase 1; blocks story test and implementation work.
- **Story (Phase 3)**: Depends on Phase 2; tests must be authored and confirmed failing before implementation.
- **Polish (Phase 4)**: Depends on the story implementation and tests passing.

### Within The Story

- T012-T020 unit tests must be written before T030.
- T021-T029 integration tests must be written before T031.
- T030 and T031 red-first confirmations must complete before T032-T041 implementation tasks.
- T032 establishes modal state helpers before T035-T039 consume modal state.
- T033 establishes layer stack behavior before T035-T041 route presentation through the modal layer.
- T034 establishes shared fallback/modal base behavior before T040 wires fallback creation.
- T042 and T043 must pass before T044 story validation.
- T049 final verification runs only after T048 quickstart validation.

### Parallel Opportunities

- T003 and T004 can run in parallel after T001-T002.
- T010 and T011 can run in parallel after T006-T009.
- T012-T020 should be authored serially because they modify the same unit test file.
- T021-T029 should be authored serially because they modify the same automation test file.
- T045-T047 can run in parallel after story validation.

---

## Parallel Example: Story Phase

```bash
# Unit and integration test authoring can be split by file:
Task: "Add failing unit tests for retry, dismiss, confirmation, and layer stack behavior in Source/ThorTactics/Tests/Frontend/ModalRecoveryUnitTests.cpp"
Task: "Add failing automation tests for fallback modal recovery flows in Source/ThorTactics/Tests/Frontend/ModalRecoveryFlowAutomationTest.cpp"

# After red-first confirmation, modal layer and widget work can be split carefully:
Task: "Implement modal layer stack behavior in Source/ThorTactics/Frontend/TacticsModalLayerController.cpp"
Task: "Implement native fallback modal behavior in Source/ThorTactics/Frontend/TacticsNativeModalBase.cpp"
```

---

## Implementation Strategy

### Test-Driven Story Delivery

1. Complete Phase 1 and Phase 2 to establish the target files.
2. Write all unit and integration tests first.
3. Run the targeted unit and integration commands and confirm the tests fail for missing behavior.
4. Implement modal state helpers, layer stack behavior, shared native modal behavior, progress/error/retry/dismiss/confirmation widgets, and coordinator routing.
5. Re-run unit tests until all unit behavior passes.
6. Re-run integration automation until progress, blocking error, retry, dismiss, confirmation, fallback, and stack flows pass.
7. Run quickstart validation and final `/moonspec-verify`.

### Requirement Status Coverage

- Code-and-test work: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, FR-012, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, SC-007.
- Verification-only work: none.
- Conditional fallback work: none.
- Already verified work: none.

## Notes

- This task list targets the THOR Tactics repository. The current managed workspace is MoonMind and does not contain the target game files.
- Keep final authored presentation assets out of scope; native fallback modal behavior is required.
- Preserve THOR-405 traceability through final verification.
- Do not add unrelated menu, settings, or persistence behavior while implementing this story.
