# Tasks: Runtime Prompt Boundary

**Input**: Design documents from `/work/agent_jobs/mm:50961052-74bb-4c16-979a-1d3698facd1f/repo/specs/349-runtime-prompt-boundary/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/runtime-prompt-boundary.md, quickstart.md

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason for partial/missing behavior, then implement the production code until they pass. For implemented_unverified rows, write verification tests first and run conditional fallback implementation only if those tests fail.

**Organization**: Tasks are grouped around exactly one independently testable story: Runtime Attachment Boundary.

**Source Traceability**: Preserves Jira issue `MM-650`, the original Jira preset brief in `spec.md`, and `DESIGN-REQ-026` from `docs/Tasks/TaskArchitecture.md` section 10.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh tests/unit/workflows/tasks/test_prepared_context.py tests/unit/workflows/adapters/test_target_aware_prepared_context.py tests/unit/workflows/adapters/test_base_external_agent_adapter.py tests/unit/workflows/adapters/test_openclaw_agent_adapter.py tests/unit/agents/codex_worker/test_worker.py`
- Integration tests: `pytest tests/integration/workflows/temporal/workflows/test_run_target_aware_inputs.py -q`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel because the task touches different files and does not depend on incomplete work
- Each task names exact file paths and the relevant requirement, scenario, success criterion, or source mapping

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the existing feature artifacts and test surfaces are ready for TDD work.

- [ ] T001 Confirm the active feature artifacts exist and preserve `MM-650` and `DESIGN-REQ-026` in specs/349-runtime-prompt-boundary/spec.md, specs/349-runtime-prompt-boundary/plan.md, specs/349-runtime-prompt-boundary/research.md, specs/349-runtime-prompt-boundary/data-model.md, specs/349-runtime-prompt-boundary/contracts/runtime-prompt-boundary.md, and specs/349-runtime-prompt-boundary/quickstart.md (FR-008, SC-005)
- [ ] T002 Confirm the focused unit and integration commands from specs/349-runtime-prompt-boundary/quickstart.md are still valid for the current repository layout (FR-001 through FR-008, DESIGN-REQ-026)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish the reusable test fixtures and traceability baseline needed before story-specific tests and implementation.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [ ] T003 [P] Extend or confirm reusable canonical task payload fixtures with objective and step-scoped image attachments in tests/unit/workflows/tasks/test_prepared_context.py (FR-001, FR-006, SC-004, DESIGN-REQ-026)
- [ ] T004 [P] Extend or confirm runtime adapter request fixtures carrying prepared `input_refs` and `metadata.moonmind.preparedContext` in tests/unit/workflows/adapters/test_target_aware_prepared_context.py (FR-003, FR-005, SCN-002, SCN-003)
- [ ] T005 [P] Extend or confirm Temporal workflow boundary fixtures for paired `codex_cli` and multimodal/external runtime requests in tests/integration/workflows/temporal/workflows/test_run_target_aware_inputs.py (FR-001, FR-003, FR-006, SCN-004)

**Checkpoint**: Foundation ready; story test authoring and red-first confirmation can begin.

---

## Phase 3: Story - Runtime Attachment Boundary

**Summary**: As a runtime-adapter engineer, I want normalized task intent plus artifact refs to be prepared differently for text-first and multimodal runtimes without changing the canonical task contract or allowing adapters to invent attachment targeting rules.

**Independent Test**: Submit equivalent image attachment intent to text-first and multimodal/external runtime paths, then verify text-first execution receives canonical `INPUT ATTACHMENTS`, multimodal/external execution receives raw image artifact refs, objective/current-step target binding is preserved, sibling step refs are excluded, and unsupported target rules produce diagnostics instead of silent attachment loss.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, SCN-001, SCN-002, SCN-003, SCN-004, SC-001, SC-002, SC-003, SC-004, SC-005, DESIGN-REQ-026.

**Test Plan**:

