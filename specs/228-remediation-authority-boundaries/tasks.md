# Tasks: Remediation Authority Boundaries

**Input**: Design documents from `/specs/228-remediation-authority-boundaries/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: TDD-first runtime implementation. Add focused unit and service-boundary tests in the existing remediation context test file before adding the new production authority boundary.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py`
- Integration tests: service-boundary flow in `tests/unit/workflows/temporal/test_remediation_context.py`; no compose-backed integration required for this slice
- Final verification: `/moonspec-verify`

**Source Traceability**: The original MM-453 Jira preset brief is preserved in `specs/228-remediation-authority-boundaries/spec.md`. Tasks cover FR-001 through FR-018, SC-001 through SC-008, and DESIGN-REQ-010, DESIGN-REQ-011, and DESIGN-REQ-024.

## Phase 1: Setup

- [X] T001 Confirm active MM-453 feature artifacts exist in `specs/228-remediation-authority-boundaries/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/remediation-authority-boundaries.md`, and `quickstart.md`.
- [X] T002 Confirm the MM-453 source preset brief and Jira key are preserved in `specs/228-remediation-authority-boundaries/spec.md`.
- [X] T003 Record the branch-gated agent-context script limitation from `.specify/scripts/bash/update-agent-context.sh` in final verification notes if it remains unavailable.

## Phase 2: Foundational

- [X] T004 Review existing remediation mode and action policy validation in `moonmind/workflows/temporal/service.py` for FR-001, FR-004, FR-012, DESIGN-REQ-010, and DESIGN-REQ-011.
- [X] T005 Review existing evidence and action-preparation boundaries in `moonmind/workflows/temporal/remediation_tools.py` for FR-014 and FR-018.
- [X] T006 Review existing redaction helpers in `moonmind/utils/logging.py` for FR-015 and FR-017.

## Phase 3: Govern Remediation Authority

**Summary**: Enforce remediation authority through explicit modes, permissions, named security profiles, high-risk approvals, idempotency, audit output, and redaction before side-effecting remediation actions are executable.

**Independent Test**: Configure remediation submissions and action requests for each authority mode, permission level, and risk level, then verify the system allows, gates, audits, rejects, or redacts the request according to the selected mode and security policy without exposing raw secrets or durable raw access material.

**Traceability IDs**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, FR-012, FR-013, FR-014, FR-015, FR-016, FR-017, FR-018, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, SC-007, SC-008, DESIGN-REQ-010, DESIGN-REQ-011, DESIGN-REQ-024.

**Unit Test Plan**: Add tests for authority-mode decisions, profile validation, permission matrix behavior, action allowlist/risk policy, idempotency, audit output, redaction, fail-closed inputs, and raw-access denials.

**Integration Test Plan**: Add a service-boundary flow that creates a target execution, creates a remediation execution with authority policy, builds context, prepares a side-effecting action request, and evaluates the action authority decision.

- [X] T007 Add failing unit tests for `observe_only`, `approval_gated`, and `admin_auto` decision semantics in `tests/unit/workflows/temporal/test_remediation_context.py` covering FR-001 through FR-005, SC-001 through SC-003, and DESIGN-REQ-010.
- [X] T008 Add failing unit tests for security profile and permission matrix enforcement in `tests/unit/workflows/temporal/test_remediation_context.py` covering FR-006 through FR-008, SC-004, and DESIGN-REQ-011.
- [X] T009 Add failing unit tests for typed action allowlist, high-risk approval requirements, unsupported actions, and fail-closed validation in `tests/unit/workflows/temporal/test_remediation_context.py` covering FR-010, FR-012, SC-007, and DESIGN-REQ-011.
- [X] T010 Add failing unit tests for idempotency and remediation action audit output in `tests/unit/workflows/temporal/test_remediation_context.py` covering FR-009, FR-011, SC-005, and DESIGN-REQ-011.
- [X] T011 Add failing unit tests for redaction, raw-access denial, and no unauthorized direct-fetch leakage in `tests/unit/workflows/temporal/test_remediation_context.py` covering FR-013, FR-015, FR-016, FR-017, SC-006, SC-007, and DESIGN-REQ-024.
- [X] T012 Add failing service-boundary integration-style test in `tests/unit/workflows/temporal/test_remediation_context.py` covering context build, action preparation, and authority decision flow for FR-002 through FR-006 and SC-001 through SC-003.
- [X] T013 Confirm the focused tests from T007 through T012 fail for the expected missing behavior before production code changes.
- [X] T014 Implement typed remediation action authority models and policy catalog in `moonmind/workflows/temporal/remediation_actions.py` for FR-001, FR-004, FR-005, FR-010, and FR-012.
- [X] T015 Implement authority mode, permission, security profile, approval, high-risk, and idempotency evaluation in `moonmind/workflows/temporal/remediation_actions.py` for FR-002 through FR-011 and DESIGN-REQ-010 through DESIGN-REQ-011.
- [X] T016 Implement audit and redaction output handling in `moonmind/workflows/temporal/remediation_actions.py` for FR-009, FR-013, FR-015, FR-017, and DESIGN-REQ-024.
- [X] T017 Export the remediation action authority service from `moonmind/workflows/temporal/__init__.py` for contract visibility.
- [X] T018 Run focused unit verification with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py`.
- [X] T019 Story validation: verify MM-453 acceptance scenarios, FR-001 through FR-018, SC-001 through SC-008, and DESIGN-REQ-010, DESIGN-REQ-011, and DESIGN-REQ-024 against focused test results.

## Final Phase: Polish And Verification

- [X] T020 Run artifact alignment for `specs/228-remediation-authority-boundaries/spec.md`, `plan.md`, and `tasks.md`.
- [X] T021 Run full unit verification with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`.
- [X] T022 Run `/moonspec-verify` by auditing implementation against `specs/228-remediation-authority-boundaries/spec.md` and record the result.

## Dependencies And Execution Order

1. Setup tasks T001-T003 complete before foundational review.
2. Foundational tasks T004-T006 complete before writing red tests.
3. Test tasks T007-T012 complete and T013 confirms expected failures before implementation.
4. Implementation tasks T014-T017 complete before focused verification T018.
5. Story validation T019 completes before polish and final verification T020-T022.

## Implementation Strategy

Implement the smallest typed authority service that satisfies MM-453 without introducing a raw action executor or new persistent table. Existing create/link/context/evidence behavior remains in place; this story adds the missing decision boundary that future action execution must consume.
