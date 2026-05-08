# Tasks: Remediation Lock, Ledger, and Loop Guards

**Input**: Design documents from `specs/321-remediation-lock-ledger-guards/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/remediation-mutation-guard.md`, `quickstart.md`

**Tests**: Unit tests and integration tests are required. Existing plan evidence classifies all MM-621 rows as `implemented_verified`, so this task list preserves the TDD gate by auditing existing red-first coverage first, running focused verification before any code changes, and using conditional implementation tasks only if verification exposes drift.

**Organization**: One story phase only: Remediation Mutation Safety.

**Source Traceability**: Covers MM-621, FR-001 through FR-014, SCN-001 through SCN-006, SC-001 through SC-005, and DESIGN-REQ-011, DESIGN-REQ-018, DESIGN-REQ-019, DESIGN-REQ-025 from `spec.md`.

**Test Commands**:

Unit tests: `./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py`
Integration tests: `pytest tests/integration/temporal/test_remediation_action_contracts.py -m 'integration_ci' -q --tb=short`
Final unit suite: `./tools/test_unit.sh`
Final integration suite: `./tools/test_integration.sh`
Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

Tasks use the exact checklist format `- [ ] T### [P?] Description with file path` and include traceability IDs where applicable.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the active feature artifacts and validation commands before story work.

- [X] T001 Verify active MM-621 feature artifacts and `.specify/feature.json` point to `specs/321-remediation-lock-ledger-guards/spec.md`, `specs/321-remediation-lock-ledger-guards/plan.md`, `specs/321-remediation-lock-ledger-guards/research.md`, `specs/321-remediation-lock-ledger-guards/data-model.md`, `specs/321-remediation-lock-ledger-guards/contracts/remediation-mutation-guard.md`, and `specs/321-remediation-lock-ledger-guards/quickstart.md` for FR-014 and SC-005
- [X] T002 Confirm test commands and managed-branch helper blockers recorded in `specs/321-remediation-lock-ledger-guards/quickstart.md` and `specs/321-remediation-lock-ledger-guards/research.md` for MM-621 validation

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Confirm existing implementation and fixture surfaces before validating the single story.

**CRITICAL**: No conditional implementation work can begin until this phase confirms the current source/test files under review.

- [X] T003 Inspect existing guard implementation in `moonmind/workflows/temporal/remediation_actions.py` against `specs/321-remediation-lock-ledger-guards/contracts/remediation-mutation-guard.md` for FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, FR-012, FR-013, DESIGN-REQ-011, DESIGN-REQ-018, DESIGN-REQ-019, DESIGN-REQ-025
- [X] T004 Inspect persistent guard state fields in `api_service/db/models.py` and `api_service/migrations/versions/f2a3b4c5d6e7_remediation_guard_state.py` for FR-002, FR-006, FR-009, and DESIGN-REQ-019
- [X] T005 Inspect existing unit and integration fixtures in `tests/unit/workflows/temporal/test_remediation_context.py` and `tests/integration/temporal/test_remediation_action_contracts.py` for FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, FR-012, FR-013, SCN-001, SCN-002, SCN-003, SCN-004, SCN-005, SCN-006, SC-001, SC-002, SC-003, SC-004

**Checkpoint**: Existing implementation and test surfaces are identified; story verification can begin.

---

## Phase 3: Story - Remediation Mutation Safety

**Summary**: As the MoonMind control plane, I want remediation mutations guarded by exclusive locks, stable action idempotency, retry budgets, cooldowns, target-change checks, and nested-remediation limits so concurrent or repeated remediation cannot perform unsafe side effects.

**Independent Test**: Submit or simulate multiple remediation action requests for the same target execution, including duplicate requests, stale target evidence, lock conflicts, retry-budget exhaustion, and nested remediation, then verify only one authorized mutation path proceeds and all other cases produce durable bounded decisions without duplicated side effects.

