# Tasks: Claude Decision Pipeline

**Input**: `specs/185-claude-decision-pipeline/spec.md`, `specs/185-claude-decision-pipeline/plan.md`, `specs/185-claude-decision-pipeline/research.md`, `specs/185-claude-decision-pipeline/data-model.md`, `specs/185-claude-decision-pipeline/contracts/claude-decision-pipeline.md`  
**Prerequisites**: Python dependencies installed for the repo test runner  
**Unit test command**: `pytest tests/unit/schemas/test_claude_managed_session_models.py -q`  
**Integration test command**: `pytest tests/integration/schemas/test_claude_decision_pipeline_boundary.py -q`  
**Final unit runner**: `./tools/test_unit.sh`

## Source Traceability Summary

- FR-001 through FR-013 map to MM-344 and DESIGN-REQ-011, DESIGN-REQ-012, DESIGN-REQ-025, and DESIGN-REQ-028.
- Acceptance scenarios cover decision stage order, rule precedence, protected paths, sandbox substitution, classifier outcomes, headless resolution, interactive prompts, and hook audit records.
- Contract coverage comes from `contracts/claude-decision-pipeline.md`.

## Phase 1: Setup

- [X] T001 Confirm active feature artifacts exist in `specs/185-claude-decision-pipeline/spec.md`, `specs/185-claude-decision-pipeline/plan.md`, `specs/185-claude-decision-pipeline/data-model.md`, and `specs/185-claude-decision-pipeline/contracts/claude-decision-pipeline.md`.
- [X] T002 Confirm existing Claude core schema contracts from MM-342 are present in `moonmind/schemas/managed_session_models.py`.

## Phase 2: Foundational

- [X] T003 Inspect existing compact metadata validation helpers in `moonmind/schemas/_validation.py` and `moonmind/schemas/temporal_payload_policy.py`.
- [X] T004 Inspect existing Claude managed-session tests in `tests/unit/schemas/test_claude_managed_session_models.py` and `tests/integration/schemas/test_claude_managed_session_boundary.py`.

## Phase 3: Story - Claude Decision And Hook Provenance

**Summary**: Add runtime-validatable Claude DecisionPoint and HookAudit contracts with provenance and documented event vocabularies.  
**Independent Test**: Construct representative decision pipeline records and hook audit records, then assert stage order, provenance, outcomes, event names, and compact audit data.  
**Traceability IDs**: FR-001 through FR-013, SC-001 through SC-005, DESIGN-REQ-011, DESIGN-REQ-012, DESIGN-REQ-025, DESIGN-REQ-028.

### Unit Test Plan

- Validate all documented decision stages and decision event names.
- Validate protected-path, classifier, headless, and hook-tightened helper invariants.
- Validate HookAudit source scopes, outcomes, and bounded audit data.
- Validate existing Claude core and Codex managed-session behavior still passes existing tests.

### Integration Test Plan

- Exercise a representative decision sequence in canonical stage order and assert related HookAudit and DecisionPoint records remain normalized.

- [X] T005 [P] Add failing unit tests for decision stage order, decision events, DecisionPoint wire shape, and policy first-match provenance covering FR-001, FR-002, FR-003, FR-008, DESIGN-REQ-011, DESIGN-REQ-012, DESIGN-REQ-025, SC-001 in `tests/unit/schemas/test_claude_managed_session_models.py`.
- [X] T006 [P] Add failing unit tests for protected-path, sandbox-substitution, classifier, headless, and hook-tightened DecisionPoint invariants covering FR-004, FR-005, FR-006, FR-007, FR-010, DESIGN-REQ-012, SC-002 in `tests/unit/schemas/test_claude_managed_session_models.py`.
- [X] T007 [P] Add failing unit tests for HookAudit validation covering FR-011, FR-012, FR-013, DESIGN-REQ-028, SC-003 in `tests/unit/schemas/test_claude_managed_session_models.py`.
- [X] T008 [P] Add failing integration-style boundary tests for a representative end-to-end decision sequence and hook work-event validation covering all acceptance scenarios, FR-009, and SC-004 in `tests/integration/schemas/test_claude_decision_pipeline_boundary.py`.
- [X] T009 Run focused tests and confirm the new tests fail before production implementation.
- [X] T010 Implement Claude decision constants, type aliases, `ClaudeDecisionPoint`, and `ClaudeHookAudit` in `moonmind/schemas/managed_session_models.py`.
- [X] T011 Export Claude decision and hook models, constants, and aliases from `moonmind/schemas/__init__.py`.
- [X] T012 Run focused tests and confirm they pass.
- [X] T013 Run existing managed-session schema tests with `pytest tests/unit/schemas/test_managed_session_models.py -q` to confirm Codex behavior was not regressed.

## Final Phase: Polish And Verification

- [X] T014 Review `specs/185-claude-decision-pipeline/spec.md`, `plan.md`, `data-model.md`, `contracts/claude-decision-pipeline.md`, and `tasks.md` for traceability drift after implementation.
- [X] T015 Run `./tools/test_unit.sh` for required final unit verification. Passed with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`.
- [X] T016 Run `./tools/test_integration.sh` when Docker is available, or record the exact blocker if Docker is unavailable. Blocked in this managed container because `/var/run/docker.sock` is unavailable.
- [X] T017 Run final `/moonspec-verify` equivalent against `specs/185-claude-decision-pipeline/spec.md` and record whether MM-344 is fully implemented.

## Dependencies And Execution Order

- T001-T004 complete before story tests.
- T005-T008 can be written in parallel but must all complete before T009.
- T009 must confirm red-first failure before T010 and T011.
- T012 and T013 must pass before final verification tasks.

## Parallel Examples

- T005 and T008 can be drafted independently because unit and integration tests live in different files.
- T010 and T011 should not run in parallel because exports depend on implemented model names.

## Implementation Strategy

Follow TDD: write the tests first, confirm failure, implement the minimal schema contracts and exports, then rerun focused tests and the repo unit runner. Keep scope limited to decision and hook provenance contracts for STORY-003 and do not implement policy source resolution, checkpoint storage payloads, or team messaging.
