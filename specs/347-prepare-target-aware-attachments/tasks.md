# Tasks: Prepare-Time Target-Aware Attachment Materialization

**Input**: Design documents from `specs/347-prepare-target-aware-attachments/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/prepared-attachment-manifest.md`, `quickstart.md`

**Tests**: Unit tests and integration tests are REQUIRED. Write or confirm tests first, confirm missing or regressed behavior before implementation, then implement or repair production code until tests pass.

**Organization**: Tasks cover exactly one story: `Target-Aware Attachment Preparation`.

**Source Traceability**: Preserves Jira issue `MM-648`, the canonical Jira preset brief, FR-001 through FR-010, SC-001 through SC-005, and DESIGN-REQ-002, DESIGN-REQ-020, DESIGN-REQ-029. `plan.md` classifies all rows as `implemented_verified`; final `/moonspec-verify` remains the last dedicated workflow step.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh`
- Integration tests: `./tools/test_integration.sh`
- Focused integration fallback when Docker is unavailable: `pytest tests/integration/workflows/temporal/workflows/test_run_target_aware_inputs.py -q --tb=short`
- Final verification: `/moonspec-verify` (`/speckit.verify` equivalent)

## Phase 1: Setup

**Purpose**: Confirm active artifacts and requirement status before validation work.

- [X] T001 Confirm `.specify/feature.json` points to `specs/347-prepare-target-aware-attachments` and that `specs/347-prepare-target-aware-attachments/spec.md` contains exactly one `## User Story -` section. (FR-010, SC-005)
- [X] T002 Confirm `specs/347-prepare-target-aware-attachments/plan.md`, `research.md`, `data-model.md`, `contracts/prepared-attachment-manifest.md`, and `quickstart.md` exist and preserve `MM-648`. (FR-010, SC-005)
- [X] T003 Inspect `specs/347-prepare-target-aware-attachments/plan.md` `## Requirement Status` and confirm FR-001 through FR-010, SC-001 through SC-005, DESIGN-REQ-002, DESIGN-REQ-020, and DESIGN-REQ-029 are present, with SC-003 marked `implemented_verified`. (FR-010, SC-005, SC-003)

## Phase 2: Foundational

**Purpose**: Confirm the existing test and implementation surfaces used by the single story.

- [X] T004 [P] Inspect unit test surfaces in `tests/unit/workflows/tasks/test_prepared_context.py` and `tests/unit/agents/codex_worker/test_attachment_materialization.py` for manifest metadata, stable step-ref, no-inline-content, and materialization coverage. (FR-001, FR-002, FR-003, FR-004, FR-006, FR-007, FR-008, FR-009, DESIGN-REQ-002, DESIGN-REQ-020, DESIGN-REQ-029)
- [X] T005 [P] Inspect integration boundary coverage in `tests/integration/workflows/temporal/workflows/test_run_target_aware_inputs.py` for objective/current-step context isolation and identify where target-specific preparation failure coverage belongs. (FR-005, SC-003, SC-004, DESIGN-REQ-020)
- [X] T006 [P] Inspect implementation surfaces in `moonmind/workflows/tasks/prepared_context.py` and `moonmind/agents/codex_worker/worker.py` for stable step identity enforcement, `workspacePath`, `status`, and explicit failure behavior. (FR-001 through FR-009)

**Checkpoint**: Existing evidence surfaces are identified; story validation can begin.

## Phase 3: Story - Target-Aware Attachment Preparation

**Summary**: Preparation materializes objective and step attachments while preserving explicit target identity and rejecting ambiguous retargeting inputs.

**Independent Test**: Build mixed objective/step attachment payloads and verify manifest entries, materialized paths, status metadata, no inline payloads, fail-fast behavior for step attachments without stable refs, per-target workflow context isolation, and target-specific preparation failure diagnostics.

**Traceability**: FR-001 through FR-010; SC-001 through SC-005; DESIGN-REQ-002, DESIGN-REQ-020, DESIGN-REQ-029; MM-648.

**Unit Test Plan**:

