# Tasks: Prepare-Time Target-Aware Attachment Materialization

**Input**: Design documents from `specs/347-prepare-target-aware-attachments/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/prepared-attachment-manifest.md, quickstart.md

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement production code until they pass.

**Organization**: One story: `Target-Aware Attachment Preparation`.

**Source Traceability**: Preserves Jira issue `MM-648`, the canonical Jira preset brief, FR-001 through FR-010, SC-001 through SC-005, and DESIGN-REQ-002, DESIGN-REQ-020, DESIGN-REQ-029.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh`
- Integration tests: `./tools/test_integration.sh`
- Final verification: `/moonspec-verify` (`/speckit.verify` equivalent)

## Phase 1: Setup

- [X] T001 Confirm no existing `MM-648` feature artifacts and create `specs/347-prepare-target-aware-attachments/`. (FR-010, SC-005)
- [X] T002 Preserve the full `MM-648` Jira preset brief in `spec.md` and `.specify/feature.json`. (FR-010, SC-005)
- [X] T003 Inspect existing prepared context and worker materialization surfaces. (FR-001 through FR-009)

## Phase 2: Design

- [X] T004 Create `plan.md`, `research.md`, `data-model.md`, `contracts/prepared-attachment-manifest.md`, and `quickstart.md`. (FR-001 through FR-010)
- [X] T005 Confirm the plan uses no new persistent storage and keeps binary bytes out of workflow-visible payloads. (FR-001, FR-008, DESIGN-REQ-002)

## Phase 3: Story - Target-Aware Attachment Preparation

**Summary**: Preparation materializes objective and step attachments while preserving explicit target identity and rejecting ambiguous retargeting inputs.

**Independent Test**: Build mixed objective/step attachment payloads and verify manifest entries, materialized paths, status metadata, no inline payloads, and fail-fast behavior for step attachments without stable step refs.

### Unit Tests (write first)

- [X] T006 Add failing unit tests in `tests/unit/workflows/tasks/test_prepared_context.py` proving step attachments without stable `stepRef` fail and reorder/text edits preserve `stepRef` bindings. (FR-006, FR-009, SC-002, DESIGN-REQ-029)
- [X] T007 Add failing unit tests in `tests/unit/workflows/tasks/test_prepared_context.py` proving prepared manifest entries expose `workspacePath` and `status` metadata without binary content. (FR-003, FR-004, FR-008, DESIGN-REQ-020)
- [X] T008 Add failing worker tests in `tests/unit/agents/codex_worker/test_attachment_materialization.py` proving step attachment fallback by index is rejected and materialized entries include `status`. (FR-002, FR-004, FR-007, FR-009)

### Red-First Confirmation

- [X] T009 Run `./tools/test_unit.sh tests/unit/workflows/tasks/test_prepared_context.py tests/unit/agents/codex_worker/test_attachment_materialization.py` and confirm new tests fail for missing behavior. (T006-T008)

### Implementation

- [X] T010 Update `moonmind/workflows/tasks/prepared_context.py` to require stable step refs for step attachments and include bounded `workspacePath`/`status` metadata in prepared entries. (FR-003, FR-004, FR-006, FR-008, FR-009)
- [X] T011 Update `moonmind/agents/codex_worker/worker.py` to reject step-scoped attachment materialization when the step lacks a stable reference and mark successful manifest entries as prepared. (FR-002, FR-004, FR-007, FR-009)

### Story Validation

- [X] T012 Run focused unit tests and fix failures. (FR-001 through FR-009)
- [X] T013 Run `./tools/test_unit.sh` for full unit verification. (FR-001 through FR-010)
- [X] T014 Run `./tools/test_integration.sh` or document exact blocker if unavailable. (SC-001, SC-003, SC-004)
  - Evidence: `./tools/test_integration.sh` was blocked by Docker administrative policy (`403 Forbidden`) while Compose tried to build the pytest image. Supplemental local focused integration check passed: `pytest tests/integration/workflows/temporal/workflows/test_run_target_aware_inputs.py -q --tb=short`.

## Phase 4: Verification

- [X] T015 Run `/moonspec-verify` against `MM-648`, the preserved brief, source mappings, tests, and implementation evidence. (FR-010, SC-005)

## Coverage Inventory

- FR-001, FR-008, DESIGN-REQ-002: T005, T007, T010, T012-T015.
- FR-002, FR-003, FR-004, FR-005, FR-007, DESIGN-REQ-020: T007, T008, T010-T014.
- FR-006, FR-009, DESIGN-REQ-029: T006, T008, T010-T014.
- FR-010, SC-005: T001, T002, T015.
