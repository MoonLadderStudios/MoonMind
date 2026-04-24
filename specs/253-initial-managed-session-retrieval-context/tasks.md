# Tasks: Initial Managed-Session Retrieval Context

**Input**: Design documents from `specs/253-initial-managed-session-retrieval-context/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/managed-session-retrieval-context-contract.md

**Tests**: Unit tests and integration/workflow-boundary tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement production changes only where the new verification exposes a real MM-505 gap.

**Organization**: Tasks are grouped by phase around the single MM-505 story so the work stays focused, traceable, and independently testable.

**Source Traceability**: The original MM-505 Jira preset brief is preserved in `spec.md`. Tasks cover exactly one story and map FR-001 through FR-006, SCN-001 through SCN-006, SC-001 through SC-006, and DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-008, DESIGN-REQ-011, DESIGN-REQ-017, and DESIGN-REQ-025. Requirement-status summary from `plan.md`: 7 partial rows require code-and-test work if verification fails, 2 implemented-unverified rows require verification-first coverage with conditional fallback implementation, and 5 implemented-verified rows require traceability-preserving final validation only.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/rag/test_context_pack.py tests/unit/rag/test_service.py tests/unit/rag/test_context_injection.py tests/unit/workflows/temporal/runtime/strategies/test_remaining_strategies.py tests/unit/workflows/temporal/test_agent_runtime_activities.py tests/unit/services/temporal/runtime/test_launcher.py`
- Integration tests: `./tools/test_integration.sh`; targeted workflow-boundary command `pytest tests/integration/workflows/temporal/test_managed_session_retrieval_context.py -q --tb=short`
- Final verification: `/moonspec-verify` / `/speckit.verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the active MM-505 artifacts and existing retrieval/runtime verification surfaces before writing new tests.

- [X] T001 Verify the active feature artifacts exist in `specs/253-initial-managed-session-retrieval-context/spec.md`, `plan.md`, `research.md`, `data-model.md`, `quickstart.md`, and `contracts/managed-session-retrieval-context-contract.md` for FR-006 and SCN-006 traceability.
- [X] T002 Inspect existing retrieval/runtime code and current verification surfaces in `moonmind/rag/context_injection.py`, `moonmind/rag/service.py`, `moonmind/workflows/temporal/runtime/strategies/codex_cli.py`, `moonmind/workflows/temporal/activity_runtime.py`, `tests/unit/rag/test_context_injection.py`, `tests/unit/workflows/temporal/runtime/strategies/test_remaining_strategies.py`, `tests/unit/workflows/temporal/test_agent_runtime_activities.py`, and `tests/unit/services/temporal/runtime/test_launcher.py` to choose extension points for FR-001 through FR-005 and DESIGN-REQ coverage.
- [X] T003 [P] Create `tests/integration/workflows/temporal/test_managed_session_retrieval_context.py` if it does not already exist and reserve it for MM-505 workflow-boundary verification covering SCN-001 through SCN-005 and DESIGN-REQ-002 / DESIGN-REQ-025.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish failing verification-first tests before any production implementation work begins.

**CRITICAL**: No production implementation work can begin until these red-first tests are written and confirmed failing for the intended MM-505 gaps.

- [X] T004 [P] Add failing unit tests in `tests/unit/rag/test_context_injection.py` for FR-003, FR-004, SCN-003, SCN-004, SC-003, SC-004, DESIGN-REQ-002, DESIGN-REQ-006, DESIGN-REQ-008, and DESIGN-REQ-011 covering durable artifact publication, compact startup context handling, and untrusted retrieved-text framing.
- [X] T005 [P] Add failing unit tests in `tests/unit/workflows/temporal/runtime/strategies/test_remaining_strategies.py` and `tests/unit/services/temporal/runtime/test_launcher.py` for FR-001, FR-005, SCN-001, SCN-005, SC-001, SC-005, DESIGN-REQ-001, and DESIGN-REQ-017 covering pre-command retrieval ordering and shared runtime contract reuse.
- [X] T006 [P] Add failing unit tests in `tests/unit/workflows/temporal/test_agent_runtime_activities.py` and `tests/unit/rag/test_service.py` for FR-002, FR-004, SCN-002, SCN-004, SC-002, DESIGN-REQ-005, DESIGN-REQ-006, and DESIGN-REQ-025 covering lean retrieval-path behavior, deterministic `ContextPack` assembly, adapter input preparation, and direct/gateway policy neutrality.
- [X] T007 [P] Add a failing workflow-boundary integration test in `tests/integration/workflows/temporal/test_managed_session_retrieval_context.py` for FR-002, FR-003, FR-005, SCN-001 through SCN-005, SC-001 through SC-005, DESIGN-REQ-002, DESIGN-REQ-005, DESIGN-REQ-008, DESIGN-REQ-011, DESIGN-REQ-017, and DESIGN-REQ-025.
- [X] T008 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/rag/test_context_pack.py tests/unit/rag/test_service.py tests/unit/rag/test_context_injection.py tests/unit/workflows/temporal/runtime/strategies/test_remaining_strategies.py tests/unit/workflows/temporal/test_agent_runtime_activities.py tests/unit/services/temporal/runtime/test_launcher.py` and `pytest tests/integration/workflows/temporal/test_managed_session_retrieval_context.py -q --tb=short` to confirm T004-T007 fail for the intended missing or under-verified behavior.