**Traceability**: FR-001 through FR-014; SCN-001 through SCN-006; SC-001 through SC-005; DESIGN-REQ-011, DESIGN-REQ-018, DESIGN-REQ-019, DESIGN-REQ-025.

**Unit Test Plan**: Validate guard decision semantics for identity, exclusive locks, lock conflict/loss, idempotency keys, ledger duplicate and unsafe reuse, budgets, cooldowns, nested-remediation denial, target freshness, bounded reasons, and redaction in `tests/unit/workflows/temporal/test_remediation_context.py`.

**Integration Test Plan**: Validate remediation context, authority, mutation guard, action request/result artifacts, and verification artifacts compose through the hermetic integration boundary in `tests/integration/temporal/test_remediation_action_contracts.py`.

### Unit Tests (write/verify first)

- [X] T006 Audit existing red-first unit coverage in `tests/unit/workflows/temporal/test_remediation_context.py` for exclusive locks, lock recovery, lock loss, ledger duplicate decisions, unsafe idempotency reuse, budgets, cooldowns, nested remediation, target freshness, and redaction covering FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, FR-012, FR-013, SCN-001, SCN-002, SCN-003, SCN-004, SCN-005, SCN-006
- [X] T007 Run `./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py` and confirm current unit evidence passes for FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, FR-012, FR-013, SC-001, SC-002, SC-003, SC-004, DESIGN-REQ-011, DESIGN-REQ-018, DESIGN-REQ-019, DESIGN-REQ-025 before touching `moonmind/workflows/temporal/remediation_actions.py`

### Integration Tests (write/verify first)

- [X] T008 Audit existing integration coverage in `tests/integration/temporal/test_remediation_action_contracts.py` for remediation context, authority decision, mutation guard decision, action request/result artifacts, verification artifacts, and raw action rejection covering DESIGN-REQ-011 and DESIGN-REQ-019
- [X] T009 Run `pytest tests/integration/temporal/test_remediation_action_contracts.py -m 'integration_ci' -q --tb=short` and confirm the action evidence boundary passes before touching `moonmind/workflows/temporal/remediation_tools.py` or `moonmind/workflows/temporal/remediation_context.py`

### Red-First Confirmation

- [X] T010 Confirm the MM-621 plan status remains `implemented_verified` in `specs/321-remediation-lock-ledger-guards/plan.md`; if T006 or T008 finds missing coverage, add the missing failing unit or integration test first in `tests/unit/workflows/temporal/test_remediation_context.py` or `tests/integration/temporal/test_remediation_action_contracts.py` before any production edit
- [X] T011 Confirm any newly added MM-621 test fails for the intended reason before production edits, or record in `specs/321-remediation-lock-ledger-guards/research.md` that no new red-first test was needed because existing implemented-verified coverage passed

### Conditional Implementation Tasks

- [X] T012 If T007 fails for guard decision semantics, update `moonmind/workflows/temporal/remediation_actions.py` to restore FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, FR-012, FR-013 behavior while preserving the v1 guard response contract in `specs/321-remediation-lock-ledger-guards/contracts/remediation-mutation-guard.md`
- [X] T013 If T007 exposes durable state drift, update `api_service/db/models.py` or `api_service/migrations/versions/f2a3b4c5d6e7_remediation_guard_state.py` only as needed to preserve lock and ledger durability for FR-006, FR-009, and DESIGN-REQ-019
- [X] T014 If T009 fails at the evidence boundary, update `moonmind/workflows/temporal/remediation_context.py` or `moonmind/workflows/temporal/remediation_tools.py` to preserve bounded context, action request/result artifacts, and verification artifacts for DESIGN-REQ-011 and DESIGN-REQ-019
- [X] T015 If T010 or T011 added failing tests, rerun `./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py` and `pytest tests/integration/temporal/test_remediation_action_contracts.py -m 'integration_ci' -q --tb=short` until MM-621 behavior passes in `tests/unit/workflows/temporal/test_remediation_context.py` and `tests/integration/temporal/test_remediation_action_contracts.py`

### Story Validation

