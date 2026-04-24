# Tasks: Managed-Session Follow-Up Retrieval

**Input**: Design documents from `specs/254-managed-session-followup-retrieval/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `quickstart.md`, `contracts/managed-session-followup-retrieval-contract.md`

**Tests**: Unit tests and integration/workflow-boundary tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement production changes only where the new verification exposes a real MM-506 gap.

**Organization**: Tasks are grouped by phase around the single MM-506 story so the work stays focused, traceable, and independently testable.

**Source Traceability**: The original MM-506 Jira preset brief is preserved in `spec.md`. Tasks cover exactly one story and map FR-001 through FR-007, the seven acceptance scenarios in `spec.md`, SC-001 through SC-006, and DESIGN-REQ-003, DESIGN-REQ-007, DESIGN-REQ-015, DESIGN-REQ-019, DESIGN-REQ-020, DESIGN-REQ-023, and DESIGN-REQ-025. Requirement-status summary from `plan.md`: 2 missing rows require new contract and test work, 10 partial rows require code-and-test work, 1 implemented-unverified row requires verification-first coverage with a conditional fallback implementation task, and 1 implemented-verified row requires traceability-preserving final validation only.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_retrieval_gateway.py tests/unit/rag/test_service.py tests/unit/rag/test_context_injection.py tests/unit/agents/codex_worker/test_handlers.py tests/unit/workflows/temporal/test_agent_runtime_activities.py tests/unit/services/temporal/runtime/test_launcher.py`
- Integration tests: `./tools/test_integration.sh`; targeted workflow-boundary command `pytest tests/integration/workflows/temporal/test_managed_session_followup_retrieval.py -q --tb=short`
- Final verification: `/moonspec-verify` / `/speckit.verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the active MM-506 artifacts and choose the exact runtime and retrieval boundaries to extend before writing new tests.

- [ ] T001 Verify the active feature artifacts exist in `specs/254-managed-session-followup-retrieval/spec.md`, `plan.md`, `research.md`, `data-model.md`, `quickstart.md`, and `contracts/managed-session-followup-retrieval-contract.md` for FR-007 and acceptance scenario traceability.
- [ ] T002 Inspect the current follow-up retrieval surfaces in `api_service/api/routers/retrieval_gateway.py`, `moonmind/rag/service.py`, `moonmind/rag/context_pack.py`, `moonmind/rag/context_injection.py`, `moonmind/agents/codex_worker/handlers.py`, and `moonmind/workflows/temporal/activity_runtime.py` to lock the extension points for FR-001 through FR-006 and DESIGN-REQ-003 / DESIGN-REQ-025.
- [ ] T003 [P] Reserve `tests/integration/workflows/temporal/test_managed_session_followup_retrieval.py` for MM-506 runtime-boundary verification covering the acceptance scenarios, SC-001 through SC-005, and DESIGN-REQ-003, DESIGN-REQ-007, DESIGN-REQ-015, DESIGN-REQ-019, DESIGN-REQ-020, DESIGN-REQ-023, and DESIGN-REQ-025.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish failing verification-first tests before any production implementation work begins.

**CRITICAL**: No production implementation work can begin until these red-first tests are written and confirmed failing for the intended MM-506 gaps.

- [ ] T004 [P] Add failing unit tests in `tests/unit/agents/codex_worker/test_handlers.py`, `tests/unit/workflows/temporal/test_agent_runtime_activities.py`, and `tests/unit/services/temporal/runtime/test_launcher.py` for FR-001, FR-006, SC-001, acceptance scenarios 1 and 6, DESIGN-REQ-019, DESIGN-REQ-023, and DESIGN-REQ-025 covering the runtime-facing capability signal, reference-data notice, and runtime-neutral semantics.
- [ ] T005 [P] Add failing unit tests in `tests/unit/api/routers/test_retrieval_gateway.py` and `tests/unit/rag/test_service.py` for FR-002, FR-003, FR-005, SC-002 through SC-005, SC-002 through SC-004, DESIGN-REQ-003, DESIGN-REQ-007, DESIGN-REQ-015, and DESIGN-REQ-020 covering MoonMind-owned routing, bounded request validation, successful response contracts, and deterministic disabled or invalid-request denials.
- [ ] T006 [P] Add failing unit tests in `tests/unit/rag/test_context_injection.py` and `tests/unit/rag/test_service.py` for FR-004, SC-003, SC-004, acceptance scenarios 3 and 4, and DESIGN-REQ-025 covering `ContextPack` metadata, `context_text`, transport metadata, and compact observability evidence for session-initiated retrieval.
- [ ] T007 [P] Add a failing workflow-boundary integration test in `tests/integration/workflows/temporal/test_managed_session_followup_retrieval.py` for FR-001 through FR-006, acceptance scenarios 1 through 6, and SC-001 through SC-005 proving the capability signal reaches the runtime boundary, follow-up retrieval stays MoonMind-owned, and disabled retrieval fails fast with a stable reason.
- [ ] T008 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_retrieval_gateway.py tests/unit/rag/test_service.py tests/unit/rag/test_context_injection.py tests/unit/agents/codex_worker/test_handlers.py tests/unit/workflows/temporal/test_agent_runtime_activities.py tests/unit/services/temporal/runtime/test_launcher.py` and `pytest tests/integration/workflows/temporal/test_managed_session_followup_retrieval.py -q --tb=short` to confirm T004-T007 fail for the intended missing, partial, or under-verified MM-506 behavior.