- Confirm prepared context entries reject inline binary/generated content and include bounded refs/metadata.
- Confirm objective and step attachments produce stable target-aware workspace paths and manifest metadata.
- Confirm step-scoped attachments without stable `id`, `stepRef`, or `ref` fail fast.
- Confirm reorder/text edits do not retarget existing attachment bindings.
- Confirm worker materialization failure diagnostics identify the affected target.

**Integration Test Plan**:

- Confirm `MoonMind.Run` target-aware workflow boundary provides objective context plus only current-step context.
- Add or run an integration-level target-specific preparation failure check for SC-003.
- Run the compose-backed `./tools/test_integration.sh` when Docker is available; otherwise run focused local integration checks and document Docker policy blockage.

### Unit Tests (red-first / verification-first)

- [X] T007 [P] Run `./tools/test_unit.sh tests/unit/workflows/tasks/test_prepared_context.py` and confirm prepared context unit coverage passes or fails for the intended missing behavior. (FR-001, FR-003, FR-004, FR-006, FR-008, FR-009, SC-001, SC-002, DESIGN-REQ-002, DESIGN-REQ-029)
- [X] T008 [P] Run `./tools/test_unit.sh tests/unit/agents/codex_worker/test_attachment_materialization.py` and confirm worker materialization unit coverage passes or fails for the intended missing behavior. (FR-002, FR-004, FR-007, FR-009, SC-001, DESIGN-REQ-020, DESIGN-REQ-029)

### Integration Tests (verification-first)

- [X] T009 Run `./tools/test_integration.sh` for hermetic integration coverage, or if Docker is unavailable, record the exact blocker and run `pytest tests/integration/workflows/temporal/workflows/test_run_target_aware_inputs.py -q --tb=short`. (FR-005, SC-004, DESIGN-REQ-020)
- [X] T010 Add or run an integration-level target-specific preparation failure check in `tests/integration/workflows/temporal/workflows/test_run_target_aware_inputs.py` or the nearest existing integration boundary, proving missing/invalid attachment preparation fails with the affected target. (SC-003, FR-007, DESIGN-REQ-020)

### Red-First Confirmation

- [X] T011 If T007 or T008 fails, confirm the failure is due to missing or regressed `MM-648` behavior before editing production code; otherwise record the tests as implemented-verified evidence. (FR-001 through FR-009)
- [X] T012 If T009 or T010 fails, confirm whether the failure is a real target-aware workflow/preparation regression or an environment blocker before editing production code; otherwise record the integration evidence. (FR-005, FR-007, SC-003, SC-004)

### Conditional Implementation Repairs

- [X] T013 If T007 fails for prepared-context behavior, repair `moonmind/workflows/tasks/prepared_context.py` to enforce stable step refs, preserve refs-only metadata, and emit `workspacePath`/`status` fields. (FR-001, FR-003, FR-004, FR-006, FR-008, FR-009, DESIGN-REQ-002, DESIGN-REQ-029)
- [X] T014 If T008 fails for worker materialization behavior, repair `moonmind/agents/codex_worker/worker.py` to reject ambiguous step attachments, materialize target-distinct paths, mark manifest entries prepared, and preserve target-specific failure diagnostics. (FR-002, FR-004, FR-007, FR-009, DESIGN-REQ-020, DESIGN-REQ-029)
- [X] T015 If T009 or T010 exposes a real workflow/preparation boundary regression, repair `moonmind/workflows/temporal/workflows/run.py`, `moonmind/workflows/temporal/activity_runtime.py`, `moonmind/workflows/tasks/prepared_context.py`, or related target-aware wiring so steps receive objective plus only current-step context and preparation failures expose affected targets. (FR-005, FR-007, SC-003, SC-004, DESIGN-REQ-020)

### Story Validation

- [X] T016 Run focused unit verification with `./tools/test_unit.sh tests/unit/workflows/tasks/test_prepared_context.py tests/unit/agents/codex_worker/test_attachment_materialization.py` after any repairs, and confirm all `MM-648` unit coverage passes. (FR-001 through FR-009, SC-001 through SC-003, DESIGN-REQ-002, DESIGN-REQ-020, DESIGN-REQ-029)
- [X] T017 Run full unit verification with `./tools/test_unit.sh` after any repairs, and capture the result in final verification evidence. (FR-001 through FR-010)
- [X] T018 Run integration verification using `./tools/test_integration.sh` or focused fallback commands with documented Docker blocker, and capture the result in final verification evidence. (FR-005, FR-007, SC-003, SC-004, DESIGN-REQ-020)

