# Tasks: Full Frontend Runtime Proof Coverage

**Input**: Design documents from `/specs/357-full-frontend-runtime-proof-coverage/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped by phase around a single user story so the work stays focused, traceable, and independently testable.

**Source Traceability**: FR-001 through FR-014, SCN-001 through SCN-006, and SC-001 through SC-006 are traced through the tasks below.

**Test Commands**:

- Unit tests: Target THOR command for C++ automation/unit-style frontend proof tests
- Integration tests: Target THOR command for TacticsEditor compile, frontend automation, and map or entry smoke
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Path Conventions

- Target implementation paths are in the THOR Tactics Unreal workspace.
- This MoonMind checkout does not contain the target THOR source; implementation tasks are blocked until run in that workspace.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish the validation harness locations and evidence-output surface.

- [ ] T001 Confirm THOR workspace contains the `.uproject`, `Source/ThorTactics/`, and TacticsEditor target for FR-001 in the target repository root
- [ ] T002 Create frontend runtime proof tool directory in `Source/ThorTactics/Tools/FrontendRuntimeProof/`
- [ ] T003 [P] Create frontend runtime proof test directory in `Source/ThorTactics/Tests/Frontend/`
- [ ] T004 [P] Create or update validation evidence output location in `Source/ThorTactics/Tools/FrontendRuntimeProof/README.md`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Define reusable evidence records, fallback decisions, and telemetry parsing before runtime proof tasks begin.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [ ] T005 Add failing unit tests for runtime evidence record formatting covering FR-011 and SC-004 in `Source/ThorTactics/Tests/Frontend/FrontendRuntimeProofUnitTests.cpp`
- [ ] T006 Add failing unit tests for Docker fallback decision logic covering FR-012 and SCN-005 in `Source/ThorTactics/Tests/Frontend/FrontendRuntimeProofUnitTests.cpp`
- [ ] T007 Add failing unit tests for generated selection telemetry evidence extraction covering FR-009 in `Source/ThorTactics/Tests/Frontend/FrontendRuntimeProofUnitTests.cpp`
- [ ] T008 Run the target THOR unit test command for `Source/ThorTactics/Tests/Frontend/FrontendRuntimeProofUnitTests.cpp` and confirm T005-T007 fail for the expected missing proof harness
- [ ] T009 Implement evidence record model and formatter for FR-011 in `Source/ThorTactics/Frontend/TacticsFrontendEvidence.cpp`
- [ ] T010 Implement fallback decision helper for FR-012 in `Source/ThorTactics/Tools/FrontendRuntimeProof/RunFrontendRuntimeProof.cpp`
- [ ] T011 Implement telemetry extraction helper for FR-009 in `Source/ThorTactics/Frontend/TacticsFrontendTelemetry.cpp`

**Checkpoint**: Foundation ready - story test and implementation work can now begin.

---

## Phase 3: Story - Full Frontend Runtime Proof Coverage

**Summary**: As a developer, I want runtime proof coverage for the full frontend menu architecture so implementation is verified beyond unit-level widget construction.

**Independent Test**: Run the three-tier validation sequence and confirm the evidence summary contains exact commands, exit codes, required flow coverage, key `LogTactics` lines, fallback status, and PR-ready reporting.

**Traceability**: FR-001 through FR-014, SCN-001 through SCN-006, SC-001 through SC-006.

**Test Plan**:

- Unit: evidence formatting, fallback decisions, telemetry extraction, and non-goal guardrails.
- Integration: TacticsEditor compile, required frontend automation flows, map or entry smoke, fallback behavior, quickstart and PR evidence reporting.

### Unit Tests (write first)

- [ ] T012 Add failing unit test for Tier 1 evidence command and exit-code preservation covering FR-001, FR-002, SCN-001, and SC-001 in `Source/ThorTactics/Tests/Frontend/FrontendRuntimeProofUnitTests.cpp`
- [ ] T013 Add failing unit test for PR-ready summary completeness covering FR-011, FR-013, SCN-004, SCN-006, and SC-006 in `Source/ThorTactics/Tests/Frontend/FrontendRuntimeProofUnitTests.cpp`
- [ ] T014 Add failing unit test that rejects frontend feature implementation tasks inside proof-only evidence output covering FR-014 in `Source/ThorTactics/Tests/Frontend/FrontendRuntimeProofUnitTests.cpp`
- [ ] T015 Run the target THOR unit test command for `Source/ThorTactics/Tests/Frontend/FrontendRuntimeProofUnitTests.cpp` and confirm T012-T014 fail for the expected missing behavior

### Integration Tests (write first)

- [ ] T016 Add failing integration automation for Home startup coverage covering FR-003 and SCN-002 in `Source/ThorTactics/Tests/Frontend/FrontendRuntimeProofAutomationTest.cpp`
- [ ] T017 Add failing integration automation for generated Home navigation coverage covering FR-004 and SCN-002 in `Source/ThorTactics/Tests/Frontend/FrontendRuntimeProofAutomationTest.cpp`
- [ ] T018 Add failing integration automation for Play panel coverage covering FR-005 and SCN-002 in `Source/ThorTactics/Tests/Frontend/FrontendRuntimeProofAutomationTest.cpp`
- [ ] T019 Add failing integration automation for Options panel coverage covering FR-006 and SCN-002 in `Source/ThorTactics/Tests/Frontend/FrontendRuntimeProofAutomationTest.cpp`
- [ ] T020 Add failing integration automation for modal behavior coverage covering FR-007 and SCN-002 in `Source/ThorTactics/Tests/Frontend/FrontendRuntimeProofAutomationTest.cpp`
- [ ] T021 Add failing integration automation for blocked Online Co-op coverage covering FR-008 and SCN-002 in `Source/ThorTactics/Tests/Frontend/FrontendRuntimeProofAutomationTest.cpp`
- [ ] T022 Add failing integration automation for generated selection telemetry coverage covering FR-009, SCN-002, and SC-002 in `Source/ThorTactics/Tests/Frontend/FrontendRuntimeProofAutomationTest.cpp`
- [ ] T023 Add failing integration smoke for `/Game/Maps/MainMenu` or active frontend entry route covering FR-010, SCN-003, and SC-003 in `Source/ThorTactics/Tests/Frontend/FrontendRuntimeProofAutomationTest.cpp`
- [ ] T024 Add failing integration validation that Docker fallback is attempted before CI-only classification covering FR-012, SCN-005, and SC-005 in `Source/ThorTactics/Tests/Frontend/FrontendRuntimeProofAutomationTest.cpp`
- [ ] T025 Run the target THOR integration automation command for `Source/ThorTactics/Tests/Frontend/FrontendRuntimeProofAutomationTest.cpp` and confirm T016-T024 fail for expected missing proof coverage

### Implementation

- [ ] T026 Implement Tier 1 TacticsEditor compile wrapper and evidence capture for FR-001, FR-002, SCN-001, and SC-001 in `Source/ThorTactics/Tools/FrontendRuntimeProof/RunFrontendRuntimeProof.cpp`
- [ ] T027 Implement Tier 2 automation orchestration and flow checklist aggregation for FR-003 through FR-009, SCN-002, and SC-002 in `Source/ThorTactics/Frontend/TacticsFrontendRuntimeProof.cpp`
- [ ] T028 Wire Home startup and generated Home navigation evidence hooks for FR-003 and FR-004 in `Source/ThorTactics/Frontend/TacticsFrontendRuntimeProof.cpp`
- [ ] T029 Wire Play panel and Options panel evidence hooks for FR-005 and FR-006 in `Source/ThorTactics/Frontend/TacticsFrontendRuntimeProof.cpp`
- [ ] T030 Wire modal behavior and blocked Online Co-op evidence hooks for FR-007 and FR-008 in `Source/ThorTactics/Frontend/TacticsFrontendRuntimeProof.cpp`
- [ ] T031 Wire generated selection telemetry capture for FR-009 in `Source/ThorTactics/Frontend/TacticsFrontendTelemetry.cpp`
- [ ] T032 Implement Tier 3 map or active entry smoke wrapper for FR-010, SCN-003, and SC-003 in `Source/ThorTactics/Tools/FrontendRuntimeProof/RunFrontendRuntimeProof.cpp`
- [ ] T033 Implement evidence summary rendering with commands, exit codes, key `LogTactics` lines, fallback status, and PR-ready output for FR-011 and FR-013 in `Source/ThorTactics/Frontend/TacticsFrontendEvidence.cpp`
- [ ] T034 Implement Docker fallback attempt and CI-only classification rules for FR-012 in `Source/ThorTactics/Tools/FrontendRuntimeProof/RunFrontendRuntimeProof.cpp`
- [ ] T035 Update `specs/357-full-frontend-runtime-proof-coverage/quickstart.md` with actual target THOR commands, exit codes, and validation results covering FR-013 and SC-006
- [ ] T036 Run target THOR unit and integration validation commands, fix failures, and record all three tier results in `specs/357-full-frontend-runtime-proof-coverage/quickstart.md`

**Checkpoint**: The story is fully functional, covered by unit and integration tests, and testable independently.

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: Strengthen the completed proof coverage without expanding into frontend feature implementation.

- [ ] T037 [P] Review evidence output for secret-safe and reviewable log summaries in `Source/ThorTactics/Frontend/TacticsFrontendEvidence.cpp`
- [ ] T038 [P] Ensure quickstart evidence and PR-ready summary use the same tier labels as `contracts/runtime-proof-evidence-contract.md`
- [ ] T039 Confirm no production frontend menu feature behavior was added beyond validation/evidence seams for FR-014 in `Source/ThorTactics/Frontend/TacticsFrontendRuntimeProof.cpp`
- [ ] T040 Run `/moonspec-verify` against `specs/357-full-frontend-runtime-proof-coverage/spec.md` to validate the final implementation against the original THOR-406 Jira brief

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately in the target THOR workspace
- **Foundational (Phase 2)**: Depends on Setup completion - blocks story proof work
- **Story (Phase 3)**: Depends on Foundational phase completion
- **Polish (Phase 4)**: Depends on the story being functionally complete and tests passing

### Within The Story

- Unit tests must be written and fail before implementation.
- Integration tests must be written and fail before implementation.
- Evidence model and fallback helpers must exist before tier orchestration.
- Tier 2 flow hooks can be implemented in parallel after the automation tests exist.
- Quickstart and PR-ready evidence must be updated after real validation commands run.

### Parallel Opportunities

- T003-T004 can run in parallel after T001.
- T012-T014 must be sequenced because they all edit `Source/ThorTactics/Tests/Frontend/FrontendRuntimeProofUnitTests.cpp`.
- T016-T022 must be sequenced because they all edit `Source/ThorTactics/Tests/Frontend/FrontendRuntimeProofAutomationTest.cpp`.
- T031 can run independently after T027 defines the shared aggregation surface; T028-T030 should be sequenced because they edit `Source/ThorTactics/Frontend/TacticsFrontendRuntimeProof.cpp`.
- T037-T038 can run in parallel after T033 and T035.

## Parallel Example: Story Phase

```bash
Task: "Add failing integration automation for Home startup coverage in Source/ThorTactics/Tests/Frontend/FrontendRuntimeProofAutomationTest.cpp"
Task: "Add failing unit test for PR-ready summary completeness in Source/ThorTactics/Tests/Frontend/FrontendRuntimeProofUnitTests.cpp"
```

## Implementation Strategy

### Test-Driven Story Delivery

1. Run this task list in the target THOR Tactics workspace.
2. Confirm the workspace contains the Unreal project and TacticsEditor target.
3. Write evidence, fallback, telemetry, and automation tests first.
4. Confirm the tests fail for the expected missing runtime proof behavior.
5. Implement the proof harness, flow hooks, fallback wrapper, and evidence rendering.
6. Run Tier 1 compile, Tier 2 automation, and Tier 3 smoke.
7. Update quickstart and PR-ready evidence with exact commands, exit codes, and key `LogTactics` lines.
8. Run `/moonspec-verify` against the original THOR-406 Jira brief.

## Notes

- THOR-406 is proof coverage only; do not implement new frontend menu features unless a test exposes a minimal evidence seam that cannot otherwise be observed.
- This MoonMind repository does not contain the target THOR source, so implementation cannot start in this checkout.
