# Tasks: Prepare-Time Target-Aware Attachment Materialization

**Input**: Design documents from `specs/347-prepare-target-aware-attachments/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/prepared-attachment-manifest.md`, `quickstart.md`

**Tests**: Unit tests and integration tests are REQUIRED. Write or confirm tests first, confirm missing or regressed behavior before implementation, then implement or repair production code until tests pass.

**Organization**: Tasks cover exactly one story: `Target-Aware Attachment Preparation`.

**Source Traceability**: Preserves Jira issue `MM-648`, the canonical Jira preset brief, FR-001 through FR-010, SC-001 through SC-005, and DESIGN-REQ-002, DESIGN-REQ-020, DESIGN-REQ-029. `plan.md` classifies all rows as `implemented_verified`; this task list therefore preserves validation-first work plus conditional implementation repairs if any verification regresses.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh`
- Integration tests: `./tools/test_integration.sh`
- Focused integration fallback when Docker is unavailable: `pytest tests/integration/workflows/temporal/workflows/test_run_target_aware_inputs.py -q --tb=short`
- Final verification: `/moonspec-verify` (`/speckit.verify` equivalent)

## Phase 1: Setup

**Purpose**: Confirm active artifacts and requirement status before validation work.

- [ ] T001 Confirm `.specify/feature.json` points to `specs/347-prepare-target-aware-attachments` and that `specs/347-prepare-target-aware-attachments/spec.md` contains exactly one `## User Story -` section. (FR-010, SC-005)
- [ ] T002 Confirm `specs/347-prepare-target-aware-attachments/plan.md`, `research.md`, `data-model.md`, `contracts/prepared-attachment-manifest.md`, and `quickstart.md` exist and preserve `MM-648`. (FR-010, SC-005)
- [ ] T003 Inspect `specs/347-prepare-target-aware-attachments/plan.md` `## Requirement Status` and confirm FR-001 through FR-010, SC-001 through SC-005, DESIGN-REQ-002, DESIGN-REQ-020, and DESIGN-REQ-029 are present. (FR-010, SC-005)

## Phase 2: Foundational

**Purpose**: Confirm the existing test and implementation surfaces used by the single story.

- [ ] T004 [P] Inspect unit test surfaces in `tests/unit/workflows/tasks/test_prepared_context.py` and `tests/unit/agents/codex_worker/test_attachment_materialization.py` for manifest metadata, stable step-ref, no-inline-content, and materialization coverage. (FR-001, FR-002, FR-003, FR-004, FR-006, FR-007, FR-008, FR-009, DESIGN-REQ-002, DESIGN-REQ-020, DESIGN-REQ-029)
- [ ] T005 [P] Inspect integration boundary coverage in `tests/integration/workflows/temporal/workflows/test_run_target_aware_inputs.py` for objective/current-step context isolation. (FR-005, SC-004, DESIGN-REQ-020)
- [ ] T006 [P] Inspect implementation surfaces in `moonmind/workflows/tasks/prepared_context.py` and `moonmind/agents/codex_worker/worker.py` for stable step identity enforcement, `workspacePath`, `status`, and explicit failure behavior. (FR-001 through FR-009)

**Checkpoint**: Existing evidence surfaces are identified; story validation can begin.

## Phase 3: Story - Target-Aware Attachment Preparation

**Summary**: Preparation materializes objective and step attachments while preserving explicit target identity and rejecting ambiguous retargeting inputs.

**Independent Test**: Build mixed objective/step attachment payloads and verify manifest entries, materialized paths, status metadata, no inline payloads, fail-fast behavior for step attachments without stable refs, and per-target workflow context isolation.

**Traceability**: FR-001 through FR-010; SC-001 through SC-005; DESIGN-REQ-002, DESIGN-REQ-020, DESIGN-REQ-029; MM-648.

**Unit Test Plan**:

- Confirm prepared context entries reject inline binary/generated content and include bounded refs/metadata.
- Confirm objective and step attachments produce stable target-aware workspace paths and manifest metadata.
- Confirm step-scoped attachments without stable `id`, `stepRef`, or `ref` fail fast.
- Confirm reorder/text edits do not retarget existing attachment bindings.
- Confirm worker materialization failure diagnostics identify the affected target.

**Integration Test Plan**:

- Confirm `MoonMind.Run` target-aware workflow boundary provides objective context plus only current-step context.
- Run the compose-backed `./tools/test_integration.sh` when Docker is available; otherwise run the focused local integration fallback and document Docker policy blockage.

### Unit Tests (red-first / verification-first)

- [ ] T007 [P] Run `./tools/test_unit.sh tests/unit/workflows/tasks/test_prepared_context.py` and confirm prepared context unit coverage passes or fails for the intended missing behavior. (FR-001, FR-003, FR-004, FR-006, FR-008, FR-009, SC-001, SC-002, DESIGN-REQ-002, DESIGN-REQ-029)
- [ ] T008 [P] Run `./tools/test_unit.sh tests/unit/agents/codex_worker/test_attachment_materialization.py` and confirm worker materialization unit coverage passes or fails for the intended missing behavior. (FR-002, FR-004, FR-007, FR-009, SC-001, SC-003, DESIGN-REQ-020, DESIGN-REQ-029)

### Integration Tests (verification-first)

- [ ] T009 Run `./tools/test_integration.sh` for hermetic integration coverage, or if Docker is unavailable, record the exact blocker and run `pytest tests/integration/workflows/temporal/workflows/test_run_target_aware_inputs.py -q --tb=short`. (FR-005, SC-004, DESIGN-REQ-020)

### Red-First Confirmation

