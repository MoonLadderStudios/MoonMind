# Tasks: Claude Policy Envelope

**Input**: `specs/185-claude-policy-envelope/spec.md`, `specs/185-claude-policy-envelope/plan.md`, `specs/185-claude-policy-envelope/research.md`, `specs/185-claude-policy-envelope/data-model.md`, `specs/185-claude-policy-envelope/contracts/claude-policy-envelope.md`  
**Prerequisites**: Python dependencies installed for the repo test runner  
**Unit test command**: `pytest tests/unit/schemas/test_claude_policy_envelope.py -q`  
**Integration test command**: `pytest tests/integration/schemas/test_claude_policy_envelope_boundary.py -q`  
**Final unit runner**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`

## Source Traceability Summary

- FR-001 through FR-013 map to MM-343 and DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-010, DESIGN-REQ-024, DESIGN-REQ-028, and DESIGN-REQ-030.
- Acceptance scenarios cover managed-source precedence, lower-scope observability, fail-closed startup, security-dialog handshake, BootstrapPreferences semantics, and governance metadata.
- Contract coverage comes from `contracts/claude-policy-envelope.md`.

## Phase 1: Setup

- [X] T001 Confirm active feature artifacts exist in `specs/185-claude-policy-envelope/spec.md`, `specs/185-claude-policy-envelope/plan.md`, `specs/185-claude-policy-envelope/research.md`, `specs/185-claude-policy-envelope/data-model.md`, and `specs/185-claude-policy-envelope/contracts/claude-policy-envelope.md`.
- [X] T002 [P] Create unit schema test file for MM-343 at `tests/unit/schemas/test_claude_policy_envelope.py`.
- [X] T003 [P] Create integration schema boundary test file for MM-343 at `tests/integration/schemas/test_claude_policy_envelope_boundary.py`.

## Phase 2: Foundational

- [X] T004 Inspect MM-342 Claude session core contracts in `moonmind/schemas/managed_session_models.py` for compatible session-id and compact-payload patterns.
- [X] T005 Inspect schema exports in `moonmind/schemas/__init__.py` so MM-343 policy contracts are exposed through the same public schema boundary.

## Phase 3: Story - Claude Policy Envelope

**Summary**: Add runtime-validatable Claude policy-envelope contracts and deterministic policy-resolution behavior for MM-343.  
**Independent Test**: Feed fixture policy sources through the policy boundary and assert compiled envelopes, handshakes, events, precedence, fail-closed behavior, dialog requirements, and BootstrapPreferences semantics.  
**Traceability IDs**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, FR-012, FR-013, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, SC-007, DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-010, DESIGN-REQ-024, DESIGN-REQ-028, DESIGN-REQ-030.

### Unit Test Plan

- Validate server-managed versus endpoint-managed precedence.
- Validate lower-scope sources remain observability-only.
- Validate fetch states, fail-closed behavior, and policy handshake states.
- Validate security-dialog-required behavior for risky managed controls in interactive and non-interactive sessions.
- Validate BootstrapPreferences are represented as bootstrap templates only.
- Validate provider mode, trust level, managed source kind, fetch state, version, and visibility metadata.

### Integration Test Plan

- Exercise the public `moonmind.schemas` boundary with server-managed, endpoint-managed, cache-hit, fetch-failed, fail-closed, security-dialog, non-interactive blocked, and BootstrapPreferences scenarios.
- Assert serialized camelCase aliases and compact event/envelope payloads match the contract.

- [X] T006 [P] Add failing unit tests for managed-source precedence covering FR-002, FR-005, FR-006, DESIGN-REQ-007, DESIGN-REQ-008, SC-001, and SC-002 in `tests/unit/schemas/test_claude_policy_envelope.py`.
- [X] T007 [P] Add failing unit tests for lower-scope observability-only behavior covering FR-007, DESIGN-REQ-007, and SC-003 in `tests/unit/schemas/test_claude_policy_envelope.py`.
- [X] T008 [P] Add failing unit tests for fetch states and fail-closed handshake behavior covering FR-004, FR-012, DESIGN-REQ-008, and SC-005 in `tests/unit/schemas/test_claude_policy_envelope.py`.
- [X] T009 [P] Add failing unit tests for security-dialog and non-interactive blocked behavior covering FR-011, DESIGN-REQ-010, and SC-001 in `tests/unit/schemas/test_claude_policy_envelope.py`.
- [X] T010 [P] Add failing unit tests for BootstrapPreferences-as-template and provider trust metadata covering FR-008, FR-009, DESIGN-REQ-009, DESIGN-REQ-024, and SC-006 in `tests/unit/schemas/test_claude_policy_envelope.py`.
- [X] T011 [P] Add failing integration-style boundary tests for public schema imports, camelCase serialization, events, visibility metadata, and scenario matrix coverage for all acceptance scenarios in `tests/integration/schemas/test_claude_policy_envelope_boundary.py`.
- [X] T012 Run `pytest tests/unit/schemas/test_claude_policy_envelope.py tests/integration/schemas/test_claude_policy_envelope_boundary.py -q` and confirm the new tests fail before production implementation.
- [X] T013 Implement Claude policy literals, policy source, envelope, handshake, event, and nested policy control models in `moonmind/schemas/managed_session_models.py` for FR-001 through FR-013.
- [X] T014 Implement deterministic `resolve_claude_policy_envelope` behavior in `moonmind/schemas/managed_session_models.py` for managed-source precedence, fail-closed behavior, security dialog, events, visibility, and BootstrapPreferences semantics.
- [X] T015 Export MM-343 Claude policy contracts and resolver from `moonmind/schemas/__init__.py`.
- [X] T016 Run `pytest tests/unit/schemas/test_claude_policy_envelope.py tests/integration/schemas/test_claude_policy_envelope_boundary.py -q` and confirm focused tests pass.
- [X] T017 Run existing MM-342 Claude session core tests with `pytest tests/unit/schemas/test_claude_managed_session_models.py tests/integration/schemas/test_claude_managed_session_boundary.py -q` to confirm no session-core regression.

## Final Phase: Polish And Verification

- [X] T018 Review `specs/185-claude-policy-envelope/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/claude-policy-envelope.md`, and `tasks.md` for traceability drift after implementation.
- [X] T019 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` for required final unit verification.
- [X] T020 Run `./tools/test_integration.sh` when Docker is available, or record the exact `/var/run/docker.sock` blocker if Docker is unavailable. Blocked in this managed container because Docker reported `dial unix /var/run/docker.sock: connect: no such file or directory`.
- [X] T021 Run final `/moonspec-verify` equivalent against `specs/185-claude-policy-envelope/spec.md` and record whether MM-343 is fully implemented.

## Dependencies And Execution Order

- T001-T005 complete before story tests.
- T006-T011 can be written in parallel but must all complete before T012.
- T012 must confirm red-first failure before T013, T014, and T015.
- T016 and T017 must pass before final verification tasks.
- T018-T021 complete after focused implementation validation.

## Parallel Examples

- T006 and T011 can be drafted independently because unit and integration tests live in different files.
- T002 and T003 can run in parallel because they create different test files.
- T013 and T015 should not run in parallel because exports depend on implemented model names.

## Implementation Strategy

Follow TDD: write unit and integration-style boundary tests first, confirm failure, implement the minimal policy contracts and resolver, then rerun focused tests and required runners. Keep scope limited to MM-343 policy-envelope compilation and handshake state; do not implement live Claude provider fetching, per-action decision resolution, hook runtime invocation, or context compaction.