- Unit: prepared context selection, raw refs, target-kind guardrails, adapter input refs, text-first prompt safety, missing-preparation diagnostics.
- Integration: Temporal workflow request construction for text-first vs multimodal/external runtime modes from the same canonical task payload, target binding preservation, sibling exclusion, diagnostics.

### Unit Tests (write first) ⚠️

> Write these tests first. Partial rows should fail for the intended missing behavior before implementation. Implemented-unverified rows may pass; if they pass, skip their conditional fallback implementation tasks and preserve the evidence.

- [ ] T006 [P] Add or update unit tests proving selected prepared context exposes raw artifact refs without broadening objective/current-step target binding in tests/unit/workflows/tasks/test_prepared_context.py (FR-001, FR-003, FR-006, SC-002, SC-004, DESIGN-REQ-026)
- [ ] T007 [P] Add a failing unit guardrail test proving adapter-facing prepared context excludes sibling step refs and cannot add non-canonical target rules in tests/unit/workflows/adapters/test_target_aware_prepared_context.py (FR-005, SCN-003, SC-003, DESIGN-REQ-026)
- [ ] T008 [P] Add or update external adapter unit tests proving raw image artifact refs are passed as `input_refs` without mutating canonical task metadata in tests/unit/workflows/adapters/test_base_external_agent_adapter.py (FR-003, SCN-002, SC-002)
- [ ] T009 [P] Add or update OpenClaw adapter translation tests proving raw input refs are included only as adapter-boundary refs and not as control-plane target semantics in tests/unit/workflows/adapters/test_openclaw_agent_adapter.py (FR-003, FR-005, SCN-002, SCN-003)
- [ ] T010 [P] Add or update text-first prompt regression tests preserving existing `INPUT ATTACHMENTS` ordering, generated vision context paths, and unsafe metadata filtering in tests/unit/agents/codex_worker/test_worker.py (FR-002, SCN-001, SC-001)
- [ ] T011 [P] Add or update unit tests for missing prepared context/raw refs diagnostics in tests/unit/workflows/tasks/test_prepared_context.py (FR-007, DESIGN-REQ-026)

### Integration Tests (write first) ⚠️

- [ ] T012 Add a failing paired runtime workflow-boundary test using the same canonical task payload for `codex_cli` and multimodal/external runtime modes in tests/integration/workflows/temporal/workflows/test_run_target_aware_inputs.py (FR-001, FR-003, FR-006, SCN-002, SCN-004, SC-002, SC-004, DESIGN-REQ-026)
- [ ] T013 Add a failing integration guardrail test proving adapter/runtime preparation rejects or excludes non-canonical target broadening with explicit diagnostics in tests/integration/workflows/temporal/workflows/test_run_target_aware_inputs.py (FR-005, FR-007, SCN-003, SC-003, DESIGN-REQ-026)
- [ ] T014 Add or update integration coverage preserving already-verified text-first `INPUT ATTACHMENTS` behavior through the workflow boundary in tests/integration/workflows/temporal/workflows/test_run_target_aware_inputs.py (FR-002, SCN-001, SC-001)

### Red-First Confirmation ⚠️

- [ ] T015 Run `./tools/test_unit.sh tests/unit/workflows/tasks/test_prepared_context.py tests/unit/workflows/adapters/test_target_aware_prepared_context.py tests/unit/workflows/adapters/test_base_external_agent_adapter.py tests/unit/workflows/adapters/test_openclaw_agent_adapter.py tests/unit/agents/codex_worker/test_worker.py` and record that new partial-row tests fail for the expected MM-650 boundary gaps in specs/349-runtime-prompt-boundary/implementation-notes.md (FR-003, FR-005, FR-006, SCN-002, SCN-003, SC-002, SC-003, SC-004, DESIGN-REQ-026)
- [ ] T016 Run `pytest tests/integration/workflows/temporal/workflows/test_run_target_aware_inputs.py -q` and record that new partial-row integration tests fail for the expected MM-650 boundary gaps in specs/349-runtime-prompt-boundary/implementation-notes.md (FR-003, FR-005, FR-006, FR-007, SCN-002, SCN-003, SCN-004, DESIGN-REQ-026)
- [ ] T017 Record any implemented_unverified tests that already pass and identify which conditional fallback tasks can be skipped in specs/349-runtime-prompt-boundary/implementation-notes.md (FR-001, FR-007, SCN-004)