**Checkpoint**: Red-first verification exists and fails for the intended MM-506 gaps.

---

## Phase 3: Story - Allow Managed Sessions To Request Follow-Up Retrieval

**Summary**: As a managed session runtime, I want to request additional retrieval through MoonMind-owned surfaces during execution so that later turns can receive authorized context without bypassing MoonMind policy and runtime boundaries.

**Independent Test**: Start a managed-session run with follow-up retrieval enabled and verify the runtime receives explicit capability guidance, issues a retrieval request through the MoonMind-owned retrieval surface, gets `ContextPack` metadata plus text output within policy bounds, and fails fast with a clear reason when the feature is disabled or the request exceeds the permitted retrieval contract. Confirm generated artifacts and verification output preserve the Jira reference `MM-506`.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, acceptance scenarios 1 through 7, DESIGN-REQ-003, DESIGN-REQ-007, DESIGN-REQ-015, DESIGN-REQ-019, DESIGN-REQ-020, DESIGN-REQ-023, DESIGN-REQ-025

**Test Plan**:

- Unit: capability signalling, request validation, fail-fast behavior, response-shape guarantees, transport metadata, runtime-neutral wording, and edge-case denial handling.
- Integration: managed-session runtime-boundary delivery, MoonMind-owned routing, transport-neutral semantics, and traceability-preserving story validation.

### Unit Tests (write first) ⚠️

> **NOTE: Write these tests FIRST. Run them, confirm they FAIL for the expected reason, then implement only enough code to make them pass.**

- [ ] T009 [P] Add failing unit test coverage for FR-001, FR-006, acceptance scenarios 1 and 6, and DESIGN-REQ-019 / DESIGN-REQ-023 in `tests/unit/agents/codex_worker/test_handlers.py` and `tests/unit/workflows/temporal/test_agent_runtime_activities.py` to prove a managed runtime receives an explicit follow-up retrieval capability signal.
- [ ] T010 [P] Add failing unit test coverage for FR-002, FR-003, FR-005, acceptance scenarios 2, 4, and 5, and DESIGN-REQ-007 / DESIGN-REQ-015 / DESIGN-REQ-020 in `tests/unit/api/routers/test_retrieval_gateway.py` and `tests/unit/rag/test_service.py` to prove valid requests are accepted only through MoonMind-owned routing and invalid or disabled requests fail fast.
- [ ] T011 [P] Add failing unit test coverage for FR-004, acceptance scenario 3, SC-003, and DESIGN-REQ-025 in `tests/unit/rag/test_context_injection.py` and `tests/unit/rag/test_service.py` to prove successful follow-up retrieval returns both machine-readable and text output with compact evidence.
- [ ] T012 Run the unit test command from T008 to confirm T009-T011 fail for the expected reason before any production changes.