**Checkpoint**: Red-first verification exists and fails for the intended MM-505 gaps.

---

## Phase 3: Story - Publish Initial Retrieval Context For Managed Sessions

**Summary**: As a workflow operator, I want MoonMind to resolve and publish managed-session retrieval context before runtime execution starts so the managed session begins with the right durable context instead of reconstructing it ad hoc.

**Independent Test**: Start a managed-session step with retrieval enabled and verify MoonMind resolves retrieval settings before runtime execution, performs embedding-backed search and `ContextPack` assembly without an extra chat/completions retrieval hop, persists the retrieved context behind artifacts or refs rather than large workflow payloads, injects the resulting context through the runtime adapter input surface, and preserves MM-505 traceability.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, SCN-001, SCN-002, SCN-003, SCN-004, SCN-005, SCN-006, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-008, DESIGN-REQ-011, DESIGN-REQ-017, DESIGN-REQ-025

**Test Plan**:

- Unit: retrieval packaging, artifact publication, startup ordering, adapter framing, direct/gateway invariants, and compact durable-state checks.
- Integration: managed-session workflow-boundary startup behavior, compact durable publication, reusable runtime contract behavior, and end-to-end traceability.

### Implementation

- [X] T009 [P] If T004 or T007 exposes durable-publication or compact-state gaps, update `moonmind/rag/context_injection.py` and `moonmind/rag/context_pack.py` for FR-003, SCN-003, SC-003, DESIGN-REQ-002, DESIGN-REQ-008, and DESIGN-REQ-011.
- [X] T010 [P] If T005 or T007 exposes shared-runtime contract gaps, update `moonmind/workflows/temporal/runtime/strategies/base.py`, `moonmind/workflows/temporal/runtime/strategies/codex_cli.py`, and `moonmind/workflows/temporal/runtime/strategies/claude_code.py` for FR-001, FR-005, SCN-001, SCN-005, SC-001, SC-005, DESIGN-REQ-001, and DESIGN-REQ-017.
- [X] T011 [P] If T006 or T007 exposes lean-retrieval or transport-policy gaps, update `moonmind/rag/service.py` and `api_service/api/routers/retrieval_gateway.py` for FR-002, SCN-002, SC-002, DESIGN-REQ-005, and DESIGN-REQ-025. Verification proved the existing production path already satisfied this requirement, so no production edit was required.
- [X] T012 If T004 or T006 exposes adapter instruction-boundary gaps, update `moonmind/rag/context_injection.py` and `moonmind/workflows/temporal/activity_runtime.py` for FR-004, SCN-004, SC-004, and DESIGN-REQ-006. The gap was resolved in `moonmind/rag/context_injection.py`; `activity_runtime.py` needed verification only.
- [X] T013 Run the targeted unit and workflow-boundary commands from T008 and fix failures until T004-T007 pass for FR-001 through FR-005 and DESIGN-REQ coverage.
- [X] T014 Run the MM-505 end-to-end validation flow in `specs/253-initial-managed-session-retrieval-context/quickstart.md`, including `rg -n "MM-505" specs/253-initial-managed-session-retrieval-context`, to confirm story validation and traceability for FR-006, SCN-006, and SC-006.

