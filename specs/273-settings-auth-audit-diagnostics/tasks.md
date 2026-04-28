# Tasks: Settings Authorization Audit Diagnostics

**Input**: `specs/273-settings-auth-audit-diagnostics/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/settings-audit-diagnostics-api.md`, `quickstart.md`

**Unit Test Command**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
**Targeted Iteration Command**: `pytest tests/unit/services/test_settings_catalog.py tests/unit/api_service/api/routers/test_settings_api.py -q`
**Integration Test Command**: `./tools/test_integration.sh` only if compose-backed behavior is touched

## Source Traceability Summary

- `MM-543` is preserved as the canonical Jira preset brief in `spec.md`.
- `DESIGN-REQ-014`: settings least-privilege permission categories.
- `DESIGN-REQ-015`: audit records, audit APIs, and redaction.
- `DESIGN-REQ-018`: fail-fast diagnostics and no sensitive fallback.
- `DESIGN-REQ-025`: backend authorization is authoritative; hidden UI is not a boundary.

## Story

As an auditor or authorized operator, I want settings changes and diagnostics to be protected by least-privilege permissions and redacted audit output so configuration activity is accountable without exposing sensitive values.

**Independent Test**: Exercise settings permission checks, audit record creation/retrieval, redaction policy, and diagnostics output for allowed and denied users against representative settings, secrets, provider profiles, and launch-readiness failures.

## Task Phases

### Phase 1: Setup

- [X] T001 Create MoonSpec feature directory and preserve MM-543 Jira preset brief in `specs/273-settings-auth-audit-diagnostics/spec.md`
- [X] T002 Create planning, research, data model, contract, and quickstart artifacts under `specs/273-settings-auth-audit-diagnostics/`

### Phase 2: Foundational Tests

- [X] T003 [P] Add failing service tests for settings permission taxonomy, audit output redaction, SecretRef metadata visibility, and diagnostics in `tests/unit/services/test_settings_catalog.py` (FR-001, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-018)
- [X] T004 [P] Add failing API tests for backend permission denial/allowance, audit route output, diagnostics route output, and ignored client-supplied descriptor metadata in `tests/unit/api_service/api/routers/test_settings_api.py` (FR-002, FR-004, FR-011, FR-012, DESIGN-REQ-025)
- [X] T005 Run targeted tests and confirm new tests fail for missing implementation: `pytest tests/unit/services/test_settings_catalog.py tests/unit/api_service/api/routers/test_settings_api.py -q`

### Phase 3: Story Implementation

- [X] T006 Add settings permission constants, request permission extraction, route action mapping, and authorization helpers in `api_service/services/settings_catalog.py` and `api_service/api/routers/settings.py` (FR-001, FR-002, FR-011, DESIGN-REQ-014, DESIGN-REQ-025)
- [X] T007 Extend settings audit service/read models and redaction decisions in `api_service/services/settings_catalog.py` (FR-003, FR-005, FR-006, FR-007, DESIGN-REQ-015)
- [X] T008 Add `/api/v1/settings/audit` route with key/scope filters, bounded limit, permission checks, and redacted output in `api_service/api/routers/settings.py` (FR-004, FR-005, FR-006, FR-007)
- [X] T009 Add diagnostics read model/service route for effective value source, read-only reason, validation/restart/readiness blockers, recent sanitized change context, and no-fallback behavior in `api_service/services/settings_catalog.py` and `api_service/api/routers/settings.py` (FR-008, FR-009, FR-010, DESIGN-REQ-018)
- [X] T010 Ensure settings patch handling ignores client-supplied descriptor, permission, redaction, or audit metadata in `api_service/api/routers/settings.py` (FR-012)

### Phase 4: Validation

- [X] T011 Run targeted tests until passing: `pytest tests/unit/services/test_settings_catalog.py tests/unit/api_service/api/routers/test_settings_api.py -q`
- [X] T012 Run final unit verification: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- [X] T013 Update task checkboxes and add verification notes preserving `MM-543` in `specs/273-settings-auth-audit-diagnostics/tasks.md`
- [X] T014 Run final `/moonspec-verify` equivalent and record verdict in `specs/273-settings-auth-audit-diagnostics/verification.md`

## Verification Notes

- Targeted test command passed on 2026-04-28: `pytest tests/unit/services/test_settings_catalog.py tests/unit/api_service/api/routers/test_settings_api.py -q` -> 39 passed.
- Final unit command passed on 2026-04-28: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` -> Python 4160 passed, 1 xpassed, 16 subtests passed; frontend 17 files and 460 tests passed.
- Traceability preserved for Jira issue `MM-543` and DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-018, DESIGN-REQ-025.

## Dependencies

- T003 and T004 can run in parallel after T001-T002.
- T006-T010 depend on failing tests from T003-T005.
- T011-T014 depend on implementation completion.

## Implementation Strategy

Deliver the smallest backend-centered slice that satisfies MM-543: explicit settings permissions, backend route enforcement, redacted audit read output, actionable diagnostics, and tests proving direct backend requests cannot bypass policy. Do not add frontend-only security assumptions.
