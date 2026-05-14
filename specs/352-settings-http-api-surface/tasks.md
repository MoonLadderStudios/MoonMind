# Tasks: Settings HTTP API Surface

**Input**: Design documents from `/work/agent_jobs/mm:d0605b15-f8b2-40f8-9e2f-a9ea20825eef/repo/specs/352-settings-http-api-surface/`
**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/settings-http-api-surface.md](./contracts/settings-http-api-surface.md), [quickstart.md](./quickstart.md)

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped around exactly one independently testable story: Operate Settings Through Canonical APIs.

**Source Traceability**: MM-657 and the original Jira preset brief are preserved in `spec.md`. Tasks cover FR-001 through FR-017, SCN-001 through SCN-008, SC-001 through SC-006, and DESIGN-REQ-001 through DESIGN-REQ-013.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- Focused unit tests: `pytest tests/unit/services/test_settings_catalog.py tests/unit/api_service/api/routers/test_settings_api.py -q`
- Integration tests: `./tools/test_integration.sh`
- Focused integration tests: `pytest tests/integration/api/test_settings_overrides_contract.py tests/integration/api/test_settings_effective_values_contract.py tests/integration/api/test_settings_http_api_surface_contract.py -m 'integration_ci' -q`
- Final verification: `/moonspec-verify`

## Requirement Status Summary

- Code-and-test work: FR-009, FR-010, FR-013, FR-016; SCN-005, SCN-006; SC-001, SC-004; DESIGN-REQ-005, DESIGN-REQ-007, DESIGN-REQ-010.
- Verification-first with conditional fallback: FR-002, FR-014, FR-017; SCN-001, SCN-008; SC-002, SC-006; DESIGN-REQ-001, DESIGN-REQ-008.
- Already verified and preserved through final validation: FR-001, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-011, FR-012, FR-015; SCN-002, SCN-003, SCN-004, SCN-007; SC-003, SC-005; DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-006, DESIGN-REQ-009, DESIGN-REQ-011, DESIGN-REQ-012, DESIGN-REQ-013.

## Phase 1: Setup

**Purpose**: Confirm the active MoonSpec artifacts and local test surfaces before writing tests.

- [ ] T001 Confirm `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/settings-http-api-surface.md`, and `quickstart.md` exist for MM-657 in specs/352-settings-http-api-surface/ (FR-017, SC-006)
- [ ] T002 [P] Review existing settings API route structure in api_service/api/routers/settings.py and note current catalog/effective/update/reset/audit endpoints before adding validate/preview routes (FR-001, FR-003, FR-005, FR-008, FR-011)
- [ ] T003 [P] Review existing settings service validation helpers in api_service/services/settings_catalog.py before adding validate/preview service behavior (FR-009, FR-010, FR-016)
- [ ] T004 [P] Review existing unit and integration fixtures in tests/unit/api_service/api/routers/test_settings_api.py, tests/unit/services/test_settings_catalog.py, tests/integration/api/test_settings_overrides_contract.py, and tests/integration/api/test_settings_effective_values_contract.py (SC-001)

---

## Phase 2: Foundational

**Purpose**: Add test harness support required before the story tests can be written.

**CRITICAL**: No production implementation work starts until Phase 3 red-first tests are written and confirmed failing.

- [ ] T005 Add shared unit-test helper assertions for override count, audit count, and sanitized SettingsError envelopes in tests/unit/api_service/api/routers/test_settings_api.py (FR-009, FR-010, FR-013, FR-015)
- [ ] T006 [P] Add shared service-test helper assertions for preview validation issues, reload metadata, and redacted diffs in tests/unit/services/test_settings_catalog.py (FR-010, FR-016)
- [ ] T007 [P] Create integration contract test file tests/integration/api/test_settings_http_api_surface_contract.py with existing AsyncClient/database fixture pattern from tests/integration/api/test_settings_overrides_contract.py (SC-001, DESIGN-REQ-001)