### Conditional Fallback Implementation for Implemented-Unverified Rows

- [ ] T018 If T006 or T012 exposes missing normalized task intent plus artifact refs across runtime modes, update moonmind/workflows/temporal/workflows/run.py and moonmind/workflows/tasks/prepared_context.py to preserve selected prepared refs consistently (FR-001, SCN-004)
- [ ] T019 If T011 or T013 exposes silent missing-preparation behavior, update moonmind/workflows/tasks/prepared_context.py and moonmind/workflows/temporal/activity_runtime.py to return bounded diagnostics for selected-runtime missing context or raw refs (FR-007, DESIGN-REQ-026)

### Implementation

- [ ] T020 Complete multimodal/external raw artifact ref support or hardening in moonmind/workflows/tasks/prepared_context.py so selected `rawInputRefs` remain available without changing the canonical task contract (FR-003, SCN-002, SC-002, DESIGN-REQ-026)
- [ ] T021 Complete adapter-boundary guardrails in moonmind/workflows/adapters/base_external_agent_adapter.py so adapters consume selected refs without adding target kinds or target rules (FR-005, SCN-003, SC-003)
- [ ] T022 Complete provider translation guardrails in moonmind/workflows/adapters/openclaw_agent_adapter.py so raw refs are adapter-boundary inputs only and never redefine attachment target semantics (FR-003, FR-005, SCN-002, SCN-003)
- [ ] T023 Complete workflow-boundary wiring in moonmind/workflows/temporal/workflows/run.py so text-first managed runtime and multimodal/external runtime requests are derived from the same canonical task payload while preserving objective/current-step target binding (FR-001, FR-003, FR-006, SCN-004, SC-004, DESIGN-REQ-026)
- [ ] T024 Preserve or adjust text-first prompt rendering in moonmind/agents/codex_worker/worker.py only if T010 or T014 exposes a regression in `INPUT ATTACHMENTS` ordering, generated context paths, sibling exclusion, or unsafe metadata filtering (FR-002, SCN-001, SC-001)
- [ ] T025 Ensure target-aware vision context remains restricted to canonical `objective` and `step` target kinds in moonmind/vision/service.py if T013 exposes a target-kind validation gap (FR-004, FR-005, DESIGN-REQ-026)

### Story Validation

- [ ] T026 Run the focused unit command from this tasks.md and verify all MM-650 unit tests pass after implementation in tests/unit/workflows/tasks/test_prepared_context.py, tests/unit/workflows/adapters/test_target_aware_prepared_context.py, tests/unit/workflows/adapters/test_base_external_agent_adapter.py, tests/unit/workflows/adapters/test_openclaw_agent_adapter.py, and tests/unit/agents/codex_worker/test_worker.py (FR-001 through FR-008, SC-001 through SC-005, DESIGN-REQ-026)
- [ ] T027 Run `pytest tests/integration/workflows/temporal/workflows/test_run_target_aware_inputs.py -q` and verify the single story passes through text-first and multimodal/external workflow-boundary paths (SCN-001 through SCN-004, DESIGN-REQ-026)
- [ ] T028 Update specs/349-runtime-prompt-boundary/implementation-notes.md with passing focused test evidence, skipped conditional fallback tasks, and any implementation files changed for MM-650 (FR-008, SC-005)

**Checkpoint**: The Runtime Attachment Boundary story is fully covered by unit and integration tests and remains independently testable.

---

## Phase 4: Polish & Verification

**Purpose**: Strengthen the completed story without adding hidden scope.

