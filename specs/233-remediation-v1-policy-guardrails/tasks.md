# Tasks: Remediation V1 Policy Guardrails

**Input**: `specs/233-remediation-v1-policy-guardrails/spec.md`, `specs/233-remediation-v1-policy-guardrails/plan.md`
**Prerequisites**: `research.md`, `data-model.md`, `contracts/remediation-v1-policy-guardrails.md`, `quickstart.md`

## Validation Commands

- Unit: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_temporal_service.py tests/unit/workflows/temporal/test_remediation_context.py`
- Final unit suite: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- Integration: service-boundary coverage lives in the unit command above; no compose-backed provider test is required.

## Source Traceability Summary

- MM-458 is preserved in `spec.md`, `plan.md`, this task file, quickstart, implementation notes, verification output, commit text, and pull request metadata.
- The plan originally classified FR-001, FR-002, FR-019, SC-001, SC-003, and DESIGN-REQ-016 as verification-first; the added tests now verify them without fallback implementation.
- The plan classifies raw capability denial and bounded outcome behavior as already implemented, with one added catalog metadata verification test.

## Phase 1: Setup

- [X] T001 Confirm active feature locator points to `specs/233-remediation-v1-policy-guardrails` in `.specify/feature.json`
- [X] T002 Review existing remediation create/action fixtures in `tests/unit/workflows/temporal/test_temporal_service.py` and `tests/unit/workflows/temporal/test_remediation_context.py`

## Phase 2: Foundational

- [X] T003 Identify policy-only create-time verification boundary in `moonmind/workflows/temporal/service.py` (FR-001, FR-002, SC-001, SC-003, DESIGN-REQ-016)
- [X] T004 Identify typed action capability metadata boundary in `moonmind/workflows/temporal/remediation_actions.py` (FR-004 through FR-006, FR-018, SC-002, DESIGN-REQ-024)

## Phase 3: Story - Enforce Remediation V1 Policy Guardrails

**Summary**: Keep remediation v1 manual by default, keep future self-healing policy metadata inert unless explicitly bounded, deny raw admin capabilities, and preserve structured bounded outcomes.

**Independent Test**: Exercise remediation submission, policy parsing, action/tool exposure, UI-visible capability metadata, and failure/edge-case classification; verify v1 remains manual by default, unsupported raw admin requests fail closed, future self-healing policy is inert unless explicitly enabled, and every documented edge case produces a structured bounded outcome.

**Traceability IDs**: FR-001 through FR-021, SC-001 through SC-007, DESIGN-REQ-016, DESIGN-REQ-022, DESIGN-REQ-023, DESIGN-REQ-024

**Unit Test Plan**: Add focused verification tests in `tests/unit/workflows/temporal/test_temporal_service.py` and `tests/unit/workflows/temporal/test_remediation_context.py`.

**Integration Test Plan**: Use async DB-backed service-boundary unit tests to prove runtime behavior without provider credentials or compose-backed services.

- [X] T005 Add verification test that a task containing only `task.remediationPolicy` does not create a `TemporalExecutionRemediationLink` in `tests/unit/workflows/temporal/test_temporal_service.py` (FR-001, FR-002, FR-019, SC-001, SC-003, DESIGN-REQ-016)
- [X] T006 Add verification test that allowed remediation action metadata excludes raw host, Docker, SQL, storage, secret-read, and redaction-bypass capabilities in `tests/unit/workflows/temporal/test_remediation_context.py` (FR-004 through FR-006, FR-018, SC-002, DESIGN-REQ-024)
- [X] T007 Run targeted tests and treat failures as red-first evidence for fallback implementation: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_temporal_service.py tests/unit/workflows/temporal/test_remediation_context.py`
- [X] T008 Conditional fallback skipped because T005 passed; no `moonmind/workflows/temporal/service.py` production change required (FR-001, FR-002, FR-019)
- [X] T009 Conditional fallback skipped because T006 passed; no `moonmind/workflows/temporal/remediation_actions.py` production change required (FR-004 through FR-006, FR-018)
- [X] T010 Confirm existing remediation bounded-outcome coverage still passes in `tests/unit/workflows/temporal/test_remediation_context.py` (FR-011 through FR-017, SC-005, SC-006, DESIGN-REQ-022, DESIGN-REQ-023)
- [X] T011 Update this task file to mark completed verification and skipped fallback implementation decisions (FR-020, FR-021, SC-007)

## Final Phase: Polish And Verification

- [X] T012 Review `specs/233-remediation-v1-policy-guardrails/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/remediation-v1-policy-guardrails.md`, and `quickstart.md` for MM-458 and DESIGN-REQ traceability
- [X] T013 Run full unit verification: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- [X] T014 Run `/moonspec-verify` equivalent read-only verification for `specs/233-remediation-v1-policy-guardrails/spec.md`

## Dependencies And Order

- T001 through T004 must complete before story verification tests.
- T005 and T006 must be written before T007.
- T008 and T009 are conditional and run only if the verification tests fail.
- T010 and T011 must complete before final verification.

## Parallel Examples

- T005 and T006 can be drafted independently because they touch different test modules.
- T008 and T009 touch different production modules and can run independently if both are needed.

## Implementation Strategy

Use verification tests first because most MM-458 behavior appears present by absence or existing guardrails. Implement only the narrow fallback changes needed if those verification tests expose executable automatic remediation or raw capability metadata.
