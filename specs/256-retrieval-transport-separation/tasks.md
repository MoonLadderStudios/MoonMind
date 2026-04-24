# Tasks: Retrieval Transport and Configuration Separation

**Input**: Design documents from `specs/256-retrieval-transport-separation/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `quickstart.md`, `contracts/retrieval-transport-contract.md`

**Tests**: Unit tests and integration or workflow-boundary tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement production changes only where the new verification exposes a real MM-508 gap.

**Organization**: Tasks are grouped by phase around the single MM-508 story so the work stays focused, traceable, and independently testable.

**Source Traceability**: The original MM-508 Jira preset brief is preserved in `spec.md`. Tasks cover exactly one story and map FR-001 through FR-007, acceptance scenarios 1 through 6, SC-001 through SC-007, and DESIGN-REQ-004, DESIGN-REQ-009, DESIGN-REQ-010, DESIGN-REQ-014, DESIGN-REQ-016, DESIGN-REQ-019, DESIGN-REQ-024, and DESIGN-REQ-025. Requirement-status summary from `plan.md`: 0 missing rows, 12 partial rows require test-first completion work, 3 implemented-unverified rows require verification-first coverage, and 1 implemented-verified row requires traceability-preserving final validation only.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/rag/test_settings.py tests/unit/rag/test_service.py tests/unit/rag/test_context_injection.py tests/unit/api/routers/test_retrieval_gateway.py tests/unit/services/temporal/runtime/test_launcher.py tests/unit/workflows/temporal/test_agent_runtime_activities.py tests/unit/api_service/api/routers/test_provider_profiles.py`
- Integration tests: `./tools/test_integration.sh`; targeted workflow-boundary command `pytest tests/integration/workflows/temporal/test_managed_session_retrieval_context.py -q --tb=short`
- Final verification: `/moonspec-verify` / `/speckit.verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the active MM-508 artifacts and lock the retrieval, provider-profile, and runtime-boundary surfaces for transport-separation work.

- [ ] T001 Verify the active feature artifacts exist in `specs/256-retrieval-transport-separation/spec.md`, `plan.md`, `research.md`, `data-model.md`, `quickstart.md`, and `contracts/retrieval-transport-contract.md` for FR-007 and traceability coverage.
- [ ] T002 Inspect the current retrieval configuration and transport boundaries in `moonmind/rag/settings.py`, `moonmind/rag/service.py`, `moonmind/rag/context_injection.py`, `api_service/api/routers/retrieval_gateway.py`, `api_service/api/routers/provider_profiles.py`, `api_service/api/routers/executions.py`, and `moonmind/workflows/temporal/runtime/launcher.py` to lock the extension points for FR-001 through FR-006 and the in-scope DESIGN-REQ rows.
- [ ] T003 [P] Reserve focused verification files in `tests/unit/rag/test_settings.py`, `tests/unit/rag/test_service.py`, `tests/unit/rag/test_context_injection.py`, `tests/unit/api/routers/test_retrieval_gateway.py`, `tests/unit/services/temporal/runtime/test_launcher.py`, `tests/unit/workflows/temporal/test_agent_runtime_activities.py`, and `tests/integration/workflows/temporal/test_managed_session_retrieval_context.py` for MM-508 coverage.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish only the reusable verification scaffolding MM-508 depends on.

**CRITICAL**: No story implementation work can begin until these blocking test scaffolds are ready.

- [ ] T004 [P] Extend retrieval-settings fixtures in `tests/unit/rag/test_settings.py` and `tests/unit/rag/test_service.py` for gateway-preference, direct-transport, and budget-propagation scenarios covering FR-002, FR-003, and FR-005.
- [ ] T005 [P] Extend runtime-boundary fixtures in `tests/unit/services/temporal/runtime/test_launcher.py`, `tests/unit/workflows/temporal/test_agent_runtime_activities.py`, and `tests/integration/workflows/temporal/test_managed_session_retrieval_context.py` for retrieval-versus-profile separation and transport-observability scenarios covering FR-001, FR-005, and FR-006.
- [ ] T006 [P] Extend degraded-fallback and gateway-route fixtures in `tests/unit/rag/test_context_injection.py` and `tests/unit/api/routers/test_retrieval_gateway.py` for explicit local-fallback gating and gateway auth-readiness scenarios covering FR-002 and FR-004.
- [ ] T007 Confirm the selected unit and targeted integration commands from `specs/256-retrieval-transport-separation/quickstart.md` can execute against the reserved MM-508 test surfaces before story-specific red-first tests are added.

**Checkpoint**: Reusable MM-508 verification scaffolding is ready and story-specific red-first tests can begin.

---

## Phase 3: Story - Separate Retrieval Configuration From Runtime Profiles

**Summary**: As MoonMind retrieval configuration, I want retrieval transport and embedding/vector-store settings to stay separate from managed-runtime provider profiles so that MoonMind can choose direct, gateway, or explicit fallback retrieval without overloading runtime launch profiles.

**Independent Test**: Configure a managed runtime with a provider profile and run retrieval setup under multiple environment shapes. Verify runtime launch profile data remains separate from retrieval settings, gateway retrieval becomes the preferred transport when MoonMind owns outbound retrieval or embedding credentials are absent in the session environment, direct retrieval still works when configuration and policy allow it, local fallback is explicit and labeled as degraded behavior, overlay and budget knobs flow through the selected retrieval path, and all resulting MoonSpec artifacts preserve `MM-508`.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, SC-007, acceptance scenarios 1 through 6, DESIGN-REQ-004, DESIGN-REQ-009, DESIGN-REQ-010, DESIGN-REQ-014, DESIGN-REQ-016, DESIGN-REQ-019, DESIGN-REQ-024, DESIGN-REQ-025

**Test Plan**:

- Unit: retrieval-setting separation, gateway-preference reasoning, direct-transport support, fallback gating and labeling, compact metadata, and knob propagation.
- Integration: managed-runtime boundary proof for retrieval-versus-profile ownership, transport selection visibility, and coherent overlay or budget behavior.

### Unit Tests (write first) ⚠️

> **NOTE: Write these tests FIRST. Run them, confirm they FAIL for the expected reason, then implement only enough code to make them pass.**

- [ ] T008 [P] Add failing unit coverage for FR-001, DESIGN-REQ-004, DESIGN-REQ-019, acceptance scenario 1, and SC-001 in `tests/unit/services/temporal/runtime/test_launcher.py` and `tests/unit/workflows/temporal/test_agent_runtime_activities.py` to prove retrieval settings remain independent from provider-profile launch settings.
- [ ] T009 [P] Add failing unit coverage for FR-002, DESIGN-REQ-009, DESIGN-REQ-024, acceptance scenario 2, and SC-002 in `tests/unit/rag/test_settings.py`, `tests/unit/rag/test_service.py`, and `tests/unit/api/routers/test_retrieval_gateway.py` to prove gateway preference when runtime embedding credentials are unavailable or MoonMind owns outbound retrieval.
- [ ] T010 [P] Add verification-first unit coverage for FR-003, DESIGN-REQ-010, acceptance scenario 3, and SC-003 in `tests/unit/rag/test_settings.py` and `tests/unit/rag/test_service.py` to prove direct retrieval remains an allowed path.
- [ ] T011 [P] Add failing unit coverage for FR-004, DESIGN-REQ-014, acceptance scenario 4, and SC-004 in `tests/unit/rag/test_context_injection.py` to prove local fallback is explicit, policy gated, and degraded.
- [ ] T012 [P] Add failing unit coverage for FR-005, DESIGN-REQ-016, DESIGN-REQ-025, acceptance scenario 5, and SC-005 in `tests/unit/rag/test_service.py`, `tests/unit/rag/test_context_injection.py`, and `tests/unit/api/routers/test_retrieval_gateway.py` to prove overlay, filters, and budget knobs stay coherent across transports and compact metadata records the selected transport.
- [ ] T013 [P] Add failing unit coverage for FR-006, DESIGN-REQ-025, acceptance scenario 5, and SC-006 in `tests/unit/services/temporal/runtime/test_launcher.py` and `tests/unit/api_service/api/routers/test_provider_profiles.py` to prove provider profiles do not become the implicit retrieval-transport or embedding-credential authority.
- [ ] T014 Run the unit test command from `specs/256-retrieval-transport-separation/quickstart.md` to confirm T008-T013 fail for the expected reason before any production changes.

### Integration Tests (write first) ⚠️

- [ ] T015 [P] Add a failing integration test in `tests/integration/workflows/temporal/test_managed_session_retrieval_context.py` for FR-001, FR-002, acceptance scenarios 1 and 2, SC-001, SC-002, and DESIGN-REQ-004 / DESIGN-REQ-009 / DESIGN-REQ-019 covering managed-runtime retrieval-versus-profile separation and gateway preference.
- [ ] T016 [P] Add a failing integration test in `tests/integration/workflows/temporal/test_managed_session_retrieval_context.py` for FR-003, FR-004, FR-005, FR-006, acceptance scenarios 3 through 5, SC-003 through SC-006, and DESIGN-REQ-010 / DESIGN-REQ-014 / DESIGN-REQ-016 / DESIGN-REQ-024 / DESIGN-REQ-025 covering direct availability, degraded fallback, and coherent knob propagation.
- [ ] T017 Run `pytest tests/integration/workflows/temporal/test_managed_session_retrieval_context.py -q --tb=short` to confirm T015-T016 fail for the expected reason before implementation.

### Red-First Confirmation ⚠️

- [ ] T018 Record the intended red-first failure evidence from T014 and T017 in MM-508 implementation notes or verification notes so final verification can distinguish already-correct behavior from newly completed transport-separation work.

### Conditional Fallback Implementation (implemented_unverified rows)

- [ ] T019 If T010 or T016 proves the direct path no longer satisfies MM-508, update `moonmind/rag/settings.py` and `moonmind/rag/service.py` for FR-003, acceptance scenario 3, SC-003, and DESIGN-REQ-010 to preserve direct retrieval without weakening gateway preference.

### Implementation

- [ ] T020 Implement explicit degraded fallback observability for FR-004, acceptance scenario 4, SC-004, and DESIGN-REQ-014 in `moonmind/rag/context_injection.py` and any affected retrieval metadata helpers so local fallback stays policy gated and transport-visible.
- [ ] T021 Implement retrieval-setting separation for FR-001, acceptance scenario 1, SC-001, and DESIGN-REQ-004 / DESIGN-REQ-019 in `api_service/api/routers/executions.py`, `moonmind/workflows/temporal/runtime/launcher.py`, and any affected retrieval-setting helpers.
- [ ] T022 Implement gateway-preference behavior for FR-002, acceptance scenario 2, SC-002, and DESIGN-REQ-009 / DESIGN-REQ-024 in `moonmind/rag/settings.py`, `moonmind/rag/service.py`, and `api_service/api/routers/retrieval_gateway.py`.
- [ ] T023 Implement coherent transport metadata and knob propagation for FR-005, FR-006, acceptance scenario 5, SC-005, SC-006, and DESIGN-REQ-016 / DESIGN-REQ-025 in `moonmind/rag/context_injection.py`, `moonmind/rag/service.py`, `api_service/api/routers/retrieval_gateway.py`, and any affected runtime metadata helpers.
- [ ] T024 Run the targeted unit and integration commands from `specs/256-retrieval-transport-separation/quickstart.md` and fix failures until T008-T023 satisfy FR-001 through FR-006 and the in-scope DESIGN-REQ rows.
- [ ] T025 Run the MM-508 story validation flow from `specs/256-retrieval-transport-separation/quickstart.md`, including `rg -n "MM-508" specs/256-retrieval-transport-separation`, to confirm story validation and traceability for FR-007 and SC-007.

**Checkpoint**: The story is fully functional, covered by unit and integration tests, and independently validated.

---

## Phase 4: Polish & Verification

**Purpose**: Strengthen the completed story without changing scope.

- [ ] T026 [P] Refresh `specs/256-retrieval-transport-separation/plan.md`, `research.md`, `data-model.md`, `quickstart.md`, and `contracts/retrieval-transport-contract.md` if implementation changes the verified requirement statuses or contract details.
- [ ] T027 [P] Expand edge-case unit coverage in `tests/unit/rag/test_context_injection.py`, `tests/unit/rag/test_settings.py`, and `tests/unit/api/routers/test_retrieval_gateway.py` for mixed transport availability, unsupported providers, and degraded fallback reasons.
- [ ] T028 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/rag/test_settings.py tests/unit/rag/test_service.py tests/unit/rag/test_context_injection.py tests/unit/api/routers/test_retrieval_gateway.py tests/unit/services/temporal/runtime/test_launcher.py tests/unit/workflows/temporal/test_agent_runtime_activities.py tests/unit/api_service/api/routers/test_provider_profiles.py` for final unit verification.
- [ ] T029 Run `./tools/test_integration.sh` when hermetic integration coverage applies, or run `pytest tests/integration/workflows/temporal/test_managed_session_retrieval_context.py -q --tb=short` and record the exact runtime blocker if Docker or Temporal infrastructure is unavailable.
- [ ] T030 Run the quickstart validation in `specs/256-retrieval-transport-separation/quickstart.md` and capture any operator-facing prerequisite updates needed for MM-508.
- [ ] T031 Run `/moonspec-verify` / `/speckit.verify` for `specs/256-retrieval-transport-separation/spec.md` and write final verification evidence to `specs/256-retrieval-transport-separation/verification.md`.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup completion and blocks story-specific red-first tests until reusable transport and boundary fixtures are ready.
- **Story (Phase 3)**: Depends on Phase 2 completion and red-first verification.
- **Polish (Phase 4)**: Depends on story validation and passing targeted tests.

