# Tasks: Claude Session Core

**Input**: `specs/179-claude-session-core/spec.md`, `specs/179-claude-session-core/plan.md`, `specs/179-claude-session-core/research.md`, `specs/179-claude-session-core/data-model.md`, `specs/179-claude-session-core/contracts/claude-managed-session-core.md`  
**Prerequisites**: Python dependencies installed for the repo test runner  
**Unit test command**: `pytest tests/unit/schemas/test_claude_managed_session_models.py -q`  
**Integration test command**: `pytest tests/integration/schemas/test_claude_managed_session_boundary.py -q`  
**Final unit runner**: `./tools/test_unit.sh`

## Source Traceability Summary

- FR-001 through FR-009 map to MM-342 and DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-026, DESIGN-REQ-027, and DESIGN-REQ-028.
- Acceptance scenarios cover local session creation, Remote Control projection, cloud handoff, lifecycle validation, and rejection of Codex thread aliases.
- Contract coverage comes from `contracts/claude-managed-session-core.md`.

## Phase 1: Setup

- [X] T001 Confirm active feature artifacts exist in `specs/179-claude-session-core/spec.md`, `specs/179-claude-session-core/plan.md`, `specs/179-claude-session-core/data-model.md`, and `specs/179-claude-session-core/contracts/claude-managed-session-core.md`.
- [X] T002 Create integration schema test directory if missing at `tests/integration/schemas/`.

## Phase 2: Foundational

- [X] T003 Inspect existing managed-session validation helpers in `moonmind/schemas/managed_session_models.py` and `moonmind/schemas/_validation.py` for reuse without changing Codex behavior.
- [X] T004 Inspect existing managed-session tests in `tests/unit/schemas/test_managed_session_models.py` to preserve current Codex contract behavior.

## Phase 3: Story - Claude Session Core Schema

**Summary**: Add runtime-validatable Claude Code managed-session core contracts using shared Managed Session Plane vocabulary.  
**Independent Test**: Create local, cloud, SDK, scheduled, Remote Control, and handoff-shaped Claude sessions through the schema boundary and assert normalized `session_id` records without Codex thread aliases.  
**Traceability IDs**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, SC-001, SC-002, SC-003, SC-004, SC-005, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-026, DESIGN-REQ-027, DESIGN-REQ-028.

### Unit Test Plan

- Validate local, Remote Control, cloud, scheduled, SDK, and handoff session shapes.
- Validate accepted and rejected lifecycle states for session, turn, work item, and surface records.
- Validate `session_id` naming and rejection of `threadId`, `thread_id`, `childThread`, and `child_thread`.
- Validate Codex managed-session behavior still passes existing tests.

### Integration Test Plan

- Exercise the schema boundary with all documented session shapes in one integration-style test and assert lineage/projection invariants.

- [X] T005 [P] Add failing unit tests for Claude session shape validation covering FR-001, FR-002, FR-004, DESIGN-REQ-001, DESIGN-REQ-003, and SC-001 in `tests/unit/schemas/test_claude_managed_session_models.py`.
- [X] T006 [P] Add failing unit tests for Remote Control projection and cloud handoff covering FR-005, FR-006, DESIGN-REQ-027, DESIGN-REQ-028, and SC-004 in `tests/unit/schemas/test_claude_managed_session_models.py`.
- [X] T007 [P] Add failing unit tests for lifecycle validation covering FR-007, DESIGN-REQ-026, and SC-002 in `tests/unit/schemas/test_claude_managed_session_models.py`.
- [X] T008 [P] Add failing unit tests for `session_id` naming and Codex alias rejection covering FR-003, FR-009, DESIGN-REQ-002, DESIGN-REQ-028, and SC-003 in `tests/unit/schemas/test_claude_managed_session_models.py`.
- [X] T009 [P] Add failing integration-style schema boundary tests for all documented session shapes covering all acceptance scenarios and SC-001 through SC-005 in `tests/integration/schemas/test_claude_managed_session_boundary.py`.
- [X] T010 Run `pytest tests/unit/schemas/test_claude_managed_session_models.py tests/integration/schemas/test_claude_managed_session_boundary.py -q` and confirm the new tests fail before production implementation.
- [X] T011 Implement Claude managed-session literals, `ClaudeSurfaceBinding`, `ClaudeManagedSession`, `ClaudeManagedTurn`, and `ClaudeManagedWorkItem` in `moonmind/schemas/managed_session_models.py` for FR-001 through FR-009.
- [X] T012 Export Claude managed-session models and aliases from `moonmind/schemas/__init__.py` for contract accessibility.
- [X] T013 Run `pytest tests/unit/schemas/test_claude_managed_session_models.py tests/integration/schemas/test_claude_managed_session_boundary.py -q` and confirm the focused tests pass.
- [X] T014 Run existing Codex managed-session schema tests with `pytest tests/unit/schemas/test_managed_session_models.py -q` to confirm Codex behavior was not regressed.

## Final Phase: Polish And Verification

- [X] T015 Review `specs/179-claude-session-core/spec.md`, `plan.md`, `data-model.md`, `contracts/claude-managed-session-core.md`, and `tasks.md` for traceability drift after implementation.
- [X] T016 Run `./tools/test_unit.sh` for required final unit verification.
- [X] T017 Run `./tools/test_integration.sh` when Docker is available, or record the exact blocker if Docker is unavailable. Blocked in this managed container because `/var/run/docker.sock` is unavailable.
- [X] T018 Run final `/speckit.verify` equivalent against `specs/179-claude-session-core/spec.md` and record whether MM-342 is fully implemented.

## Dependencies And Execution Order

- T001-T004 complete before story tests.
- T005-T009 can be written in parallel but must all complete before T010.
- T010 must confirm red-first failure before T011 and T012.
- T013 and T014 must pass before final verification tasks.

## Parallel Examples

- T005 and T009 can be drafted independently because unit and integration tests live in different files.
- T011 and T012 should not run in parallel because exports depend on implemented model names.

## Implementation Strategy

Follow TDD: write the tests first, confirm failure, implement the minimal schema contracts and exports, then rerun focused tests and the repo unit runner. Keep scope limited to core schema contracts for STORY-001 and do not implement policy resolution, checkpoint restore behavior, subagent/team orchestration, or telemetry export.
