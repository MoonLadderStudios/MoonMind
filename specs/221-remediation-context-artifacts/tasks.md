# Tasks: Remediation Context Artifacts

**Input**: Design documents from `/specs/221-remediation-context-artifacts/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests are required first. Integration coverage is represented by service/artifact boundary unit tests for this artifact-generation slice.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py tests/unit/workflows/temporal/test_temporal_service.py`
- Integration tests: Not required for this bounded service/artifact slice; no compose-backed boundary is introduced.
- Final verification: `/moonspec-verify`

## Phase 1: Setup

- [X] T001 Confirm active story artifacts and MM-432 traceability in `specs/221-remediation-context-artifacts/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/remediation-context-artifacts.md`, and `quickstart.md`.

## Phase 2: Foundational

- [X] T002 Add nullable context artifact reference storage in `api_service/db/models.py` and a new Alembic migration for FR-002.

## Phase 3: Build Bounded Remediation Context

**Summary**: Generate a bounded remediation context artifact from a persisted remediation link.

**Independent Test**: Create a target execution and remediation execution, run the context builder, then assert the generated artifact is linked, complete, bounded, and ref-only.

**Traceability IDs**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, DESIGN-REQ-006, DESIGN-REQ-011, DESIGN-REQ-019, DESIGN-REQ-022, DESIGN-REQ-023.

- [X] T003 Add failing unit tests for valid context artifact generation, execution linkage, remediation link context ref, target identity, selectors, refs, policies, and boundedness in `tests/unit/workflows/temporal/test_remediation_context.py` for FR-001 through FR-006.
- [X] T004 Add a failing unit test for non-remediation workflow IDs failing before artifact creation in `tests/unit/workflows/temporal/test_remediation_context.py` for FR-007.
- [X] T005 Implement `RemediationContextBuilder` in `moonmind/workflows/temporal/remediation_context.py` for FR-001 through FR-006.
- [X] T006 Export the builder through `moonmind/workflows/temporal/__init__.py` for service-boundary reuse.
- [X] T007 Run focused unit tests and update implementation until they pass for SC-001, SC-002, SC-003, and SC-004.

## Final Phase: Polish And Verification

- [X] T008 Run relevant unit verification from `specs/221-remediation-context-artifacts/quickstart.md`.
- [X] T009 Run `/moonspec-verify` by auditing implementation against `specs/221-remediation-context-artifacts/spec.md` and recording the result.
