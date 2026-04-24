# Tasks: Managed-Session Retrieval Durability Boundaries

**Input**: Design documents from `specs/255-managed-session-retrieval-durability/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `quickstart.md`, `contracts/managed-session-retrieval-durability-contract.md`

**Tests**: Unit tests and integration/workflow-boundary tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement production changes only where the new verification exposes a real MM-507 gap.

**Organization**: Tasks are grouped by phase around the single MM-507 story so the work stays focused, traceable, and independently testable.

**Source Traceability**: The original MM-507 Jira preset brief is preserved in `spec.md`. Tasks cover exactly one story and map FR-001 through FR-006, acceptance scenarios 1 through 6, SC-001 through SC-006, and DESIGN-REQ-005, DESIGN-REQ-011, DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-017, and DESIGN-REQ-023. Requirement-status summary from `plan.md`: 2 missing rows require new contract-and-code work, 8 partial rows require test-first completion work, 2 implemented-unverified rows require verification-first coverage with a conditional fallback implementation task, and 1 implemented-verified row requires traceability-preserving final validation only.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/rag/test_context_pack.py tests/unit/rag/test_context_injection.py tests/unit/services/temporal/runtime/test_managed_session_controller.py tests/unit/services/temporal/runtime/test_launcher.py tests/unit/workflows/temporal/runtime/strategies/test_remaining_strategies.py tests/unit/workflows/temporal/test_agent_runtime_activities.py`
- Integration tests: `./tools/test_integration.sh`; targeted workflow-boundary command `pytest tests/integration/workflows/temporal/test_managed_session_retrieval_durability.py -q --tb=short`
- Final verification: `/moonspec-verify` / `/speckit.verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the active MM-507 artifacts and reserve the exact runtime and test surfaces for reset-era durability work.

- [X] T001 Verify the active feature artifacts exist in `specs/255-managed-session-retrieval-durability/spec.md`, `plan.md`, `research.md`, `data-model.md`, `quickstart.md`, and `contracts/managed-session-retrieval-durability-contract.md` for FR-006 and traceability coverage.
- [X] T002 Inspect the current retrieval publication and managed-session continuity boundaries in `moonmind/rag/context_injection.py`, `moonmind/rag/context_pack.py`, `moonmind/workflows/temporal/runtime/managed_session_controller.py`, `moonmind/workflows/temporal/runtime/managed_session_supervisor.py`, `moonmind/workflows/temporal/runtime/strategies/codex_cli.py`, and `moonmind/workflows/temporal/runtime/strategies/claude_code.py` to lock the extension points for FR-001 through FR-005 and DESIGN-REQ-005 / DESIGN-REQ-023.
- [X] T003 [P] Create `tests/integration/workflows/temporal/test_managed_session_retrieval_durability.py` for MM-507 workflow-boundary verification covering acceptance scenarios 1 through 5, SC-001 through SC-005, and DESIGN-REQ-005, DESIGN-REQ-011, DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-017, and DESIGN-REQ-023.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish only the reusable verification scaffolding the MM-507 story depends on.

**CRITICAL**: No story implementation work can begin until these blocking test scaffolds are ready.

- [X] T004 [P] Extend reusable retrieval publication fixtures in `tests/unit/rag/test_context_injection.py` and `tests/unit/services/temporal/runtime/test_launcher.py` so MM-507 can verify compact artifact metadata and durable artifact reuse without duplicating setup.
- [X] T005 [P] Extend managed-session continuity fixtures in `tests/unit/services/temporal/runtime/test_managed_session_controller.py` and `tests/integration/workflows/temporal/test_managed_session_retrieval_durability.py` for reset, reconcile, and session-epoch replacement scenarios covering FR-003 and FR-004.
- [X] T006 Confirm the selected unit and targeted integration commands from `specs/255-managed-session-retrieval-durability/quickstart.md` can execute against the reserved MM-507 test surfaces before story-specific red-first tests are added.

**Checkpoint**: Reusable MM-507 verification scaffolding is ready and story-specific red-first tests can begin.

---

## Phase 3: Story - Preserve Durable Retrieval Truth Across Session Resets

**Summary**: As MoonMind durability logic, I want managed-session retrieval state to remain authoritative in durable refs, artifacts, and bounded metadata so that session resets and new epochs do not turn transient session memory into the source of truth.

**Independent Test**: Start a managed-session workflow that performs retrieval, publish retrieval output behind artifacts or refs, then reset or replace the session epoch. Verify authoritative retrieval truth remains recoverable from durable MoonMind surfaces rather than session-local cache state, large retrieved bodies stay out of durable workflow payloads, the next step can rerun retrieval or reattach the latest context pack ref, and generated evidence preserves the Jira reference `MM-507`.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, acceptance scenarios 1 through 6, DESIGN-REQ-005, DESIGN-REQ-011, DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-017, DESIGN-REQ-023

**Test Plan**:

- Unit: durable artifact authority, compact metadata discipline, reconcile/reset preservation, latest-context recovery selection, and runtime-neutral continuity wording.
- Integration: managed-session reset/epoch workflow behavior, durable artifact reuse, rerun-or-reattach recovery, and cross-runtime continuity semantics.

### Unit Tests (write first) ⚠️

> **NOTE: Write these tests FIRST. Run them, confirm they FAIL for the expected reason, then implement only enough code to make them pass.**

- [X] T007 [P] Add failing unit test coverage for FR-001, DESIGN-REQ-005, DESIGN-REQ-012, acceptance scenario 1, and SC-001 in `tests/unit/rag/test_context_injection.py` and `tests/unit/workflows/temporal/test_agent_runtime_activities.py` to prove durable artifact/ref-backed retrieval truth stays authoritative over session-local cache state.
- [ ] T008 [P] Add failing unit test coverage for FR-002, DESIGN-REQ-011, acceptance scenario 2, and SC-002 in `tests/unit/rag/test_context_injection.py` and `tests/unit/rag/test_context_pack.py` to prove large retrieved bodies stay behind artifacts/refs and compact metadata remains bounded.
- [X] T009 [P] Add failing unit test coverage for FR-003, DESIGN-REQ-013, acceptance scenario 3, and SC-003 in `tests/unit/services/temporal/runtime/test_managed_session_controller.py` to prove reset or session-epoch replacement preserves durable retrieval evidence.
- [X] T010 [P] Add failing unit test coverage for FR-004, DESIGN-REQ-017, acceptance scenario 4, and SC-004 in `tests/unit/services/temporal/runtime/test_managed_session_controller.py` and `tests/unit/services/temporal/runtime/test_launcher.py` to prove the next step can recover by rerunning retrieval or reattaching the latest durable context ref.
- [ ] T011 [P] Add failing unit test coverage for FR-005, DESIGN-REQ-023, acceptance scenario 5, and SC-005 in `tests/unit/workflows/temporal/runtime/strategies/test_remaining_strategies.py` and `tests/unit/services/temporal/runtime/test_launcher.py` to prove durability semantics remain runtime-neutral across managed runtimes.
- [X] T012 Run the unit test command from `specs/255-managed-session-retrieval-durability/quickstart.md` to confirm T007-T011 fail for the expected reason before any production changes.

### Integration Tests (write first) ⚠️

- [X] T013 [P] Add a failing integration test in `tests/integration/workflows/temporal/test_managed_session_retrieval_durability.py` for FR-001, FR-002, FR-003, acceptance scenarios 1 through 3, SC-001 through SC-003, and DESIGN-REQ-005 / DESIGN-REQ-011 / DESIGN-REQ-012 / DESIGN-REQ-013 covering compact durable publication and preservation across reset or new session epochs.
- [ ] T014 [P] Add a failing integration test in `tests/integration/workflows/temporal/test_managed_session_retrieval_durability.py` for FR-004, FR-005, acceptance scenarios 4 and 5, SC-004, SC-005, and DESIGN-REQ-017 / DESIGN-REQ-023 covering rerun-or-reattach recovery and runtime-neutral continuity semantics.
- [X] T015 Run `pytest tests/integration/workflows/temporal/test_managed_session_retrieval_durability.py -q --tb=short` to confirm T013-T014 fail for the expected reason before implementation.

### Red-First Confirmation ⚠️

- [X] T016 Record the intended red-first failure evidence from T012 and T015 in MM-507 implementation notes or verification notes so final verification can distinguish already-correct behavior from newly completed reset-era durability work.

### Conditional Fallback Implementation (implemented_unverified rows)

- [ ] T017 If T008, T013, or T014 proves compact publication is insufficient after continuity changes, update `moonmind/rag/context_injection.py` and `moonmind/rag/context_pack.py` for FR-002, acceptance scenario 2, SC-002, and DESIGN-REQ-011 to keep large retrieved bodies behind artifacts/refs while preserving bounded metadata.

### Implementation

- [X] T018 Implement durable-truth authority handling for FR-001, acceptance scenario 1, SC-001, and DESIGN-REQ-005 / DESIGN-REQ-012 in `moonmind/rag/context_injection.py`, `moonmind/schemas/agent_runtime_models.py`, and `moonmind/workflows/temporal/activity_runtime.py`.
- [X] T019 Implement reset and session-epoch preservation behavior for FR-003, acceptance scenario 3, SC-003, and DESIGN-REQ-013 in `moonmind/workflows/temporal/runtime/managed_session_controller.py`, `moonmind/workflows/temporal/runtime/managed_session_supervisor.py`, and any affected managed-session record or metadata helpers.
- [X] T020 Implement latest-context recovery behavior for FR-004, acceptance scenario 4, SC-004, and DESIGN-REQ-017 in `moonmind/workflows/temporal/runtime/managed_session_controller.py`, `moonmind/workflows/temporal/runtime/launcher.py`, and any affected runtime metadata or recovery helpers.
- [ ] T021 Implement runtime-neutral continuity semantics for FR-005, acceptance scenario 5, SC-005, and DESIGN-REQ-023 in `moonmind/workflows/temporal/runtime/strategies/codex_cli.py`, `moonmind/workflows/temporal/runtime/strategies/claude_code.py`, and shared helper surfaces touched by T018-T020.
- [X] T022 Run the targeted unit and integration commands from `specs/255-managed-session-retrieval-durability/quickstart.md` and fix failures until T007-T021 satisfy FR-001 through FR-005 and the in-scope DESIGN-REQ rows.
- [X] T023 Run the MM-507 story validation flow from `specs/255-managed-session-retrieval-durability/quickstart.md`, including `rg -n "MM-507" specs/255-managed-session-retrieval-durability`, to confirm story validation and traceability for FR-006, acceptance scenario 6, and SC-006.

**Checkpoint**: The story is fully functional, covered by unit and integration tests, and independently validated.

---

## Phase 4: Polish & Verification

**Purpose**: Strengthen the completed story without changing scope.

- [ ] T024 [P] Refresh `specs/255-managed-session-retrieval-durability/plan.md`, `research.md`, `data-model.md`, `quickstart.md`, and `contracts/managed-session-retrieval-durability-contract.md` if implementation changes the verified requirement statuses or recovery contract details.
- [ ] T025 [P] Expand edge-case unit coverage in `tests/unit/rag/test_context_injection.py` and `tests/unit/services/temporal/runtime/test_managed_session_controller.py` for missing continuity cache state, stale artifact refs, and rerun-versus-reattach selection.
- [X] T026 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/rag/test_context_pack.py tests/unit/rag/test_context_injection.py tests/unit/services/temporal/runtime/test_managed_session_controller.py tests/unit/services/temporal/runtime/test_launcher.py tests/unit/workflows/temporal/runtime/strategies/test_remaining_strategies.py tests/unit/workflows/temporal/test_agent_runtime_activities.py` for final unit verification.
- [X] T027 Run `./tools/test_integration.sh` when hermetic integration coverage applies, or run `pytest tests/integration/workflows/temporal/test_managed_session_retrieval_durability.py -q --tb=short` and record the exact runtime blocker if Docker or Temporal infrastructure is unavailable.
- [X] T028 Run the quickstart validation in `specs/255-managed-session-retrieval-durability/quickstart.md` and capture any operator-facing prerequisite updates needed for MM-507.
- [ ] T029 Run `/moonspec-verify` / `/speckit.verify` for `specs/255-managed-session-retrieval-durability/spec.md` and write final verification evidence to `specs/255-managed-session-retrieval-durability/verification.md`.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup completion and blocks story-specific red-first tests until reusable continuity fixtures are ready.
- **Story (Phase 3)**: Depends on Phase 2 completion and red-first verification.
- **Polish (Phase 4)**: Depends on story validation and passing targeted tests.

