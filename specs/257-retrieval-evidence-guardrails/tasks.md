# Tasks: Retrieval Evidence And Trust Guardrails

**Input**: Design documents from `specs/257-retrieval-evidence-guardrails/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `quickstart.md`, `contracts/retrieval-evidence-contract.md`

**Tests**: Unit tests and integration/workflow-boundary tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement production changes only where the new verification exposes a real MM-509 gap.

**Organization**: Tasks are grouped by phase around the single MM-509 story so the work stays focused, traceable, and independently testable.

**Source Traceability**: The original MM-509 Jira preset brief is preserved in `spec.md`. Tasks cover exactly one story and map FR-001 through FR-007, acceptance scenarios 1 through 7, SC-001 through SC-007, and DESIGN-REQ-016, DESIGN-REQ-018, DESIGN-REQ-020, DESIGN-REQ-021, DESIGN-REQ-022, DESIGN-REQ-023, and DESIGN-REQ-025. Requirement-status summary from `plan.md`: 9 partial rows require test-first completion work, 4 implemented-unverified rows require verification-first coverage with conditional fallback implementation tasks, and 1 implemented-verified row requires traceability-preserving final validation only.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/rag/test_context_pack.py tests/unit/rag/test_context_injection.py tests/unit/rag/test_service.py tests/unit/rag/test_guardrails.py tests/unit/rag/test_telemetry.py tests/unit/api/routers/test_retrieval_gateway.py`
- Integration tests: `./tools/test_integration.sh`; targeted workflow-boundary commands `pytest tests/integration/workflows/temporal/test_managed_session_retrieval_context.py -q --tb=short` and `pytest tests/integration/workflows/temporal/test_managed_session_retrieval_durability.py -q --tb=short`
- Final verification: `/moonspec-verify` / `/speckit.verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the active MM-509 artifacts and reserve the exact runtime, API, and test surfaces for retrieval-evidence contract work.

- [ ] T001 Verify the active feature artifacts exist in `specs/257-retrieval-evidence-guardrails/spec.md`, `plan.md`, `research.md`, `data-model.md`, `quickstart.md`, and `contracts/retrieval-evidence-contract.md` for FR-007 and traceability coverage.
- [ ] T002 Inspect the current retrieval evidence and trust-boundary surfaces in `moonmind/rag/context_pack.py`, `moonmind/rag/context_injection.py`, `moonmind/rag/service.py`, `moonmind/rag/guardrails.py`, and `api_service/api/routers/retrieval_gateway.py` to lock the extension points for FR-001 through FR-006 and DESIGN-REQ-016 / DESIGN-REQ-025.
- [ ] T003 [P] Reserve the targeted runtime-boundary suites in `tests/integration/workflows/temporal/test_managed_session_retrieval_context.py` and `tests/integration/workflows/temporal/test_managed_session_retrieval_durability.py` for MM-509 acceptance scenarios 1 through 6 and SC-001 through SC-006.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Prepare only the reusable verification scaffolding the MM-509 story depends on.

**CRITICAL**: No story implementation work can begin until these blocking test scaffolds are ready.

- [ ] T004 [P] Extend reusable retrieval evidence fixtures in `tests/unit/rag/test_context_pack.py` and `tests/unit/rag/test_context_injection.py` so MM-509 can assert artifact refs, degraded reasons, and bounded metadata without duplicating setup for FR-001, FR-003, and FR-005.
- [ ] T005 [P] Extend retrieval gateway and budget-validation fixtures in `tests/unit/api/routers/test_retrieval_gateway.py` and `tests/unit/rag/test_service.py` for FR-004, acceptance scenario 4, and DESIGN-REQ-021 / DESIGN-REQ-025.
- [ ] T006 [P] Extend runtime-boundary retrieval fixtures in `tests/integration/workflows/temporal/test_managed_session_retrieval_context.py` and `tests/integration/workflows/temporal/test_managed_session_retrieval_durability.py` for FR-002, FR-005, FR-006, and DESIGN-REQ-018 / DESIGN-REQ-022 / DESIGN-REQ-023.
- [ ] T007 Confirm the selected unit and targeted integration commands from `specs/257-retrieval-evidence-guardrails/quickstart.md` can execute against the reserved MM-509 test surfaces before story-specific red-first tests are added.

**Checkpoint**: Reusable MM-509 verification scaffolding is ready and story-specific red-first tests can begin.

---

## Phase 3: Story - Record Retrieval Evidence And Enforce Trust Guardrails

**Summary**: As MoonMind operations, I want every retrieval action to publish durable evidence and enforce trust and secret-handling guardrails so that retrieval remains auditable, policy-bounded, and safe across managed runtimes.

**Independent Test**: Execute retrieval through automatic and session-issued paths, including an allowed degraded or fallback case, and verify MoonMind records durable evidence for initiation mode, transport, filters, result counts, budgets, truncation, artifact/ref locations, and degraded reasons when applicable. Confirm retrieved text is injected with untrusted-reference safety framing that prefers current workspace state on conflict, raw provider keys or token-bearing config bodies are absent from durable workflow payloads and retrieval artifacts, session-issued retrieval remains bounded by authorized scope and policy controls, and traceability artifacts preserve `MM-509`.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, SC-007, acceptance scenarios 1 through 7, DESIGN-REQ-016, DESIGN-REQ-018, DESIGN-REQ-020, DESIGN-REQ-021, DESIGN-REQ-022, DESIGN-REQ-023, DESIGN-REQ-025

**Test Plan**:

- Unit: durable evidence fields, trust framing strings, secret exclusion, budget and policy enforcement, explicit retrieval state metadata, and contract serialization.
- Integration: semantic versus degraded retrieval publication, runtime-facing trust framing, policy-bounded retrieval at API/runtime boundaries, artifact/ref durability, and cross-runtime consistency.

### Unit Tests (write first) ⚠️

> **NOTE: Write these tests FIRST. Run them, confirm they FAIL for the expected reason, then implement only enough code to make them pass.**

- [ ] T008 [P] Add failing unit test coverage for FR-001, acceptance scenario 1, SC-001, and DESIGN-REQ-016 in `tests/unit/rag/test_context_pack.py` and `tests/unit/rag/test_context_injection.py` to prove one durable evidence envelope records transport, filters, budgets, usage, publication refs, degraded reason, initiation mode, and truncation state.
- [ ] T009 [P] Add verification-first unit test coverage for FR-002, acceptance scenario 2, SC-002, and DESIGN-REQ-018 in `tests/unit/rag/test_context_injection.py` to prove the full untrusted-reference and current-workspace-preference framing is preserved for semantic and degraded retrieval.
- [ ] T010 [P] Add failing unit test coverage for FR-003, acceptance scenario 3, SC-003, and DESIGN-REQ-020 in `tests/unit/rag/test_context_injection.py` and `tests/unit/rag/test_service.py` to prove provider keys, OAuth tokens, bearer tokens, and secret-bearing config bodies never reach durable retrieval artifacts or metadata.
- [ ] T011 [P] Add failing unit test coverage for FR-004, acceptance scenario 4, SC-004, and DESIGN-REQ-021 / DESIGN-REQ-025 in `tests/unit/api/routers/test_retrieval_gateway.py` and `tests/unit/rag/test_service.py` to prove session-issued retrieval remains bounded by authorized scope, supported filters, budgets, transport policy, provider/secret policy, and audit requirements.
- [ ] T012 [P] Add failing unit test coverage for FR-005, acceptance scenario 5, SC-005, and DESIGN-REQ-022 in `tests/unit/rag/test_context_injection.py` and `tests/unit/rag/test_guardrails.py` to prove enabled, disabled, and degraded retrieval states remain explicit and never masquerade as normal semantic retrieval.
- [ ] T013 [P] Add verification-first unit test coverage for FR-006, acceptance scenario 6, SC-006, and DESIGN-REQ-023 in `tests/unit/rag/test_context_injection.py` and `tests/unit/rag/test_telemetry.py` to prove the shared evidence and trust contract remains runtime-neutral.
- [ ] T014 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/rag/test_context_pack.py tests/unit/rag/test_context_injection.py tests/unit/rag/test_service.py tests/unit/rag/test_guardrails.py tests/unit/rag/test_telemetry.py tests/unit/api/routers/test_retrieval_gateway.py` to confirm T008-T013 fail for the expected reason before any production changes.

