# Tasks: Remediation Mutation Guards

**Input**: `specs/230-remediation-mutation-guards/spec.md`, `specs/230-remediation-mutation-guards/plan.md`
**Prerequisites**: `research.md`, `data-model.md`, `contracts/remediation-mutation-guards.md`, `quickstart.md`

## Validation Commands

- Unit: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py`
- Final unit suite: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- Integration: service-boundary coverage lives in the unit command above; no compose-backed provider test is required.

## Source Traceability Summary

- MM-455 is preserved in `spec.md`, `plan.md`, this task file, quickstart, implementation notes, verification output, commit text, and pull request metadata.
- `DESIGN-REQ-014`, `DESIGN-REQ-015`, `DESIGN-REQ-016`, and parts of `DESIGN-REQ-022`/`DESIGN-REQ-023` require new code and tests.
- Existing verified behavior from `RemediationActionAuthorityService` and `RemediationEvidenceToolService` must remain intact.

## Phase 1: Setup

- [X] T001 Confirm active feature locator points to `specs/230-remediation-mutation-guards` in `.specify/feature.json`
- [X] T002 Review existing remediation action/evidence helper imports and fixtures in `tests/unit/workflows/temporal/test_remediation_context.py`

## Phase 2: Foundational

- [X] T003 Define the mutation guard test fixture strategy in `tests/unit/workflows/temporal/test_remediation_context.py` using existing `_create_target_and_remediation`, `_admin_permissions`, and `_admin_profile` helpers
- [X] T004 Identify the production extension point in `moonmind/workflows/temporal/remediation_actions.py` so guard evaluation remains a decision boundary and does not execute side effects

## Phase 3: Story - Guard Remediation Mutations

**Summary**: Guard side-effecting remediation actions with locks, action-ledger idempotency, budgets, cooldowns, nested-remediation policy, and fresh target state before execution.

**Independent Test**: Create linked remediation executions for one target, attempt concurrent and duplicate side-effecting actions, exhaust retry and cooldown limits, attempt self/nested remediation, and mutate the target between diagnosis and action; verify the system allows only the valid guarded action and records explicit no-op, rejection, re-diagnosis, or escalation reasons.

**Traceability IDs**: FR-001 through FR-025, SC-001 through SC-010, DESIGN-REQ-009, DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-016, DESIGN-REQ-022, DESIGN-REQ-023

**Unit Test Plan**: Add focused tests in `tests/unit/workflows/temporal/test_remediation_context.py` for lock, ledger, budget, cooldown, nested remediation, target freshness, and redaction-safe serialization.

**Integration Test Plan**: Use async DB-backed service-boundary tests in `tests/unit/workflows/temporal/test_remediation_context.py` to prove guard evaluation consumes real remediation links and target records.

- [X] T005 Add failing unit tests for exclusive default `target_execution` lock conflict, idempotent reacquisition, stale recovery, and lock-loss denial in `tests/unit/workflows/temporal/test_remediation_context.py` (FR-004 through FR-009, SC-001, SC-002, DESIGN-REQ-014)
- [X] T006 Add failing unit tests for action-ledger duplicate replay, missing idempotency key, and unsafe idempotency-key reuse in `tests/unit/workflows/temporal/test_remediation_context.py` (FR-010 through FR-012, SC-003, SC-004, DESIGN-REQ-015)
- [X] T007 Add failing unit tests for max actions per target, max attempts per action kind, cooldown denial, and terminal escalation reasons in `tests/unit/workflows/temporal/test_remediation_context.py` (FR-013 through FR-016, SC-005, DESIGN-REQ-016)
- [X] T008 Add failing unit tests for self-target rejection, default nested-remediation rejection, explicit nested-policy allowance, and default self-healing depth in `tests/unit/workflows/temporal/test_remediation_context.py` (FR-017, FR-018, SC-006, DESIGN-REQ-016)
- [X] T009 Add failing service-boundary tests for target freshness guard decisions using `RemediationEvidenceToolService.prepare_action_request()` output in `tests/unit/workflows/temporal/test_remediation_context.py` (FR-019 through FR-022, SC-007, SC-008, DESIGN-REQ-022)
- [X] T010 Add failing unit tests for redaction-safe mutation guard serialization in `tests/unit/workflows/temporal/test_remediation_context.py` (FR-023, SC-009, DESIGN-REQ-009, DESIGN-REQ-023)
- [X] T011 Run targeted remediation tests and confirm the new guard tests fail before implementation: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py`
- [X] T012 Add `RemediationMutationGuardPolicy`, lock, ledger, budget, target freshness, and result dataclasses to `moonmind/workflows/temporal/remediation_actions.py` (FR-004 through FR-023)
- [X] T013 Implement `RemediationMutationGuardService` lock evaluation, idempotent acquisition, stale recovery, and lock-loss denial in `moonmind/workflows/temporal/remediation_actions.py` (FR-004 through FR-009, DESIGN-REQ-014)
- [X] T014 Implement action-ledger duplicate replay and unsafe idempotency-key reuse detection in `moonmind/workflows/temporal/remediation_actions.py` (FR-010 through FR-012, DESIGN-REQ-015)
- [X] T015 Implement action budgets, per-kind attempt limits, cooldown windows, and terminal escalation reasons in `moonmind/workflows/temporal/remediation_actions.py` (FR-013 through FR-016, DESIGN-REQ-016)
- [X] T016 Implement self-target, nested-remediation, and self-healing-depth guard checks in `moonmind/workflows/temporal/remediation_actions.py` (FR-017, FR-018, DESIGN-REQ-016)
- [X] T017 Implement target-freshness guard decisions consuming `RemediationTargetHealthSnapshot` data in `moonmind/workflows/temporal/remediation_actions.py` (FR-019 through FR-022, DESIGN-REQ-022)
- [X] T018 Implement redaction-safe `to_dict()` output for lock, ledger, budget, cooldown, nested-remediation, and target-freshness guard results in `moonmind/workflows/temporal/remediation_actions.py` (FR-023, FR-025, DESIGN-REQ-023)
- [X] T019 Preserve raw-access denial and existing action authority behavior while integrating guard helpers in `moonmind/workflows/temporal/remediation_actions.py` (FR-003, FR-024)
- [X] T020 Run targeted remediation tests until they pass: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py`
- [X] T021 Update `specs/230-remediation-mutation-guards/tasks.md` task checkboxes for completed implementation and validation work

## Final Phase: Polish And Verification

- [X] T022 Review `specs/230-remediation-mutation-guards/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/remediation-mutation-guards.md`, and `quickstart.md` for MM-455 and DESIGN-REQ traceability
- [X] T023 Run full unit verification: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- [X] T024 Run `/speckit.verify` equivalent read-only verification for `specs/230-remediation-mutation-guards/spec.md`

## Dependencies And Order

- T001 through T004 must complete before story tests.
- T005 through T010 must be written before T011.
- T011 must confirm red-first failure before T012 through T019.
- T020 must pass before T021 through T024.

## Parallel Examples

- T005, T006, T007, T008, T009, and T010 can be drafted independently if coordinated in the same test file.
- T013, T014, T015, T016, T017, and T018 touch the same production module and should be applied sequentially.

## Implementation Strategy

Start by proving the missing guard behavior with focused tests, then add a small in-service guard model in `remediation_actions.py`. Keep the guard as a decision boundary only: it returns executable/non-executable outcomes and never performs the underlying side effect.