**Checkpoint**: Test fixtures are ready for red-first unit and integration tasks.

---

## Phase 3: Story - Operate Settings Through Canonical APIs

**Summary**: As a MoonMind operator or settings client, I want a complete settings API surface for catalog discovery, effective reads, updates, resets, validation, preview, and audit so configuration can be inspected and changed safely through one durable contract.

**Independent Test**: Exercise catalog, effective reads, validate, preview, update, reset, audit, structured errors, redaction, and no-commit behavior through the settings API.

**Traceability**: FR-001 through FR-017; SCN-001 through SCN-008; SC-001 through SC-006; DESIGN-REQ-001 through DESIGN-REQ-013; MM-657.

**Unit Test Plan**:

- Route unit tests for `POST /api/v1/settings/validate` and `POST /api/v1/settings/preview`.
- Service unit tests for non-committing validation, preview diffs, reload metadata, dependency warnings, broken references, and redaction.
- Error-envelope unit tests for the documented MM-657 matrix.

**Integration Test Plan**:

- API contract tests for all settings API families.
- Integration tests for validate/preview no-commit behavior.
- Integration tests for all three catalog sections and existing effective/update/reset/audit preservation.

### Unit Tests (write first)

- [ ] T008 Add failing unit tests for `POST /api/v1/settings/validate` success, permission denial, structured validation errors, no override mutation, and no audit mutation in tests/unit/api_service/api/routers/test_settings_api.py (FR-009, FR-014, FR-015, DESIGN-REQ-005, DESIGN-REQ-008)
- [ ] T009 Add failing unit tests for `POST /api/v1/settings/preview` effective-value diffs, dependency warnings, reload requirements, broken-reference diagnostics, redaction, no override mutation, and no audit mutation in tests/unit/api_service/api/routers/test_settings_api.py (FR-010, FR-016, DESIGN-REQ-005, DESIGN-REQ-010)
- [ ] T010 Add failing unit tests for service-level validation and preview response helpers in tests/unit/services/test_settings_catalog.py (FR-009, FR-010, FR-016, DESIGN-REQ-005, DESIGN-REQ-010)
- [ ] T011 Add failing unit tests for the MM-657 structured error-envelope matrix in tests/unit/api_service/api/routers/test_settings_api.py (FR-013, SC-004, DESIGN-REQ-007)

### Integration Tests (write first)

- [ ] T012 Add failing integration tests for `POST /api/v1/settings/validate` and `POST /api/v1/settings/preview` no-commit behavior in tests/integration/api/test_settings_http_api_surface_contract.py (SCN-005, FR-009, FR-010, DESIGN-REQ-005)
- [ ] T013 Add failing integration tests proving catalog responses cover `providers-secrets`, `user-workspace`, and `operations` in tests/integration/api/test_settings_http_api_surface_contract.py (SCN-001, SC-002, DESIGN-REQ-001)
- [ ] T014 Add failing integration tests proving all MM-657 API families are contract-covered without regressing effective/update/reset/audit behavior in tests/integration/api/test_settings_http_api_surface_contract.py (SC-001, FR-001, FR-003, FR-005, FR-008, FR-011)
- [ ] T015 Add failing integration tests for validate/preview permissions, secret redaction, missing references, policy-blocked diagnostics, and structured errors in tests/integration/api/test_settings_http_api_surface_contract.py (SCN-006, FR-013, FR-014, FR-015, FR-016)

### Red-First Confirmation

- [ ] T016 Run `pytest tests/unit/api_service/api/routers/test_settings_api.py tests/unit/services/test_settings_catalog.py -q` and confirm T008-T011 fail for missing validate/preview behavior in tests/unit/api_service/api/routers/test_settings_api.py and tests/unit/services/test_settings_catalog.py (FR-009, FR-010, FR-013, FR-016)
- [ ] T017 Run `pytest tests/integration/api/test_settings_http_api_surface_contract.py -m 'integration_ci' -q` and confirm T012-T015 fail for missing validate/preview behavior or missing contract proof in tests/integration/api/test_settings_http_api_surface_contract.py (SCN-001, SCN-005, SCN-006, SC-001)