### Integration Tests (write first) ⚠️

- [ ] T015 [P] Add a failing integration test in `tests/integration/workflows/temporal/test_managed_session_retrieval_context.py` for FR-001, FR-002, FR-005, acceptance scenarios 1, 2, and 5, SC-001, SC-002, SC-005, and DESIGN-REQ-016 / DESIGN-REQ-018 / DESIGN-REQ-022 covering semantic versus degraded retrieval publication and runtime-facing trust framing.
- [ ] T016 [P] Add a failing integration test in `tests/integration/workflows/temporal/test_managed_session_retrieval_context.py` and `tests/integration/workflows/temporal/test_managed_session_retrieval_durability.py` for FR-003, FR-004, FR-006, acceptance scenarios 3, 4, and 6, SC-003, SC-004, SC-006, and DESIGN-REQ-020 / DESIGN-REQ-021 / DESIGN-REQ-023 / DESIGN-REQ-025 covering secret-safe publication, policy-bounded retrieval, artifact/ref durability, and runtime-neutral metadata.
- [ ] T017 Run `pytest tests/integration/workflows/temporal/test_managed_session_retrieval_context.py -q --tb=short` and `pytest tests/integration/workflows/temporal/test_managed_session_retrieval_durability.py -q --tb=short` to confirm T015-T016 fail for the expected reason before implementation.

