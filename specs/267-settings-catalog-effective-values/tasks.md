# Tasks: Settings Catalog and Effective Values

**Input**: `specs/267-settings-catalog-effective-values/spec.md`, `specs/267-settings-catalog-effective-values/plan.md`
**Unit Test Command**: `pytest tests/unit/services/test_settings_catalog.py tests/unit/api_service/api/routers/test_settings_api.py -q`
**Integration Test Command**: `./tools/test_integration.sh` when Docker is available
**Source Traceability**: `MM-537`; FR-001 through FR-010; SC-001 through SC-005; DESIGN-REQ-003, DESIGN-REQ-005, DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-022

## Phase 1: Setup

- [X] T001 Create `specs/267-settings-catalog-effective-values/` MoonSpec artifacts and preserve the MM-537 Jira preset brief in `spec.md`. (FR-010, SC-005)

## Phase 2: Foundational

- [X] T002 Identify existing settings/config and API router patterns in `moonmind/config/settings.py` and `api_service/main.py`. (FR-001, FR-008)
- [X] T003 Define the read-side data and API contract in `data-model.md` and `contracts/settings-catalog-effective-values.md`. (FR-001 through FR-009, DESIGN-REQ-005, DESIGN-REQ-022)

## Phase 3: Story - Read Settings Catalog and Effective Values

**Story Summary**: Backend clients can read catalog descriptors and effective-value explanations for explicitly exposed settings while unexposed or unsupported writes fail with structured errors.

**Independent Test**: Call the settings catalog and effective endpoints, verify descriptor metadata and source explanations, then attempt to write an unexposed setting and verify the structured `setting_not_exposed` error.

**Traceability IDs**: FR-001 through FR-010; SC-001 through SC-005; DESIGN-REQ-003, DESIGN-REQ-005, DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-022.

- [X] T004 [P] Add failing service tests for exposed descriptor metadata and unexposed-setting omission in `tests/unit/services/test_settings_catalog.py`. (FR-001, FR-002, FR-003, SC-001, DESIGN-REQ-003, DESIGN-REQ-005, DESIGN-REQ-007)
- [X] T005 [P] Add failing service tests for environment source explanations, inherited null diagnostics, and unresolved SecretRef diagnostics in `tests/unit/services/test_settings_catalog.py`. (FR-004, FR-005, SC-002, SC-003, DESIGN-REQ-008)
- [X] T006 [P] Add failing API route tests for catalog reads, effective unknown-key errors, unexposed write rejection, and scope filtering in `tests/unit/api_service/api/routers/test_settings_api.py`. (FR-001, FR-007, FR-009, SC-004, DESIGN-REQ-022)
- [X] T007 Implement `api_service/services/settings_catalog.py` with explicit registry entries, descriptor models, effective-value responses, diagnostics, and structured error models. (FR-001 through FR-009, DESIGN-REQ-003, DESIGN-REQ-005, DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-022)
- [X] T008 Implement `api_service/api/routers/settings.py` and register it from `api_service/main.py`. (FR-001, FR-004, FR-007, FR-009, DESIGN-REQ-022)
- [X] T009 Run focused unit/API tests and confirm they pass. (SC-001 through SC-004)

## Phase 4: Validation

- [X] T010 Run source traceability validation for `MM-537` and the in-scope DESIGN-REQ IDs across feature artifacts. (FR-010, SC-005)
- [X] T011 Produce `verification.md` with final MoonSpec verification evidence. (FR-001 through FR-010, SC-001 through SC-005)

## Dependencies and Execution Order

1. T001 before all other tasks.
2. T002 and T003 before implementation.
3. T004 through T006 before T007 and T008.
4. T009 after implementation.
5. T010 and T011 after tests pass.

## Parallel Examples

```text
T004 and T005 can run in parallel because they add separate service assertions.
T006 can run in parallel with T004/T005 because it covers API behavior.
```

## Implementation Strategy

This story is now implemented and verified. Future work for scoped override persistence must start from `MM-538` rather than expanding `MM-537`.