- [ ] T010 If T007 or T008 fails, confirm the failure is due to missing or regressed `MM-648` behavior before editing production code; otherwise record the tests as implemented-verified evidence. (FR-001 through FR-009)
- [ ] T011 If T009 fails, confirm whether the failure is a real target-aware workflow regression or an environment blocker before editing production code; otherwise record the integration evidence. (FR-005, SC-004)

### Conditional Implementation Repairs

- [ ] T012 If T007 fails for prepared-context behavior, repair `moonmind/workflows/tasks/prepared_context.py` to enforce stable step refs, preserve refs-only metadata, and emit `workspacePath`/`status` fields. (FR-001, FR-003, FR-004, FR-006, FR-008, FR-009, DESIGN-REQ-002, DESIGN-REQ-029)
- [ ] T013 If T008 fails for worker materialization behavior, repair `moonmind/agents/codex_worker/worker.py` to reject ambiguous step attachments, materialize target-distinct paths, mark manifest entries prepared, and preserve target-specific failure diagnostics. (FR-002, FR-004, FR-007, FR-009, DESIGN-REQ-020, DESIGN-REQ-029)
- [ ] T014 If T009 exposes a real workflow boundary regression, repair `moonmind/workflows/temporal/workflows/run.py` or related target-aware context wiring so steps receive objective plus only current-step context. (FR-005, SC-004, DESIGN-REQ-020)

### Story Validation

- [ ] T015 Run focused unit verification with `./tools/test_unit.sh tests/unit/workflows/tasks/test_prepared_context.py tests/unit/agents/codex_worker/test_attachment_materialization.py` after any repairs, and confirm all `MM-648` unit coverage passes. (FR-001 through FR-009, SC-001 through SC-003, DESIGN-REQ-002, DESIGN-REQ-020, DESIGN-REQ-029)
- [ ] T016 Run full unit verification with `./tools/test_unit.sh` after any repairs, and capture the result in final verification evidence. (FR-001 through FR-010)
- [ ] T017 Run integration verification using `./tools/test_integration.sh` or the focused fallback command with documented Docker blocker, and capture the result in final verification evidence. (FR-005, SC-004, DESIGN-REQ-020)

**Checkpoint**: The story is validated against the implemented-verified evidence, or conditional repair tasks have restored the expected behavior.

## Phase 4: Polish And Verification

**Purpose**: Preserve traceability and final evidence without adding hidden scope.

- [ ] T018 [P] Review `specs/347-prepare-target-aware-attachments/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/prepared-attachment-manifest.md`, and `quickstart.md` for `MM-648` traceability drift. (FR-010, SC-005)
- [ ] T019 [P] Review `moonmind/workflows/tasks/prepared_context.py` and `moonmind/agents/codex_worker/worker.py` for secret hygiene and binary-content guardrails in attachment metadata. (FR-001, FR-008, DESIGN-REQ-002)
- [ ] T020 Run `/moonspec-verify` against `MM-648`, the original Jira preset brief, `spec.md`, `plan.md`, `tasks.md`, source design mappings, and test evidence. (FR-010, SC-005)

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on setup artifact confirmation and identifies validation surfaces.
- **Story (Phase 3)**: Depends on foundational inspection. Verification tasks run before any conditional implementation repairs.
- **Polish And Verification (Phase 4)**: Depends on story validation evidence.

### Within The Story

- T007-T009 run before T010-T011.
- T012-T014 are conditional and run only if verification exposes a real behavior gap.
- T015-T017 run after conditional repairs, or immediately after T010-T011 when existing evidence passes.
- T020 runs only after unit and integration evidence is captured or blockers are documented.

### Parallel Opportunities

- T004, T005, and T006 can run in parallel because they inspect different surfaces.
- T007 and T008 can run independently when test runner resources permit.
- T018 and T019 can run in parallel because they cover artifact review and code guardrail review separately.

## Parallel Example

```bash
# Inspect independent evidence surfaces:
Task: "T004 Inspect unit tests in tests/unit/workflows/tasks/test_prepared_context.py and tests/unit/agents/codex_worker/test_attachment_materialization.py"
Task: "T005 Inspect integration coverage in tests/integration/workflows/temporal/workflows/test_run_target_aware_inputs.py"
Task: "T006 Inspect implementation surfaces in moonmind/workflows/tasks/prepared_context.py and moonmind/agents/codex_worker/worker.py"
```

## Implementation Strategy

1. Preserve the single-story `MM-648` scope and do not generate future stories.
2. Treat all rows from `plan.md` as `implemented_verified`, so start with verification-first tasks rather than immediate implementation.
3. If focused unit or integration tests fail for real behavior gaps, run only the corresponding conditional repair tasks.
4. Keep binary content out of workflow-visible payloads and preserve stable step-ref binding.
5. Run final unit/integration validation or document environment blockers.
6. Run `/moonspec-verify` and preserve `MM-648`, DESIGN-REQ-002, DESIGN-REQ-020, and DESIGN-REQ-029 in final evidence.

## Coverage Inventory

- FR-001, FR-008, DESIGN-REQ-002: T004, T006, T007, T012, T015, T016, T019, T020.
- FR-002, FR-003, FR-004, FR-007, DESIGN-REQ-020: T004, T006, T008, T013, T015, T016, T020.
- FR-005, SC-004: T005, T009, T014, T017, T020.
- FR-006, FR-009, SC-002, DESIGN-REQ-029: T004, T006, T007, T008, T012, T013, T015, T020.
- FR-010, SC-005: T001, T002, T003, T018, T020.
- SC-001, SC-003: T007, T008, T010, T015, T020.

## Notes

- The task list intentionally distinguishes implemented-verified evidence from conditional implementation repair work.
- Compose-backed integration remains the preferred hermetic integration command; the focused pytest fallback is valid only when Docker is unavailable in the managed environment.