### Red-First Confirmation ⚠️

- [ ] T018 Record the intended red-first failure evidence from T014 and T017 in `specs/257-retrieval-evidence-guardrails/verification.md` so final verification can distinguish already-correct behavior from newly completed MM-509 work.

### Conditional Fallback Implementation (implemented_unverified rows)

- [ ] T019 If T009 shows the current trust framing is incomplete at runtime boundaries, update `moonmind/rag/context_injection.py` and any affected runtime boundary helper assertions in `tests/integration/workflows/temporal/test_managed_session_retrieval_context.py` for FR-002, acceptance scenario 2, SC-002, and DESIGN-REQ-018 to preserve the full untrusted-reference and current-workspace-preference contract.
- [ ] T020 If T013 or T016 shows cross-runtime metadata or trust behavior diverges, update `moonmind/rag/context_injection.py`, `moonmind/workflows/temporal/runtime/launcher.py`, and any affected runtime helper surfaces for FR-006, acceptance scenario 6, SC-006, and DESIGN-REQ-023 so Codex and at least one additional runtime publish the same evidence and trust contract.

### Implementation

- [ ] T021 Implement the durable retrieval evidence envelope for FR-001, acceptance scenario 1, SC-001, and DESIGN-REQ-016 in `moonmind/rag/context_pack.py` and `moonmind/rag/context_injection.py`, including initiation-mode and truncation metadata when tests prove they are missing.
- [ ] T022 Implement secret-safe retrieval artifact and metadata handling for FR-003, acceptance scenario 3, SC-003, and DESIGN-REQ-020 in `moonmind/rag/context_injection.py` and `moonmind/rag/service.py` if T010 or T016 exposes secret leakage risk.
- [ ] T023 Implement policy-envelope hardening for FR-004, acceptance scenario 4, SC-004, and DESIGN-REQ-021 / DESIGN-REQ-025 in `api_service/api/routers/retrieval_gateway.py` and `moonmind/rag/service.py` if T011 or T016 exposes scope, budget, transport-policy, or audit-boundary gaps.
- [ ] T024 Implement explicit enabled/disabled/degraded retrieval-state visibility for FR-005, acceptance scenario 5, SC-005, and DESIGN-REQ-022 in `moonmind/rag/context_injection.py` and any affected runtime-boundary metadata surfaces if T012 or T015 exposes ambiguity.
- [ ] T025 Run the targeted unit and integration commands from `specs/257-retrieval-evidence-guardrails/quickstart.md` and fix failures until T008-T024 satisfy FR-001 through FR-006 and the in-scope DESIGN-REQ rows.
- [ ] T026 Run the MM-509 story validation flow from `specs/257-retrieval-evidence-guardrails/quickstart.md`, including `rg -n "MM-509" specs/257-retrieval-evidence-guardrails`, to confirm story validation and traceability for FR-007, acceptance scenario 7, and SC-007.