**Checkpoint**: The story is fully validated, any required implementation hardening is complete, and targeted unit plus workflow-boundary tests pass.

---

## Phase 4: Polish & Verification

**Purpose**: Strengthen the completed story without changing scope.

- [X] T015 [P] Refresh `specs/253-initial-managed-session-retrieval-context/plan.md`, `research.md`, `data-model.md`, `quickstart.md`, and `contracts/managed-session-retrieval-context-contract.md` for consistency with the verified MM-505 behavior and requirement statuses.
- [X] T016 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/rag/test_context_pack.py tests/unit/rag/test_service.py tests/unit/rag/test_context_injection.py tests/unit/workflows/temporal/runtime/strategies/test_remaining_strategies.py tests/unit/workflows/temporal/test_agent_runtime_activities.py tests/unit/services/temporal/runtime/test_launcher.py` for final unit verification.
- [X] T017 Run `./tools/test_integration.sh` when hermetic integration coverage applies, or run `pytest tests/integration/workflows/temporal/test_managed_session_retrieval_context.py -q --tb=short` and record the exact runtime blocker if Docker or Temporal infrastructure is unavailable.
- [X] T018 Run `/moonspec-verify` / `/speckit.verify` for `specs/253-initial-managed-session-retrieval-context/spec.md` and write final verification evidence to `specs/253-initial-managed-session-retrieval-context/verification.md`.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup completion and blocks any production code changes.
- **Story (Phase 3)**: Depends on red-first verification from Phase 2.
- **Polish (Phase 4)**: Depends on story validation and passing targeted tests.

### Within The Story

- T004-T007 must be written before T009-T012.
- T008 must confirm red-first failures before any fallback implementation tasks begin.
- T009, T010, and T011 can proceed in parallel after T008 because they touch different files.
- T012 depends on the results of T004/T006 because it may overlap with `moonmind/rag/context_injection.py`.
- T013 depends on any fallback implementation triggered by T009-T012.
- T014 depends on T013.

### Parallel Opportunities

- T003 can run in parallel with T002.
- T004, T005, T006, and T007 can be authored in parallel.
- T009, T010, and T011 can run in parallel after T008 confirms the specific failing gaps.
- T015 can run while T016/T017 are being prepared.

## Parallel Example: Story Phase

```bash
# After T008 confirms the failing gaps, these can run independently:
Task: "If durable publication gaps fail verification, update moonmind/rag/context_injection.py and moonmind/rag/context_pack.py"
Task: "If shared runtime contract gaps fail verification, update moonmind/workflows/temporal/runtime/strategies/base.py, codex_cli.py, and claude_code.py"
Task: "If retrieval transport-policy gaps fail verification, update moonmind/rag/service.py and api_service/api/routers/retrieval_gateway.py"
```

## Implementation Strategy

### Verification-First Story Delivery

1. Confirm the active MM-505 artifacts and current runtime/test surfaces.
2. Write unit and workflow-boundary verification tests and run them to confirm the intended failures.
3. Apply only the fallback implementation tasks required by the failing verification.
4. Rerun the targeted unit and integration commands until the MM-505 story passes.
5. Validate the quickstart flow and MM-505 traceability.
6. Run final unit verification, the required integration path, and `/moonspec-verify` / `/speckit.verify`.

## Notes

- This task list covers one story only.
- `moonspec-breakdown` is not applicable because MM-505 is already a single-story Jira preset brief.
- Fallback implementation tasks T009-T012 are conditional; if the new verification passes without code changes, they are skipped and the task run proceeds to T013-T018.
- Preserve MM-505 in all downstream evidence and verification artifacts.