- [X] T016 Validate the independent story using `specs/321-remediation-lock-ledger-guards/quickstart.md` and record whether lock conflict, duplicate ledger, stale target, budget, cooldown, missing freshness, self-target, nested remediation, and allowed action evidence outcomes satisfy FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, FR-012, FR-013, FR-014, SC-001, SC-002, SC-003, SC-004, SC-005

**Checkpoint**: The single MM-621 story is validated independently with unit and integration evidence.

---

## Phase 4: Polish And Verification

**Purpose**: Preserve traceability and run final verification without expanding scope.

- [X] T017 [P] Review `specs/321-remediation-lock-ledger-guards/spec.md`, `specs/321-remediation-lock-ledger-guards/plan.md`, `specs/321-remediation-lock-ledger-guards/research.md`, `specs/321-remediation-lock-ledger-guards/data-model.md`, `specs/321-remediation-lock-ledger-guards/contracts/remediation-mutation-guard.md`, and `specs/321-remediation-lock-ledger-guards/quickstart.md` to ensure MM-621 and the canonical Jira preset brief remain preserved for FR-014 and SC-005
- [X] T018 Run `./tools/test_unit.sh` for full unit-suite verification after focused MM-621 validation in `tests/unit/workflows/temporal/test_remediation_context.py`
- [ ] T019 Run `./tools/test_integration.sh` for full hermetic integration verification after focused MM-621 validation in `tests/integration/temporal/test_remediation_action_contracts.py`
- [ ] T020 Run `/moonspec-verify` for `specs/321-remediation-lock-ledger-guards/spec.md` after implementation and tests pass, comparing final evidence against the preserved MM-621 Jira preset brief

---

## Dependencies & Execution Order

### Phase Dependencies

Setup must finish before Foundational review. Foundational review must finish before Story verification. Story verification must finish before Polish and final `/moonspec-verify`.

### Within The Story

Unit coverage audit and focused unit verification precede conditional implementation. Integration coverage audit and focused integration verification also precede conditional implementation. Red-first confirmation tasks T010 and T011 gate all production edits. Conditional implementation tasks T012 through T014 are skipped when implemented-verified evidence passes. Story validation T016 runs after focused unit and integration evidence is available.

### Parallel Opportunities

T003, T004, and T005 can run in parallel after setup because they inspect different files. T006 and T008 can run in parallel because unit and integration coverage audits touch different test files. T012, T013, and T014 can run in parallel only if their respective verification failures are independent and touch the listed disjoint production files. T017 can run in parallel with final command preparation after story validation.

---

## Parallel Example: Story Phase

```bash
# Launch coverage audits together after Foundational review:
Task: "T006 audit tests/unit/workflows/temporal/test_remediation_context.py"
Task: "T008 audit tests/integration/temporal/test_remediation_action_contracts.py"
```

---

## Implementation Strategy

This is a verification-first task list because `plan.md` classifies every MM-621 row as `implemented_verified`. The default path is to confirm existing red-first unit and integration evidence, skip conditional implementation tasks when tests pass, run full unit and hermetic integration suites, and finish with `/moonspec-verify`. If any verification task fails or coverage is missing, add the missing failing test first, confirm the red state, then execute only the conditional implementation task needed for that failed requirement.

## Requirement Status Coverage Summary

Code-and-test work: 0 planned by default. Verification-only work: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, FR-012, FR-013, FR-014, SCN-001, SCN-002, SCN-003, SCN-004, SCN-005, SCN-006, SC-001, SC-002, SC-003, SC-004, SC-005, DESIGN-REQ-011, DESIGN-REQ-018, DESIGN-REQ-019, DESIGN-REQ-025. Conditional fallback implementation work: T012 through T014 only if focused verification fails. Already verified rows: all rows listed in `specs/321-remediation-lock-ledger-guards/plan.md`.

## Notes

The task list covers exactly one story. No P1/P2/P3 or multi-story phases are included. Final verification is T020 and uses `/moonspec-verify` as requested.
