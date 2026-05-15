# Tasks: Unit Death And Ragdoll

**Input**: Design documents from `specs/358-unit-death-ragdoll/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/unit-death-ragdoll-contract.md`, `quickstart.md`

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks cover exactly one independently testable story: unit death and ragdoll runtime behavior for THOR-407.

**Source Traceability**: Original request preserved in `spec.md`: `THOR-407: Docs/TacticsUnits_Core/UnitDeathAndRagdoll.md`.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh --filter UnitDeathAndRagdoll`
- Integration tests: `./tools/test_integration.sh --filter UnitDeathAndRagdoll`
- Final verification: `/speckit.verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel when tasks touch different files and do not depend on incomplete work.
- Include exact file paths in descriptions.
- Include requirement, scenario, or source IDs when the task implements or validates behavior.

## Phase 1: Setup

**Purpose**: Confirm the target THOR workspace and create the story-local test/code structure.

- [ ] T001 Confirm the target THOR gameplay workspace contains project/build files and record the resolved project path in `specs/358-unit-death-ragdoll/quickstart.md` for THOR-407 traceability
- [ ] T002 Create or confirm target unit death source structure in `Source/ThorTactics/Units/UnitDeathState.h` and `Source/ThorTactics/Units/UnitDeathState.cpp`, `Source/ThorTactics/Units/UnitDeathPresentation.h` and `Source/ThorTactics/Units/UnitDeathPresentation.cpp`, and `Source/ThorTactics/Combat/EncounterResolution.h` and `Source/ThorTactics/Combat/EncounterResolution.cpp`
- [ ] T003 [P] Create or confirm target unit test file `Source/ThorTactics/Tests/Units/UnitDeathStateTests.cpp` for FR-001 through FR-008
- [ ] T004 [P] Create or confirm target integration test file `Source/ThorTactics/Tests/Units/UnitDeathIntegrationTests.cpp` for SCN-001 through SCN-005
- [ ] T005 Update target test runner configuration or local wrapper for `./tools/test_unit.sh --filter UnitDeathAndRagdoll` and `./tools/test_integration.sh --filter UnitDeathAndRagdoll` in `tools/` or the target project's equivalent test script

## Phase 2: Foundational

**Purpose**: Establish shared fixtures and contracts that block story test authoring and implementation.

**Checkpoint**: No story implementation work begins until this phase is complete.

- [ ] T006 Add shared unit test fixture builders for living units, lethal damage, overkill damage, non-damage defeat causes, ragdoll-capable units, and non-ragdoll units in `Source/ThorTactics/Tests/Units/UnitDeathStateTests.cpp` covering FR-001, FR-002, FR-004, and FR-008
- [ ] T007 Add shared integration fixture setup for encounter-like combat flow, turn order, targeting, victory checks, and cleanup in `Source/ThorTactics/Tests/Units/UnitDeathIntegrationTests.cpp` covering SCN-001, SCN-004, and SCN-005
- [ ] T008 Map `contracts/unit-death-ragdoll-contract.md` to target gameplay test assertions in `Source/ThorTactics/Tests/Units/UnitDeathIntegrationTests.cpp` covering the Death Transition, Combat Participation, Presentation, and Cleanup contracts
- [ ] T009 Preserve `THOR-407` and `THOR-407: Docs/TacticsUnits_Core/UnitDeathAndRagdoll.md` in implementation evidence notes in `specs/358-unit-death-ragdoll/quickstart.md` covering FR-009 and SC-006

## Phase 3: Story - Unit Death And Ragdoll

**Summary**: As a tactics gameplay player, I want defeated units to leave active combat cleanly and enter a clear death or ragdoll presentation, so combat outcomes are readable and dead units cannot continue acting.

**Independent Test**: Drive a unit from alive to defeated through normal gameplay damage, lethal overkill damage, repeated post-death damage, and cleanup scenarios; verify that the unit reaches one stable dead state, no longer acts or blocks active combat flow, emits the expected death presentation, and can be cleaned up without re-entering gameplay systems.

**Traceability**: FR-001 through FR-009, SCN-001 through SCN-005, SC-001 through SC-006, original request `THOR-407: Docs/TacticsUnits_Core/UnitDeathAndRagdoll.md`.

**Unit Test Plan**: Defeat detection, exactly-once dead-state transition, duplicate post-death events, participation predicates, cleanup reference safety, ragdoll capability, and fallback presentation.

**Integration Test Plan**: Lethal damage in an encounter-like flow, presentation start, turn/targeting/victory behavior after death, duplicate event safety in runtime flow, and cleanup after final death.

### Unit Tests