### Integration Tests (write first) ⚠️

- [ ] T013 [P] Add a failing integration test for acceptance scenarios 1 through 5 and SC-001 through SC-005 in `tests/integration/workflows/temporal/test_managed_session_followup_retrieval.py` covering capability signalling, MoonMind-owned follow-up retrieval routing, successful retrieval output, and disabled retrieval denial.
- [ ] T014 [P] Add a failing integration test for acceptance scenario 6 and DESIGN-REQ-023 / DESIGN-REQ-025 in `tests/integration/workflows/temporal/test_managed_session_followup_retrieval.py` covering runtime-neutral semantics across the managed runtime boundary.
- [ ] T015 Run `pytest tests/integration/workflows/temporal/test_managed_session_followup_retrieval.py -q --tb=short` to confirm T013-T014 fail for the expected reason before implementation.

### Red-First Confirmation ⚠️

- [ ] T016 Record the intended red-first failures from T012 and T015 in `specs/254-managed-session-followup-retrieval/tasks.md` task notes or implementation log output so the eventual MM-506 verification can distinguish missing behavior from already-correct code.

### Conditional Fallback Implementation (implemented_unverified rows)

- [ ] T017 If T011, T013, or T014 shows the existing `ContextPack` response contract is insufficient, update `moonmind/rag/context_pack.py` and `api_service/api/routers/retrieval_gateway.py` for FR-004, acceptance scenario 3, and DESIGN-REQ-025 to align the successful response shape and compact observability evidence.

### Implementation

- [ ] T018 Implement the runtime-facing capability signal for FR-001, FR-006, acceptance scenarios 1 and 6, and DESIGN-REQ-019 / DESIGN-REQ-023 in `moonmind/agents/codex_worker/handlers.py` and `moonmind/workflows/temporal/activity_runtime.py`.
- [ ] T019 Implement the managed-session follow-up retrieval surface for FR-002, acceptance scenario 2, and DESIGN-REQ-003 / DESIGN-REQ-007 in `api_service/api/routers/retrieval_gateway.py`, `moonmind/rag/service.py`, and `moonmind/rag/guardrails.py`.
- [ ] T020 Implement bounded request validation and deterministic disabled-request denials for FR-003, FR-005, acceptance scenarios 4 and 5, and DESIGN-REQ-015 / DESIGN-REQ-020 in `api_service/api/routers/retrieval_gateway.py`, `moonmind/rag/service.py`, and `moonmind/rag/settings.py`.
- [ ] T021 Implement runtime-boundary response wiring and compact retrieval evidence for FR-004, acceptance scenario 3, and DESIGN-REQ-025 in `moonmind/rag/context_injection.py`, `moonmind/rag/context_pack.py`, and `moonmind/agents/codex_worker/handlers.py`.
- [ ] T022 Preserve runtime-neutral semantics for FR-006, acceptance scenario 6, and DESIGN-REQ-023 in `moonmind/agents/codex_worker/handlers.py`, `moonmind/workflows/temporal/activity_runtime.py`, and any shared retrieval-boundary helpers touched by T018-T021.
- [ ] T023 Run the targeted unit and integration commands from T008 and T015 and fix failures until T009-T022 satisfy FR-001 through FR-006 and the in-scope DESIGN-REQ rows.
- [ ] T024 Run the MM-506 validation flow from `specs/254-managed-session-followup-retrieval/quickstart.md`, including `rg -n "MM-506" specs/254-managed-session-followup-retrieval`, to confirm story validation and traceability for FR-007, acceptance scenario 7, and SC-006.

**Checkpoint**: The story is fully functional, covered by unit and integration tests, and independently validated.

---

## Phase 4: Polish & Verification

**Purpose**: Strengthen the completed story without changing scope.