### Conditional Verification Fallbacks

- [ ] T018 If T013 shows catalog section coverage is not present, update catalog section test fixtures or registry exposure in api_service/services/settings_catalog.py without changing already verified catalog semantics (FR-002, SC-002, DESIGN-REQ-001)
- [ ] T019 If T008 or T015 shows authorization gaps, apply the existing settings permission pattern to validate/preview routes in api_service/api/routers/settings.py (FR-014, DESIGN-REQ-008)
- [ ] T020 If T015 shows MM-657 traceability is not preserved in generated artifacts, update specs/352-settings-http-api-surface/tasks.md before implementation continues (FR-017, SC-006)

### Implementation

- [ ] T021 Add `SettingsValidationRequest`, `SettingsPreviewRequest`, validation response, preview response, diff, dependency warning, and reload requirement models in api_service/api/routers/settings.py or api_service/services/settings_catalog.py according to existing model boundaries (FR-009, FR-010, DESIGN-REQ-005)
- [ ] T022 Implement non-committing validation service behavior in api_service/services/settings_catalog.py using existing write validation rules without inserting settings_overrides rows or settings audit events (FR-009, FR-013, FR-015, DESIGN-REQ-005, DESIGN-REQ-007)
- [ ] T023 Implement non-committing preview service behavior in api_service/services/settings_catalog.py with proposed effective values, redacted diffs, dependency warnings, reload requirements, broken-reference diagnostics, and policy-blocked diagnostics (FR-010, FR-016, DESIGN-REQ-005, DESIGN-REQ-010)
- [ ] T024 Add `POST /api/v1/settings/validate` route in api_service/api/routers/settings.py with permission checks, scope coercion, structured SettingsError responses, and no-commit behavior (FR-009, FR-013, FR-014, FR-015)
- [ ] T025 Add `POST /api/v1/settings/preview` route in api_service/api/routers/settings.py with permission checks, scope coercion, structured SettingsError responses, redaction, and no-commit behavior (FR-010, FR-013, FR-014, FR-015, FR-016)
- [ ] T026 Align validate/preview error translation in api_service/api/routers/settings.py so unknown settings, non-exposed settings, invalid scopes, read-only/operator-locked settings, invalid values, unresolved SecretRefs, missing provider profiles, stale versions, permission failures, and confirmation-required cases use the shared SettingsError envelope (FR-013, SC-004, DESIGN-REQ-007)
- [ ] T027 Ensure validate/preview responses never expose raw secret plaintext or durable secret material in api_service/services/settings_catalog.py and api_service/api/routers/settings.py (FR-015, DESIGN-REQ-009)

### Story Validation

- [ ] T028 Run `pytest tests/unit/api_service/api/routers/test_settings_api.py tests/unit/services/test_settings_catalog.py -q` and make all MM-657 unit tests pass in tests/unit/api_service/api/routers/test_settings_api.py and tests/unit/services/test_settings_catalog.py (FR-009, FR-010, FR-013, FR-016)
- [ ] T029 Run `pytest tests/integration/api/test_settings_http_api_surface_contract.py tests/integration/api/test_settings_overrides_contract.py tests/integration/api/test_settings_effective_values_contract.py -m 'integration_ci' -q` and make all MM-657 integration tests pass in tests/integration/api/ (SCN-001 through SCN-007, SC-001 through SC-005)
- [ ] T030 Validate the independent story flow from specs/352-settings-http-api-surface/quickstart.md against api_service/api/routers/settings.py behavior (FR-001 through FR-016)

**Checkpoint**: The single MM-657 story is covered by red-first unit tests, red-first integration tests, implementation, and story validation.

---

## Phase 4: Polish And Verification

**Purpose**: Strengthen the completed story without adding hidden scope.