- [ ] T010 [P] Add failing unit tests for lethal damage, overkill damage, and non-damage defeat causes in `Source/ThorTactics/Tests/Units/UnitDeathStateTests.cpp` covering FR-001 and SC-001
- [ ] T011 [P] Add failing unit tests for exactly-one dead-state transition and repeated transition suppression in `Source/ThorTactics/Tests/Units/UnitDeathStateTests.cpp` covering FR-002, FR-005, and SC-004
- [ ] T012 [P] Add failing unit tests for dead-unit action, turn, and living-combatant targeting predicates in `Source/ThorTactics/Tests/Units/UnitDeathStateTests.cpp` covering FR-003 and SC-002
- [ ] T013 [P] Add failing unit tests for ragdoll-capable and non-ragdoll fallback presentation decisions in `Source/ThorTactics/Tests/Units/UnitDeathStateTests.cpp` covering FR-004, FR-008, and SC-003
- [ ] T014 [P] Add failing unit tests for cleanup, despawn, or corpse persistence reference safety in `Source/ThorTactics/Tests/Units/UnitDeathStateTests.cpp` covering FR-007 and SCN-005
- [ ] T015 Run `./tools/test_unit.sh --filter UnitDeathAndRagdoll` in the target THOR workspace and record expected red-first failures in `specs/358-unit-death-ragdoll/quickstart.md` covering T010 through T014

### Integration Tests

- [ ] T016 [P] Add failing integration test for normal lethal damage transitioning one unit to dead and removing it from active combat in `Source/ThorTactics/Tests/Units/UnitDeathIntegrationTests.cpp` covering SCN-001, FR-001, FR-002, FR-003, SC-001, and SC-002
- [ ] T017 [P] Add failing integration test for death or ragdoll presentation start and fallback presentation in `Source/ThorTactics/Tests/Units/UnitDeathIntegrationTests.cpp` covering SCN-002, FR-004, FR-008, and SC-003
- [ ] T018 [P] Add failing integration test for repeated post-death damage, delayed effects, collisions, and duplicate notifications in `Source/ThorTactics/Tests/Units/UnitDeathIntegrationTests.cpp` covering SCN-003, FR-005, and SC-004
- [ ] T019 [P] Add failing integration test for turn order, targeting, victory or encounter completion, and cleanup after death in `Source/ThorTactics/Tests/Units/UnitDeathIntegrationTests.cpp` covering SCN-004, SCN-005, FR-006, FR-007, and SC-005
- [ ] T020 Run `./tools/test_integration.sh --filter UnitDeathAndRagdoll` in the target THOR workspace and record expected red-first failures in `specs/358-unit-death-ragdoll/quickstart.md` covering T016 through T019

### Red-First Confirmation

- [ ] T021 Confirm unit and integration failures are due to missing unit death/ragdoll behavior rather than missing workspace, broken test configuration, or unrelated failures, and record the failure reason in `specs/358-unit-death-ragdoll/quickstart.md`

### Conditional Traceability Fallback

- [ ] T022 If FR-009 or SC-006 traceability verification fails, update `specs/358-unit-death-ragdoll/quickstart.md` and later verification notes to preserve `THOR-407` and `THOR-407: Docs/TacticsUnits_Core/UnitDeathAndRagdoll.md`

### Implementation

- [ ] T023 Implement or update defeat detection in `Source/ThorTactics/Units/UnitHealth.h` and `Source/ThorTactics/Units/UnitHealth.cpp` and `Source/ThorTactics/Units/UnitDeathState.h` and `Source/ThorTactics/Units/UnitDeathState.cpp` covering FR-001, SCN-001, and SC-001
- [ ] T024 Implement stable exactly-once dead-state transition and duplicate event guards in `Source/ThorTactics/Units/UnitDeathState.h` and `Source/ThorTactics/Units/UnitDeathState.cpp` covering FR-002, FR-005, SCN-003, and SC-004
- [ ] T025 Implement dead-unit participation predicates and active-combat removal in `Source/ThorTactics/Combat/TurnOrder.h` and `Source/ThorTactics/Combat/TurnOrder.cpp` and `Source/ThorTactics/Combat/Targeting.h` and `Source/ThorTactics/Combat/Targeting.cpp` covering FR-003, SCN-004, and SC-002
- [ ] T026 Implement death/ragdoll presentation trigger and non-ragdoll fallback in `Source/ThorTactics/Units/UnitDeathPresentation.h` and `Source/ThorTactics/Units/UnitDeathPresentation.cpp` covering FR-004, FR-008, SCN-002, and SC-003
- [ ] T027 Implement combat flow and cleanup integration in `Source/ThorTactics/Combat/EncounterResolution.h` and `Source/ThorTactics/Combat/EncounterResolution.cpp` covering FR-006, FR-007, SCN-004, SCN-005, and SC-005
- [ ] T028 Run `./tools/test_unit.sh --filter UnitDeathAndRagdoll` and `./tools/test_integration.sh --filter UnitDeathAndRagdoll` in the target THOR workspace, fix story-scoped failures, and record command results in `specs/358-unit-death-ragdoll/quickstart.md`
- [ ] T029 Validate the single story against `specs/358-unit-death-ragdoll/spec.md` and `specs/358-unit-death-ragdoll/contracts/unit-death-ragdoll-contract.md`, confirming FR-001 through FR-009, SCN-001 through SCN-005, and SC-001 through SC-006 are covered