- [ ] T025 [P] Refresh `specs/254-managed-session-followup-retrieval/plan.md`, `research.md`, `data-model.md`, `quickstart.md`, and `contracts/managed-session-followup-retrieval-contract.md` if implementation changes the verified requirement statuses or contract details.
- [ ] T026 [P] Expand edge-case unit coverage in `tests/unit/api/routers/test_retrieval_gateway.py` and `tests/unit/rag/test_service.py` for disabled retrieval, unsupported fields, empty-result retrieval, and bounded budget override handling.
- [ ] T027 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_retrieval_gateway.py tests/unit/rag/test_service.py tests/unit/rag/test_context_injection.py tests/unit/agents/codex_worker/test_handlers.py tests/unit/workflows/temporal/test_agent_runtime_activities.py tests/unit/services/temporal/runtime/test_launcher.py` for final unit verification.
- [ ] T028 Run `./tools/test_integration.sh` when hermetic integration coverage applies, or run `pytest tests/integration/workflows/temporal/test_managed_session_followup_retrieval.py -q --tb=short` and record the exact runtime blocker if Docker or Temporal infrastructure is unavailable.
- [ ] T029 Run the quickstart validation in `specs/254-managed-session-followup-retrieval/quickstart.md` and capture any operator-facing prerequisite updates needed for MM-506.
- [ ] T030 Run `/moonspec-verify` / `/speckit.verify` for `specs/254-managed-session-followup-retrieval/spec.md` and write final verification evidence to `specs/254-managed-session-followup-retrieval/verification.md`.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup completion and blocks any production code changes.
- **Story (Phase 3)**: Depends on red-first verification from Phase 2.
- **Polish (Phase 4)**: Depends on story validation and passing targeted tests.

### Within The Story

- T009-T011 must be written before T012.
- T013-T014 must be written before T015.
- T012 and T015 must confirm red-first failures before any implementation work begins.
- T017 is conditional and runs only if verification proves the existing response contract is insufficient.
- T018 must land before T022 because runtime-neutral semantics depend on the capability-signal surface.
- T019 and T020 both modify retrieval-boundary files and should run sequentially.
- T021 depends on the contract decisions from T017-T020.
- T023 depends on the completion of all required implementation tasks.
- T024 depends on T023.

### Parallel Opportunities

- T003 can run in parallel with T002.
- T004, T005, T006, and T007 can be authored in parallel.
- T009, T010, and T011 can be authored in parallel because they touch different test files.
- T013 and T014 can be authored in parallel within the same integration test file only if they are split cleanly by scenario blocks.
- T025 and T026 can run in parallel after story validation is complete.

## Parallel Example: Story Phase

```bash
# Launch unit-test authoring together:
Task: "Add failing capability-signal unit tests in tests/unit/agents/codex_worker/test_handlers.py and tests/unit/workflows/temporal/test_agent_runtime_activities.py"
Task: "Add failing request-validation unit tests in tests/unit/api/routers/test_retrieval_gateway.py and tests/unit/rag/test_service.py"
Task: "Add failing response-contract unit tests in tests/unit/rag/test_context_injection.py and tests/unit/rag/test_service.py"
```

## Implementation Strategy

### Verification-First Story Delivery

1. Confirm the active MM-506 artifacts and current retrieval/runtime boundaries.
2. Write unit and integration verification tests and run them to confirm the intended failures.
3. Apply the conditional fallback response-shape implementation only if verification proves it is needed.
4. Implement the capability signal, MoonMind-owned routing, bounded validation, fail-fast behavior, and runtime-neutral response wiring.
5. Rerun the targeted unit and integration commands until the MM-506 story passes.
6. Validate the quickstart flow and MM-506 traceability.
7. Run final unit verification, the required integration path, and `/moonspec-verify` / `/speckit.verify`.

## Notes

- This task list covers one story only.
- `moonspec-breakdown` is not applicable because MM-506 is already a single-story Jira preset brief.
- T017 is the only conditional fallback implementation task because FR-004 is the sole `implemented_unverified` row in `plan.md`.
- Preserve MM-506 in all downstream evidence and verification artifacts.