**Checkpoint**: The story is fully functional, covered by unit and integration tests, and independently validated.

---

## Phase 4: Polish & Verification

**Purpose**: Strengthen the completed story without changing scope.

- [ ] T027 [P] Refresh `specs/257-retrieval-evidence-guardrails/plan.md`, `research.md`, `data-model.md`, `quickstart.md`, and `contracts/retrieval-evidence-contract.md` if implementation changes the verified requirement statuses or evidence contract details.
- [ ] T028 [P] Expand edge-case unit coverage in `tests/unit/rag/test_context_injection.py` and `tests/unit/rag/test_service.py` for zero-result retrieval, disabled retrieval, unsupported budget keys, and truncation-state visibility.
- [ ] T029 [P] Expand integration coverage in `tests/integration/workflows/temporal/test_managed_session_retrieval_context.py` and `tests/integration/workflows/temporal/test_managed_session_retrieval_durability.py` for policy-denied retrieval and degraded local-fallback operator visibility.
- [ ] T030 Run the quickstart validation in `specs/257-retrieval-evidence-guardrails/quickstart.md` and capture any operator-facing prerequisite updates needed for MM-509.
- [ ] T031 Run `/moonspec-verify` / `/speckit.verify` for `specs/257-retrieval-evidence-guardrails/spec.md` and write final verification evidence to `specs/257-retrieval-evidence-guardrails/verification.md`.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup completion and blocks story-specific red-first tests until reusable fixtures are ready.
- **Story (Phase 3)**: Depends on Phase 2 completion and red-first verification.
- **Polish (Phase 4)**: Depends on story validation and passing targeted tests.

### Within The Story

- T008-T013 must be written before T014.
- T015-T016 must be written before T017.
- T014 and T017 must confirm red-first failures before any implementation work begins.
- T019 and T020 are conditional and run only if the verification-first tests for `implemented_unverified` rows expose a real gap.
- T021-T024 run only after the relevant red-first failures are confirmed.
- T025 depends on completion of all required implementation tasks and any triggered conditional fallback tasks.
- T026 depends on T025.

### Parallel Opportunities

- T003 can run in parallel with T002.
- T004-T006 can run in parallel because they prepare different reusable test surfaces.
- T008-T013 can be authored in parallel when they touch different files.
- T015 and T016 can run in parallel because they target different scenario groupings in different integration assertions.
- T027-T029 can run in parallel after story validation is complete.

## Parallel Example: Story Phase

```bash
# Launch red-first unit coverage together:
Task: "Add failing durable-evidence tests in tests/unit/rag/test_context_pack.py and tests/unit/rag/test_context_injection.py"
Task: "Add failing secret-exclusion and policy-envelope tests in tests/unit/rag/test_service.py and tests/unit/api/routers/test_retrieval_gateway.py"
Task: "Add verification-first trust-framing and cross-runtime contract tests in tests/unit/rag/test_context_injection.py and tests/unit/rag/test_telemetry.py"
```

## Implementation Strategy

### Verification-First Story Delivery

1. Confirm the active MM-509 artifacts and current retrieval runtime boundaries.
2. Prepare the shared unit and integration scaffolding for evidence, trust framing, and policy checks.
3. Write unit and integration verification tests and run them to confirm the intended failures.
4. Apply the conditional fallback work only if verification-first tests for FR-002 or FR-006 expose real gaps.
5. Implement the remaining durable evidence, secret-handling, policy-envelope, and explicit-state changes required by failing tests.
6. Re-run the targeted unit and integration commands until the MM-509 story passes.
7. Validate the quickstart flow and MM-509 traceability.
8. Run final polish coverage and `/moonspec-verify` / `/speckit.verify`.

## Notes

- This task list covers one story only.
- `moonspec-breakdown` is not applicable because MM-509 is already a single-story Jira preset brief.
- T019 and T020 are the only conditional fallback implementation tasks because FR-002 / DESIGN-REQ-018 and FR-006 / DESIGN-REQ-023 are the implemented-unverified rows in `plan.md` that need verification-first handling.
- Preserve `MM-509` in all downstream evidence and verification artifacts.
