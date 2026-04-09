# Tasks: Docker-Out-of-Docker Phase 0 Contract Lock

**Input**: Design documents from `/specs/143-dood-phase0/`
**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/, quickstart.md

**Tests**: TDD is required for this feature. Start with the focused documentation-contract test, make it fail, then update docs and tracker files until it passes.

**Organization**: Tasks are grouped by user story to keep the Phase 0 contract lock independently reviewable and executable.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the feature artifacts and validation surface before editing canonical docs

- [X] T001 Review `specs/143-dood-phase0/spec.md`, `specs/143-dood-phase0/plan.md`, and `specs/143-dood-phase0/contracts/dood-phase0-doc-contract.md` to lock the Phase 0 assertions.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish the shared validation surface and remaining-work tracker before story-specific doc edits

- [X] T002 [P] Add a failing documentation-contract test in `tests/unit/docs/test_dood_phase0_contract.py` covering glossary terms, tool-path wording, and tracker references.
- [X] T003 Create the DooD remaining-work tracker in `docs/tmp/remaining-work/ManagedAgents-DockerOutOfDocker.md` and register it in `docs/tmp/remaining-work/README.md`.

**Checkpoint**: The automated contract guard exists and the rollout tracker path is in place.

---

## Phase 3: User Story 1 - Align the canonical DooD glossary and boundary docs (Priority: P1) 🎯 MVP

**Goal**: Keep the DooD, session-plane, and execution-model docs on one glossary and one container-identity story.

**Independent Test**: Read the three canonical docs and confirm they share `session container`, `workload container`, `runner profile`, and `session-assisted workload` without implying that workload containers are session identity.

### Tests for User Story 1

- [X] T004 [US1] Run the focused glossary/boundary assertions in `tests/unit/docs/test_dood_phase0_contract.py` and confirm the initial failure before doc edits.

### Implementation for User Story 1

- [X] T005 [US1] Add the session-assisted workload cross-reference and outside-session-identity wording to `docs/ManagedAgents/CodexManagedSessionPlane.md`.
- [X] T006 [US1] Tighten glossary/tracker wording in `docs/ManagedAgents/DockerOutOfDocker.md` so the canonical doc remains the clear desired-state source for Phase 0.

**Checkpoint**: The session-plane doc and canonical DooD doc tell the same glossary and identity story.

---

## Phase 4: User Story 2 - Freeze the execution primitive and lifecycle scope (Priority: P1)

**Goal**: Preserve the tool-path-first execution boundary and the one-shot workload-container MVP scope.

**Independent Test**: Read the canonical docs and confirm Docker-backed workloads are documented as ordinary executable tools, not new `MoonMind.AgentRun` instances, while one-shot workload containers remain the initial implementation scope.

### Implementation for User Story 2

- [X] T007 [US2] Add the Docker-backed executable-tool note to `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`, explicitly distinguishing ordinary workload tools from true managed runtimes.
- [X] T008 [US2] Tighten `docs/ManagedAgents/DockerOutOfDocker.md` so the `tool.type = "skill"` boundary and one-shot-workload-first scope remain explicit.

**Checkpoint**: The execution-model and DooD docs agree on the execution primitive and MVP lifecycle scope.

---

## Phase 5: User Story 3 - Leave durable implementation tracking and executable validation (Priority: P2)

**Goal**: Leave a durable rollout tracker and green automated validation for the Phase 0 contract.

**Independent Test**: Run the focused doc-contract test and the full unit suite, then confirm the tracker is listed in `docs/tmp/remaining-work/README.md`.

### Tests for User Story 3

- [X] T009 [US3] Run `pytest -q tests/unit/docs/test_dood_phase0_contract.py` using `tests/unit/docs/test_dood_phase0_contract.py` as the targeted validation gate.
- [X] T010 [US3] Run `./tools/test_unit.sh` and record completion by updating `specs/143-dood-phase0/tasks.md`.

**Checkpoint**: Phase 0 leaves both durable tracking and executable validation behind.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final verification and artifact consistency

- [X] T011 [P] Verify the commands and expected outcomes in `specs/143-dood-phase0/quickstart.md`.
- [X] T012 [P] Mark completed work in `specs/143-dood-phase0/tasks.md` and confirm the feature artifacts stay aligned with the final docs/test result.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: Starts immediately.
- **Foundational (Phase 2)**: Depends on Setup and blocks all story work.
- **User Story 1 (Phase 3)**: Depends on Phase 2.
- **User Story 2 (Phase 4)**: Depends on Phase 2 and should follow the same canonical DooD wording updated in Phase 3.
- **User Story 3 (Phase 5)**: Depends on Phases 3 and 4 because it validates the finished wording.
- **Polish (Phase 6)**: Depends on all previous phases.

### Parallel Opportunities

- `T002` and `T003` can proceed in parallel after Setup.
- `T011` and `T012` can proceed in parallel once validation is complete.

## Implementation Strategy

### MVP First

1. Lock the test and tracker path.
2. Update the session-plane and canonical DooD docs.
3. Update the execution-model note.
4. Run targeted validation before the full unit suite.

### TDD Order

1. Write `tests/unit/docs/test_dood_phase0_contract.py`.
2. Run the focused test and confirm it fails.
3. Update the canonical docs and tracker files until the focused test passes.
4. Run `./tools/test_unit.sh` for final regression coverage.