### Within The Story

- T007-T011 must be written before T012.
- T013-T014 must be written before T015.
- T012 and T015 must confirm red-first failures before any implementation work begins.
- T017 is conditional and runs only if verification proves the current compact publication path is insufficient after reset or epoch changes.
- T018 must land before T021 because runtime-neutral continuity semantics depend on the durable-truth contract.
- T019 and T020 both modify managed-session runtime lifecycle files and should run sequentially.
- T022 depends on the completion of all required implementation tasks.
- T023 depends on T022.

### Parallel Opportunities

- T003 can run in parallel with T002.
- T004 and T005 can run in parallel because they touch different test surfaces.
- T007-T011 can be authored in parallel because they target different files or different assertions within the same suite.
- T013 and T014 can be authored in parallel if they are kept in separate scenario blocks within `tests/integration/workflows/temporal/test_managed_session_retrieval_durability.py`.
- T024 and T025 can run in parallel after story validation is complete.

## Parallel Example: Story Phase

```bash
# Launch red-first unit coverage together:
Task: "Add failing durable-truth authority tests in tests/unit/rag/test_context_injection.py and tests/unit/workflows/temporal/test_agent_runtime_activities.py"
Task: "Add failing reset-preservation tests in tests/unit/services/temporal/runtime/test_managed_session_controller.py"
Task: "Add failing runtime-neutral continuity tests in tests/unit/workflows/temporal/runtime/strategies/test_remaining_strategies.py and tests/unit/services/temporal/runtime/test_launcher.py"
```

## Implementation Strategy

### Verification-First Story Delivery

1. Confirm the active MM-507 artifacts and current retrieval/runtime continuity boundaries.
2. Prepare the shared continuity and reset test scaffolding.
3. Write unit and integration verification tests and run them to confirm the intended failures.
4. Apply the conditional compact-publication fallback only if verification proves it is needed.
5. Implement durable-truth authority, reset preservation, recovery behavior, and runtime-neutral continuity semantics.
6. Re-run the targeted unit and integration commands until the MM-507 story passes.
7. Validate the quickstart flow and MM-507 traceability.
8. Run final unit verification, the required integration path, and `/moonspec-verify` / `/speckit.verify`.

## Notes

- This task list covers one story only.
- `moonspec-breakdown` is not applicable because MM-507 is already a single-story Jira preset brief.
- T017 is the only conditional fallback implementation task because FR-002 / DESIGN-REQ-011 is the implemented-unverified compact-publication row in `plan.md`.
- Preserve `MM-507` in all downstream evidence and verification artifacts.
