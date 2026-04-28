# Tasks: Scoped Override Persistence and Inheritance

**Input**: `specs/268-scoped-override-persistence/spec.md`, `specs/268-scoped-override-persistence/plan.md`
**Unit Test Command**: `pytest tests/unit/services/test_settings_catalog.py tests/unit/api_service/api/routers/test_settings_api.py -q`
**Integration Test Command**: `./tools/test_integration.sh` when Docker is available
**Source Traceability**: `MM-538`; FR-001 through FR-012; SC-001 through SC-006; DESIGN-REQ-006, DESIGN-REQ-017, DESIGN-REQ-026

## Phase 1: Setup

- [X] T001 Create `specs/268-scoped-override-persistence/` MoonSpec artifacts and preserve the MM-538 Jira preset brief in `spec.md`. (FR-012, SC-006)

## Phase 2: Foundational

- [X] T002 Add settings override and audit SQLAlchemy models plus Alembic migration in `api_service/db/models.py` and `api_service/migrations/versions/268_settings_overrides.py`. (FR-006, FR-009, DESIGN-REQ-017)
- [X] T003 Extend settings service construction and route dependency wiring so API routes can use an async database session in `api_service/services/settings_catalog.py` and `api_service/api/routers/settings.py`. (FR-001, FR-002, FR-003, FR-004)

## Phase 3: Story - Save, Inspect, and Reset Scoped Overrides

**Story Summary**: Settings clients can persist workspace and user overrides, inspect inheritance and version metadata, reset overrides safely, and reject stale or unsafe writes without partial persistence.

**Independent Test**: Patch workspace and user settings, verify effective inheritance and sources, reset overrides, confirm adjacent resources and audit rows remain, and validate stale version plus unsafe value failures.

**Traceability IDs**: FR-001 through FR-012; SC-001 through SC-006; DESIGN-REQ-006, DESIGN-REQ-017, DESIGN-REQ-026.

### Unit Test Plan

- [X] T004 [P] Add failing service tests for workspace override persistence, inherited effective values, and version metadata in `tests/unit/services/test_settings_catalog.py`. (FR-001, FR-002, FR-007, SC-001, DESIGN-REQ-006)
- [X] T005 [P] Add failing service tests for user override precedence, workspace inheritance, and intentional null override behavior in `tests/unit/services/test_settings_catalog.py`. (FR-003, FR-005, SC-002, DESIGN-REQ-006)
- [X] T006 [P] Add failing service tests for stale expected versions, batch atomicity, and unsafe override value rejection in `tests/unit/services/test_settings_catalog.py`. (FR-007, FR-008, FR-010, SC-004, SC-005, DESIGN-REQ-026)

### API Test Plan

- [X] T007 [P] Add failing API tests for PATCH workspace/user settings and DELETE reset behavior in `tests/unit/api_service/api/routers/test_settings_api.py`. (FR-002, FR-003, FR-004, FR-011, SC-003)
- [X] T008 [P] Add failing API tests for version conflict, no partial persistence, safe SecretRef persistence, and raw secret rejection in `tests/unit/api_service/api/routers/test_settings_api.py`. (FR-007, FR-008, FR-010, FR-011, SC-004, SC-005)

### Implementation

- [X] T009 Implement settings override persistence, inheritance resolution, descriptor override metadata, and value validation in `api_service/services/settings_catalog.py`. (FR-001 through FR-010, DESIGN-REQ-006, DESIGN-REQ-017, DESIGN-REQ-026)
- [X] T010 Implement PATCH and DELETE settings routes with database sessions, atomic batches, structured errors, and reset responses in `api_service/api/routers/settings.py`. (FR-002, FR-004, FR-007, FR-011)
- [X] T011 Run focused settings tests and fix defects while preserving test intent. (SC-001 through SC-005)

## Phase 4: Polish and Verification

- [X] T012 Run source traceability validation for `MM-538` and DESIGN-REQ-006/DESIGN-REQ-017/DESIGN-REQ-026 across feature artifacts. (FR-012, SC-006)
- [X] T013 Run `/moonspec-verify` and produce `verification.md` with final MoonSpec verification evidence. (FR-001 through FR-012, SC-001 through SC-006)

## Dependencies and Execution Order

1. T001 before all other tasks.
2. T002 and T003 before implementation.
3. T004 through T008 before T009 and T010.
4. T011 after implementation.
5. T012 and T013 after tests pass.

## Parallel Examples

```text
T004, T005, and T006 can run in parallel because they add service assertions for different behavior.
T007 and T008 can run in parallel with service tests because they cover API behavior.
```

## Implementation Strategy

This story requires code and tests. Follow TDD ordering: add failing service/API tests first, confirm the targeted failures, implement storage and routes, rerun focused tests, then complete final verification.
