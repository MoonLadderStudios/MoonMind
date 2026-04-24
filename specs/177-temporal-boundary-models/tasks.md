# Tasks: Temporal Boundary Models

**Input**: `specs/177-temporal-boundary-models/spec.md`, `specs/177-temporal-boundary-models/plan.md`, `specs/177-temporal-boundary-models/research.md`, `specs/177-temporal-boundary-models/data-model.md`, `specs/177-temporal-boundary-models/contracts/temporal-boundary-inventory.md`

**Unit Test Command**: `./tools/test_unit.sh tests/unit/schemas/test_temporal_boundary_models.py tests/unit/workflows/temporal/test_boundary_inventory.py`
**Integration Test Command**: `pytest tests/integration/temporal/test_temporal_boundary_inventory_contract.py -q --tb=short`

## Source Traceability Summary

- FR-001, FR-002, DESIGN-REQ-001, DESIGN-REQ-002: deterministic inventory and required request model ownership.
- FR-003: response/snapshot ownership or explicit rationale.
- FR-004, DESIGN-REQ-003: strict Pydantic v2 model validation.
- FR-005, DESIGN-REQ-007: approved schema homes.
- FR-006, DESIGN-REQ-008: named fields and explicit compatibility escape hatches.
- FR-007, DESIGN-REQ-021: feature-spec implementation tracking.
- FR-008: no Temporal name renames.

## Phase 1: Setup

- [X] T001 Create MoonSpec artifacts for MM-327 in `specs/177-temporal-boundary-models/`
- [X] T002 Point active feature metadata at `specs/177-temporal-boundary-models/` in `.specify/feature.json`

## Phase 2: Foundational

- [X] T003 Add failing schema tests for strict aliases, nonblank normalization, duplicate detection, and MM-327/TOOL preservation in `tests/unit/schemas/test_temporal_boundary_models.py` (FR-004, SC-002)
- [X] T004 Add failing inventory unit tests for activity, workflow, signal, update, query, and Continue-As-New coverage in `tests/unit/workflows/temporal/test_boundary_inventory.py` (FR-001, FR-002, FR-003, SC-001)
- [X] T005 Add failing integration boundary test comparing inventory names with the default activity catalog and known workflow message constants in `tests/integration/temporal/test_temporal_boundary_inventory_contract.py` (FR-008, SC-003)
- [X] T006 Run focused tests and confirm they fail before production implementation using `./tools/test_unit.sh tests/unit/schemas/test_temporal_boundary_models.py tests/unit/workflows/temporal/test_boundary_inventory.py` and `pytest tests/integration/temporal/test_temporal_boundary_inventory_contract.py -q --tb=short` (FR-004, FR-008)

## Phase 3: Story

**Story Summary**: Maintainers can inspect one deterministic Temporal boundary inventory that maps covered public Temporal boundaries to named Pydantic contract models and explicit compatibility status.

**Independent Test**: Focused schema and inventory tests prove the inventory is typed, strict, traceable to MM-327, covers the required boundary kinds, and preserves existing Temporal names.

**Traceability IDs**: FR-001 through FR-008; SC-001 through SC-004; DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-021.

**Unit Test Plan**: Validate Pydantic schema behavior and deterministic inventory coverage without Docker or credentials.

**Integration Test Plan**: Validate inventory activity names against `DEFAULT_ACTIVITY_CATALOG` and workflow/message names against constants/declarations.

- [X] T007 Implement strict boundary inventory schema models in `moonmind/schemas/temporal_boundary_models.py` (FR-002, FR-003, FR-004)
- [X] T008 Implement deterministic MM-327 boundary inventory in `moonmind/workflows/temporal/boundary_inventory.py` (FR-001, FR-002, FR-003, FR-005, FR-006, FR-008)
- [X] T009 Add a migration tracker for compatibility-sensitive and incomplete boundary modeling in `specs/177-temporal-boundary-models/research.md` (FR-007, SC-004)
- [X] T010 Run focused unit and integration boundary tests and validate the single story until they pass in `tests/unit/schemas/test_temporal_boundary_models.py`, `tests/unit/workflows/temporal/test_boundary_inventory.py`, and `tests/integration/temporal/test_temporal_boundary_inventory_contract.py` (SC-001, SC-002, SC-003)

## Phase 4: Polish And Verification

- [X] T011 Run full unit suite with `./tools/test_unit.sh` or document exact blocker (all FRs)
- [X] T012 Run hermetic integration suite with `./tools/test_integration.sh` or document exact blocker (all acceptance scenarios)
- [X] T013 Run `/moonspec-verify` equivalent against `specs/177-temporal-boundary-models/` and record verdict (all FRs)

## Dependencies And Execution Order

- T001-T002 are complete setup tasks.
- T003-T006 must precede production implementation.
- T007-T009 depend on the failing tests from T003-T006.
- T010 depends on T007-T009.
- T011-T013 depend on T010.

## Parallel Opportunities

- T003 and T004 can be drafted in parallel because they touch different test files.
- T007 and T009 can be implemented in parallel after failing tests are recorded because they touch production schema and feature research artifacts respectively.

## Implementation Strategy

1. Confirm the tests fail red-first.
2. Implement only enough schema and inventory code to satisfy MM-327.
3. Keep all Temporal names unchanged.
4. Use this feature’s MoonSpec artifacts for migration tracking rather than editing canonical Temporal docs.
5. Mark tasks complete only after the corresponding files and commands are complete or explicitly blocked.