### Within The Story

- T008-T013 must be written before T014.
- T015-T016 must be written before T017.
- T014 and T017 must confirm red-first failures before any implementation work begins.
- T019 is conditional and runs only if verification proves the existing direct path is insufficient.
- T020 is required implementation work because FR-004 / DESIGN-REQ-014 remain partial in `plan.md`.
- T021 should land before T022 and T023 because boundary separation clarifies what transport behavior is allowed to consume.
- T024 depends on completion of all required implementation tasks.
- T025 depends on T024.

### Parallel Opportunities

- T003 can run in parallel with T002.
- T004-T006 can run in parallel because they touch different verification surfaces.
- T008-T013 can be authored in parallel because they target different files or distinct assertions.
- T015 and T016 can be authored in parallel if they are kept in separate scenario blocks within `tests/integration/workflows/temporal/test_managed_session_retrieval_context.py`.
- T026 and T027 can run in parallel after story validation is complete.

## Parallel Example: Story Phase

```bash
# Launch red-first coverage together:
Task: "Add failing retrieval-setting separation tests in tests/unit/services/temporal/runtime/test_launcher.py and tests/unit/workflows/temporal/test_agent_runtime_activities.py"
Task: "Add failing gateway-preference and direct-path tests in tests/unit/rag/test_settings.py and tests/unit/rag/test_service.py"
Task: "Add failing degraded-fallback tests in tests/unit/rag/test_context_injection.py"
```

## Implementation Strategy

### Verification-First Story Delivery

1. Confirm the active MM-508 artifacts and current retrieval or provider-profile boundaries.
2. Prepare shared transport and runtime-boundary test scaffolding.
3. Write unit and integration verification tests and run them to confirm intended failures.
4. Apply the conditional direct-path or degraded-fallback fallback work only if verification proves it is needed.
5. Implement retrieval-setting separation, gateway preference, and coherent metadata behavior.
6. Re-run the targeted unit and integration commands until the MM-508 story passes.
7. Validate the quickstart flow and MM-508 traceability.
8. Run final unit verification, the required integration path, and `/moonspec-verify` / `/speckit.verify`.

## Notes

- This task list covers one story only.
- `moonspec-breakdown` is not applicable because MM-508 is already a single-story Jira preset brief.
- T019 is the only conditional fallback implementation task because FR-003 / DESIGN-REQ-010 are the direct-path implemented-unverified rows in `plan.md`; FR-004 / DESIGN-REQ-014 remain partial and therefore stay in the required implementation path.
- Preserve `MM-508` in all downstream evidence and verification artifacts.