**Checkpoint**: The story is validated against implemented-verified evidence and SC-003 integration evidence, or conditional repair tasks have restored the expected behavior.

## Phase 4: Polish And Verification

**Purpose**: Preserve traceability and final evidence without adding hidden scope.

- [X] T019 [P] Review `specs/347-prepare-target-aware-attachments/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/prepared-attachment-manifest.md`, and `quickstart.md` for `MM-648` traceability drift. (FR-010, SC-005)
- [X] T020 [P] Review `moonmind/workflows/tasks/prepared_context.py` and `moonmind/agents/codex_worker/worker.py` for secret hygiene and binary-content guardrails in attachment metadata. (FR-001, FR-008, DESIGN-REQ-002)
- [ ] T021 Run `/moonspec-verify` against `MM-648`, the original Jira preset brief, `spec.md`, `plan.md`, `tasks.md`, source design mappings, and test evidence. (FR-010, SC-005)

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on setup artifact confirmation and identifies validation surfaces.
- **Story (Phase 3)**: Depends on foundational inspection. Verification tasks run before any conditional implementation repairs.
- **Polish And Verification (Phase 4)**: Depends on story validation evidence.

### Within The Story

- T007-T010 run before T011-T012.
- T013-T015 are conditional and run only if verification exposes a real behavior gap.
- T016-T018 run after conditional repairs, or immediately after T011-T012 when existing evidence passes.
- T021 runs only after unit and integration evidence is captured or blockers are documented.

### Parallel Opportunities

- T004, T005, and T006 can run in parallel because they inspect different surfaces.
- T007 and T008 can run independently when test runner resources permit.
- T019 and T020 can run in parallel because they cover artifact review and code guardrail review separately.

## Parallel Example

```bash
# Inspect independent evidence surfaces:
Task: "T004 Inspect unit tests in tests/unit/workflows/tasks/test_prepared_context.py and tests/unit/agents/codex_worker/test_attachment_materialization.py"
Task: "T005 Inspect integration coverage in tests/integration/workflows/temporal/workflows/test_run_target_aware_inputs.py"
Task: "T006 Inspect implementation surfaces in moonmind/workflows/tasks/prepared_context.py and moonmind/agents/codex_worker/worker.py"
```

## Implementation Strategy

1. Preserve the single-story `MM-648` scope and do not generate future stories.
2. Treat FR-001 through FR-010, SC-001 through SC-005, DESIGN-REQ-002, DESIGN-REQ-020, and DESIGN-REQ-029 as implemented-verified per `plan.md`.
3. Preserve the integration-level preparation failure evidence for SC-003 in `tests/integration/workflows/temporal/workflows/test_run_target_aware_inputs.py`.
4. If focused unit or integration tests fail for real behavior gaps, run only the corresponding conditional repair tasks.
5. Keep binary content out of workflow-visible payloads and preserve stable step-ref binding.
6. Run final unit/integration validation or document environment blockers.
7. Run `/moonspec-verify` and preserve `MM-648`, DESIGN-REQ-002, DESIGN-REQ-020, and DESIGN-REQ-029 in final evidence.

## Coverage Inventory

- FR-001, FR-008, DESIGN-REQ-002: T004, T006, T007, T013, T016, T017, T020, T021.
- FR-002, FR-003, FR-004, DESIGN-REQ-020: T004, T006, T008, T014, T016, T017, T021.
- FR-005, SC-004: T005, T009, T015, T018, T021.
- FR-007, SC-003: T004, T006, T008, T010, T012, T014, T015, T016, T018, T021.
- FR-006, FR-009, SC-002, DESIGN-REQ-029: T004, T006, T007, T008, T013, T014, T016, T021.
- FR-010, SC-005: T001, T002, T003, T019, T021.
- SC-001: T007, T008, T011, T016, T021.

## Notes

- The task list intentionally preserves the red-first SC-003 integration evidence added during implementation.
- Compose-backed integration remains the preferred hermetic integration command; focused pytest fallbacks are valid only when Docker is unavailable in the managed environment.
