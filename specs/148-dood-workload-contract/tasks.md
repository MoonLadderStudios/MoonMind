# Tasks: Docker-Out-of-Docker Workload Contract

**Input**: Design documents from `/specs/148-dood-workload-contract/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: TDD is required. Add failing workload contract tests first, run them to confirm the expected failure, then implement models/registry until they pass.

**Organization**: Tasks are grouped by user story so Phase 0 preservation, request validation, and registry loading are independently testable.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm feature artifacts and existing Phase 0 baseline before code changes.

- [X] T001 Review `specs/148-dood-workload-contract/spec.md`, `plan.md`, `data-model.md`, and `contracts/workload-contract.md`.
- [X] T002 Run `./tools/test_unit.sh tests/unit/docs/test_dood_phase0_contract.py` to verify the Phase 0 documentation contract still passes.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Create the TDD validation surface before implementing runtime contract code.

- [X] T003 [P] Add failing workload contract tests in `tests/unit/workloads/test_workload_contract.py` for valid request construction, deterministic labels, invalid env keys, invalid workspace paths, invalid resources, invalid images, invalid mounts, and registry loading failures.

**Checkpoint**: Workload contract tests fail because the Phase 1 models/registry do not exist yet.

---

## Phase 3: User Story 1 - Preserve the Phase 0 boundary contract (Priority: P1)

**Goal**: Keep Phase 0 docs and tracker aligned while Phase 1 code lands.

**Independent Test**: `./tools/test_unit.sh tests/unit/docs/test_dood_phase0_contract.py`

### Tests for User Story 1

- [X] T004 [US1] Re-run `./tools/test_unit.sh tests/unit/docs/test_dood_phase0_contract.py` after Phase 1 edits.

### Implementation for User Story 1

- [X] T005 [US1] Update `docs/tmp/remaining-work/ManagedAgents-DockerOutOfDocker.md` to mark Phase 1 contract work complete and keep later launcher/tool/artifact phases pending.

**Checkpoint**: Phase 0 validation remains green and the remaining-work tracker reflects Phase 1 completion.

---

## Phase 4: User Story 2 - Validate workload requests before Docker exists (Priority: P1) 🎯 MVP

**Goal**: Provide canonical Docker-free workload request/result/ownership models and profile-aware request validation.

**Independent Test**: `./tools/test_unit.sh tests/unit/workloads/test_workload_contract.py`

### Tests for User Story 2

- [X] T006 [US2] Confirm `tests/unit/workloads/test_workload_contract.py` fails before implementation because `moonmind.schemas.workload_models` and `moonmind.workloads.registry` are absent.

### Implementation for User Story 2

- [X] T007 [US2] Implement `WorkloadRequest`, `WorkloadResult`, resource models, mount models, and `WorkloadOwnershipMetadata` in `moonmind/schemas/workload_models.py`.
- [X] T008 [US2] Implement profile-aware request validation, workspace-root checks, env allowlist enforcement, resource limit enforcement, and deterministic ownership label derivation in `moonmind/workloads/registry.py`.

**Checkpoint**: A valid request can be constructed and invalid request inputs are rejected without Docker.

---

## Phase 5: User Story 3 - Load deployment-owned runner profiles safely (Priority: P2)

**Goal**: Provide a deployment-owned runner profile registry loader that accepts curated profiles and fails closed.

**Independent Test**: `./tools/test_unit.sh tests/unit/workloads/test_workload_contract.py`

### Implementation for User Story 3

- [X] T009 [US3] Implement `RunnerProfile`, profile policy validation, JSON/YAML registry loading, duplicate detection, and empty-registry behavior in `moonmind/schemas/workload_models.py` and `moonmind/workloads/registry.py`.
- [X] T010 [US3] Export workload contract APIs from `moonmind/workloads/__init__.py`.

**Checkpoint**: Registry tests accept valid JSON/YAML profile input and reject unsafe images, mounts, network, devices, and resource ceilings.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final verification, artifact consistency, and completed task state.

- [X] T011 Run `./tools/test_unit.sh tests/unit/workloads/test_workload_contract.py tests/unit/docs/test_dood_phase0_contract.py`.
- [X] T012 Run `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`.
- [X] T013 Run `.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`, keeping the validator portable under the repository's Bash when required.
- [X] T014 Mark completed work in `specs/148-dood-workload-contract/tasks.md`.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: Starts immediately.
- **Foundational (Phase 2)**: Depends on Setup and blocks implementation.
- **User Story 1 (Phase 3)**: Can proceed after tests exist.
- **User Story 2 (Phase 4)**: Depends on failing tests and is the MVP.
- **User Story 3 (Phase 5)**: Extends US2 registry loading.
- **Polish (Phase 6)**: Depends on all selected implementation phases.

### Parallel Opportunities

- T003 can be written independently of docs tracker updates.
- T007 and T009 touch shared model files and must be coordinated sequentially despite different story labels.

## Implementation Strategy

### MVP First

1. Preserve Phase 0 doc validation.
2. Add failing tests for workload request/profile validation.
3. Implement request/result/ownership models and registry validation.
4. Update tracker and run focused validation.

### TDD Order

1. Write `tests/unit/workloads/test_workload_contract.py`.
2. Run the focused test and confirm it fails on missing implementation.
3. Implement models and registry loader.
4. Re-run focused tests until green.
5. Run scope gates and full unit validation as feasible.