## Phase 4: Polish And Verification

**Purpose**: Strengthen the completed story without adding hidden scope.

- [ ] T030 [P] Refactor story-local unit death code in `Source/ThorTactics/Units/UnitDeathState.h` and `Source/ThorTactics/Units/UnitDeathState.cpp` and `Source/ThorTactics/Units/UnitDeathPresentation.h` and `Source/ThorTactics/Units/UnitDeathPresentation.cpp` after tests are green without changing behavior
- [ ] T031 [P] Refactor story-local combat integration code in `Source/ThorTactics/Combat/TurnOrder.h` and `Source/ThorTactics/Combat/TurnOrder.cpp`, `Source/ThorTactics/Combat/Targeting.h` and `Source/ThorTactics/Combat/Targeting.cpp`, and `Source/ThorTactics/Combat/EncounterResolution.h` and `Source/ThorTactics/Combat/EncounterResolution.cpp` after tests are green without changing behavior
- [ ] T032 Update `specs/358-unit-death-ragdoll/quickstart.md` with final unit and integration command evidence, exit codes, and any target-workspace blockers
- [ ] T033 Run quickstart validation from `specs/358-unit-death-ragdoll/quickstart.md` and confirm the exact unit and integration commands pass in the target THOR workspace
- [ ] T034 Run `/speckit.verify` for `specs/358-unit-death-ragdoll/spec.md` after implementation and tests pass, and record the final verdict in `specs/358-unit-death-ragdoll/verification.md`

## Dependencies And Execution Order

### Phase Dependencies

- Phase 1 Setup has no dependencies.
- Phase 2 Foundational depends on Phase 1.
- Phase 3 Story depends on Phase 2 and must follow tests-first order.
- Phase 4 Polish And Verification depends on green unit and integration tests for the story.

### Story Order

- T010 through T014 create unit tests before implementation.
- T015 confirms unit tests fail red-first.
- T016 through T019 create integration tests before implementation.
- T020 confirms integration tests fail red-first.
- T021 confirms failures are story-relevant before production code.
- T022 is conditional fallback work for implemented-unverified traceability rows.
- T023 through T027 implement production behavior.
- T028 runs focused tests until green.
- T029 validates story coverage before polish.

### Parallel Opportunities

- T003 and T004 can run in parallel after T001.
- T010 through T014 can be authored in parallel after Phase 2 because they touch focused test sections.
- T016 through T019 can be authored in parallel after Phase 2 because they cover different runtime scenarios.
- T030 and T031 can run in parallel after tests are green because they touch separate story-local code areas.

## Parallel Example

```bash
# Unit test authoring in parallel:
Task: "T010 Add failing unit tests for defeat detection in Source/ThorTactics/Tests/Units/UnitDeathStateTests.cpp"
Task: "T013 Add failing unit tests for ragdoll fallback in Source/ThorTactics/Tests/Units/UnitDeathStateTests.cpp"

# Integration test authoring in parallel:
Task: "T016 Add failing integration test for lethal damage in Source/ThorTactics/Tests/Units/UnitDeathIntegrationTests.cpp"
Task: "T019 Add failing integration test for combat flow and cleanup in Source/ThorTactics/Tests/Units/UnitDeathIntegrationTests.cpp"
```

## Implementation Strategy

1. Complete setup and foundational fixture tasks in the target THOR workspace.
2. Write all unit tests and integration tests first.
3. Run focused test commands and confirm red-first failures are caused by missing THOR-407 behavior.
4. Implement the smallest story-scoped gameplay changes for death state, presentation, combat flow, and cleanup.
5. Re-run focused unit and integration tests until green.
6. Update quickstart evidence and run `/speckit.verify`.

## Notes

- This task list covers one story only.
- The current managed MoonMind workspace lacks the THOR gameplay source; implementation tasks must run in the target THOR workspace.
- `FR-009` and `SC-006` are `implemented_unverified`; T022 provides conditional fallback if later verification finds traceability gaps.
- No implementation work should begin until red-first unit and integration evidence exists.