- [ ] T029 [P] Run `rg -n "MM-650|DESIGN-REQ-026|original Jira preset brief" specs/349-runtime-prompt-boundary` and preserve traceability evidence in specs/349-runtime-prompt-boundary/implementation-notes.md (FR-008, SC-005)
- [ ] T030 [P] Review specs/349-runtime-prompt-boundary/contracts/runtime-prompt-boundary.md against final code behavior and update only if implementation evidence changes the boundary wording (FR-001 through FR-007, DESIGN-REQ-026)
- [ ] T031 Run `./tools/test_unit.sh` for the full required unit suite and record the result in specs/349-runtime-prompt-boundary/implementation-notes.md (FR-001 through FR-008)
- [ ] T032 Run `./tools/test_integration.sh` for required hermetic integration coverage when Docker is available, or record the managed-runtime blocker and focused integration evidence in specs/349-runtime-prompt-boundary/implementation-notes.md (SCN-001 through SCN-004, DESIGN-REQ-026)
- [ ] T033 Run `/moonspec-verify` against specs/349-runtime-prompt-boundary/spec.md after implementation and tests pass, preserving `MM-650`, the original Jira preset brief, and `DESIGN-REQ-026` in final verification evidence (FR-008, SC-005)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies.
- **Foundational (Phase 2)**: depends on Phase 1; blocks story test and implementation work.
- **Story (Phase 3)**: depends on Phase 2; tests and red-first confirmation must precede production code changes.
- **Polish & Verification (Phase 4)**: depends on story tests and implementation passing.

### Within The Story

- T006 through T011 unit tests and T012 through T014 integration tests must be authored before T015 through T017 red-first confirmation.
- T015 through T017 must complete before T018 through T025 implementation or conditional fallback work.
- T018 and T019 are conditional fallback tasks for implemented_unverified rows and should be skipped when their verification tests pass.
- T020 through T025 must complete before T026 through T028 story validation.
- T029 through T033 run only after the story checkpoint passes.

### Parallel Opportunities

- T003, T004, and T005 can run in parallel because they touch separate test fixture surfaces.
- T006 through T011 can run in parallel where they touch different unit test files.
- T029 and T030 can run in parallel after the story checkpoint.

---

## Parallel Example: Story Phase

```bash
# Launch independent unit test authoring together:
Task: "T006 update tests/unit/workflows/tasks/test_prepared_context.py"
Task: "T008 update tests/unit/workflows/adapters/test_base_external_agent_adapter.py"
Task: "T010 update tests/unit/agents/codex_worker/test_worker.py"

# Launch independent implementation hardening together after red-first confirmation when files do not overlap:
Task: "T021 update moonmind/workflows/adapters/base_external_agent_adapter.py"
Task: "T022 update moonmind/workflows/adapters/openclaw_agent_adapter.py"
```

---

## Implementation Strategy

### Test-Driven Story Delivery

1. Complete setup and foundational fixture checks.
2. Write unit tests for prepared context, adapter refs, text-first prompt safety, and diagnostics.
3. Write integration tests for paired runtime preparation and adapter guardrails.
4. Run red-first confirmation and record expected failures or already-passing verification tests.
5. Apply conditional fallback implementation only for implemented_unverified rows whose verification tests fail.
6. Implement partial rows for multimodal raw refs, adapter target guardrails, cross-runtime target preservation, and diagnostics.
7. Re-run focused unit and integration tests, then full unit and required integration suites.
8. Run `/moonspec-verify` and preserve MM-650 traceability.

### Requirement Status Handling

- **Code and tests required**: FR-003, FR-005, FR-006, SCN-002, SCN-003, SC-002, SC-003, SC-004, DESIGN-REQ-026.
- **Verification tests first with conditional fallback**: FR-001, FR-007, SCN-004.
- **Already verified, preserve in final validation**: FR-002, FR-004, FR-008, SCN-001, SC-001, SC-005.

## Notes

- This task list covers exactly one story: Runtime Attachment Boundary.
- Unit and integration tests are required before implementation.
- Red-first confirmation tasks are required before production code tasks.
- Implemented_verified rows do not receive unnecessary implementation work.
- `/moonspec-verify` is the final verification task after implementation and tests pass.