- [ ] T031 [P] Update specs/352-settings-http-api-surface/contracts/settings-http-api-surface.md if implementation chooses a more precise supported error-code mapping for MM-657 (FR-013, SC-004)
- [ ] T032 [P] Update specs/352-settings-http-api-surface/quickstart.md if validate/preview payload shapes change during implementation (FR-009, FR-010)
- [ ] T033 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` using tools/test_unit.sh for final unit verification from /work/agent_jobs/mm:d0605b15-f8b2-40f8-9e2f-a9ea20825eef/repo (SC-001)
- [ ] T034 Run `./tools/test_integration.sh` using tools/test_integration.sh for final required hermetic integration verification from /work/agent_jobs/mm:d0605b15-f8b2-40f8-9e2f-a9ea20825eef/repo (SC-001)
- [ ] T035 Run `/moonspec-verify` against specs/352-settings-http-api-surface/spec.md after implementation and tests pass, confirming MM-657, the original preset brief, FR-001 through FR-017, SCN-001 through SCN-008, SC-001 through SC-006, and DESIGN-REQ-001 through DESIGN-REQ-013 (FR-017, SC-006)

---

## Dependencies And Execution Order

### Phase Dependencies

- Phase 1 Setup has no dependencies.
- Phase 2 Foundational depends on Phase 1 and blocks story test work.
- Phase 3 Story depends on Phase 2.
- Phase 4 Polish and Verification depends on Phase 3 tests and implementation passing.

### Within The Story

- T008-T011 unit tests must be written before T021-T027 implementation tasks.
- T012-T015 integration tests must be written before T021-T027 implementation tasks.
- T016-T017 red-first confirmation must complete before T021-T027.
- T018-T020 are conditional fallback tasks for `implemented_unverified` rows and only execute when their verification tests expose gaps.
- T021-T023 service/model work precedes T024-T026 route wiring.
- T027 secret hygiene hardening must complete before story validation T028-T030.
- T031-T035 only run after the story passes focused unit and integration validation.

## Parallel Opportunities

- T002, T003, and T004 can run in parallel.
- T006 and T007 can run in parallel after T005 because they touch different test files.
- T008/T009/T011 share one route test file and should be serialized; T010 can run in parallel with them.
- T012-T015 all share the new integration contract file and should be serialized.
- T018 and T019 can run in parallel if both are needed because they touch different production files; T020 is documentation-only.
- T031 and T032 can run in parallel after implementation if contract or quickstart updates are needed.

## Parallel Example

```bash
# After setup:
Task: "Review existing settings API route structure in api_service/api/routers/settings.py"
Task: "Review existing settings service validation helpers in api_service/services/settings_catalog.py"

# During unit test authoring:
Task: "Add failing route tests in tests/unit/api_service/api/routers/test_settings_api.py"
Task: "Add failing service tests in tests/unit/services/test_settings_catalog.py"
```

## Implementation Strategy

1. Confirm active artifacts and fixture surfaces.
2. Add test helpers only where needed.
3. Write unit tests for missing validate/preview route and service behavior.
4. Write integration tests for API-family coverage and no-commit validate/preview behavior.
5. Run focused unit and integration tests and confirm they fail for the intended missing behavior.
6. Implement non-committing validation and preview service behavior.
7. Add validate and preview routes using the existing settings permission and SettingsError patterns.
8. Preserve already verified catalog/effective/update/reset/audit behavior.
9. Run focused tests, then full unit and integration suites.
10. Run `/moonspec-verify` to compare final behavior against the preserved MM-657 preset brief.

## Notes

- This task list covers one story only.
- Do not add tasks for broad settings UI redesign or unrelated provider-profile CRUD.
- Do not create new persistence tables unless implementation proves existing settings/audit/provider/secret rows cannot support validate/preview no-commit behavior.
- Keep raw credentials and secret plaintext out of tests, fixtures, artifacts, logs, and error payloads.
