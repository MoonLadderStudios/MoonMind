# Tasks: Server-Side Validation and Cross-Setting Policy Enforcement

**Input**: Design documents from `/work/agent_jobs/mm:6a56ae2e-2dd6-49a9-8d85-885149e190b2/repo/specs/341-server-side-validation/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/settings-validation-contract.md`, `quickstart.md`

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped around exactly one independently testable user story: validate setting changes before they take effect.

**Source Traceability**: Original Jira issue `MM-656` and the preserved preset brief are in `spec.md`. Tasks cover FR-001 through FR-012, SCN-001 through SCN-006, SC-001 through SC-005, edge cases, and DESIGN-REQ-001 through DESIGN-REQ-011.

**Requirement Status Summary**: `missing`: FR-005, FR-006, FR-008, FR-011, SCN-004, SCN-005, SC-003, SC-004, DESIGN-REQ-002, DESIGN-REQ-004, DESIGN-REQ-005. `partial`: FR-001, FR-002, FR-003, FR-004, FR-007, FR-009, FR-010, SCN-001, SCN-002, SCN-003, SCN-006, SC-001, SC-002, DESIGN-REQ-001, DESIGN-REQ-003, DESIGN-REQ-006, DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-010, DESIGN-REQ-011. `implemented_unverified`: FR-012, SC-005. `implemented_verified`: none.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh` for final unit verification; focused command `pytest tests/unit/services/test_settings_catalog.py tests/unit/api_service/api/routers/test_settings_api.py -q`
- Integration tests: `./tools/test_integration.sh` for required hermetic integration; focused command `pytest tests/integration/api/test_settings_overrides_contract.py tests/integration/api/test_settings_effective_values_contract.py -m 'integration_ci' -q`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel only when files differ and no incomplete dependency is shared
- Every task names exact file paths and requirement, scenario, source, or success IDs where applicable

## Phase 1: Setup

**Purpose**: Confirm the existing settings test and service layout is ready for MM-656 TDD work.

- [X] T001 Confirm active feature artifacts and one-story traceability for MM-656 in specs/341-server-side-validation/spec.md, specs/341-server-side-validation/plan.md, specs/341-server-side-validation/research.md, specs/341-server-side-validation/data-model.md, specs/341-server-side-validation/contracts/settings-validation-contract.md, and specs/341-server-side-validation/quickstart.md. (FR-012, SC-005)
- [X] T002 Confirm focused unit and integration test entrypoints are available for MM-656 in tests/unit/services/test_settings_catalog.py, tests/unit/api_service/api/routers/test_settings_api.py, tests/integration/api/test_settings_overrides_contract.py, and tests/integration/api/test_settings_effective_values_contract.py. (FR-011, SC-001, SC-004)
- [X] T003 Review current settings service and API boundaries in api_service/services/settings_catalog.py and api_service/api/routers/settings.py to identify insertion points for validation result objects, boundary-aware validation, and router error mapping. (FR-001, FR-008, FR-009, DESIGN-REQ-010)

---

## Phase 2: Foundational

**Purpose**: Establish blocking validation contracts and fixtures before story-specific tests and implementation begin.

**CRITICAL**: No production implementation for MM-656 can begin until Phase 2 and the red-first tests in Phase 3 are complete.

- [X] T004 [P] Add or update reusable unit-test registry fixtures for boolean, string, integer/number, enum, list, object, SecretRef, locked, and policy-bound settings in tests/unit/services/test_settings_catalog.py. (FR-002, FR-003, SC-001, DESIGN-REQ-009)
- [X] T005 [P] Add or update API test user/database helpers for settings validation rejection, no-mutation assertions, and sanitized response checks in tests/unit/api_service/api/routers/test_settings_api.py. (FR-001, FR-009, FR-010, SCN-006)
- [X] T006 [P] Add or update hermetic integration fixtures for provider profiles, managed secrets, and persisted settings overrides in tests/integration/api/test_settings_overrides_contract.py. (FR-004, FR-010, SCN-003)
- [X] T007 [P] Add or update effective-preview and diagnostics integration fixtures in tests/integration/api/test_settings_effective_values_contract.py. (FR-008, SCN-005, SC-004)

**Checkpoint**: Foundational fixtures are ready; story test authoring can begin.

---

## Phase 3: Story - Validate Setting Changes Before They Take Effect

**Summary**: As an operator or authorized user changing MoonMind settings, I want every setting change and preview checked against catalog, authorization, constraints, dependencies, SecretRef rules, and workspace policy so unsafe configuration cannot take effect silently.

**Independent Test**: Attempt valid and invalid setting changes, previews, launch readiness checks, and diagnostics across all documented boundaries; accepted values pass, rejected values return typed structured errors, and rejected sensitive values do not persist or fall back to another source.

**Traceability**: FR-001 through FR-012; SCN-001 through SCN-006; SC-001 through SC-005; DESIGN-REQ-001 through DESIGN-REQ-011; MM-656.

**Unit Test Plan**: Value type matrix, descriptor constraints, cross-setting policy rules, referenced resources, boundary metadata, normalized validation errors, no-bypass/fail-fast behavior, traceability guard.

**Integration Test Plan**: FastAPI Settings API write rejection, effective preview diagnostics, readiness diagnostics, no persisted partial changes, sanitized errors, one invalid combination per cross-setting rule.

### Unit Tests (write first)

- [X] T008 [P] Add failing unit tests for accepted and rejected boolean, string, integer/number, enum, list, object, and SecretRef values in tests/unit/services/test_settings_catalog.py. (FR-002, SCN-001, SC-001, DESIGN-REQ-003, DESIGN-REQ-009)
- [X] T009 [P] Add failing unit tests for numeric, string, list, object, size, and executable-payload constraints in tests/unit/services/test_settings_catalog.py. (FR-003, SC-002, DESIGN-REQ-007, DESIGN-REQ-008)
- [X] T010 [P] Add failing unit tests for SecretRef syntax, SecretRef backend policy, missing managed secrets, inactive managed secrets, missing provider profiles, and disabled provider profiles in tests/unit/services/test_settings_catalog.py. (FR-004, FR-010, SCN-003, DESIGN-REQ-001)
- [X] T011 [P] Add failing unit tests for workspace policy and cross-setting rules covering allowed runtimes, allowed providers, disabled-feature canary percentage, allowed publication modes, allowed SecretRef backends, and maintenance-mode conflicts in tests/unit/services/test_settings_catalog.py. (FR-005, FR-006, SCN-004, SC-003, DESIGN-REQ-002, DESIGN-REQ-004)
- [X] T012 [P] Add failing unit tests for validation timing boundaries `descriptor_generation`, `write_request`, `pre_persistence`, `effective_preview`, `launch_execution`, `operation_execution`, and `readiness_diagnostics` in tests/unit/services/test_settings_catalog.py. (FR-008, SCN-005, SC-004, DESIGN-REQ-005, DESIGN-REQ-011)
- [X] T013 [P] Add failing unit tests for structured validation error fields `key`, `scope`, `code`, `message`, `boundary`, and `blocks` in tests/unit/services/test_settings_catalog.py. (FR-001, FR-007, FR-009, SCN-006, DESIGN-REQ-006, DESIGN-REQ-010)
- [X] T014 [P] Add failing unit tests proving rejected invalid values do not mutate persisted overrides and do not fall back to another sensitive source in tests/unit/services/test_settings_catalog.py. (FR-010, DESIGN-REQ-001, DESIGN-REQ-011)
- [X] T015 [P] Add failing traceability guard proving MM-656, FR-012, SC-005, and DESIGN-REQ-001 through DESIGN-REQ-011 remain preserved in specs/341-server-side-validation/spec.md, specs/341-server-side-validation/plan.md, and specs/341-server-side-validation/tasks.md using tests/unit/specs/test_mm656_traceability.py. (FR-012, SC-005)

### Integration Tests (write first)

- [X] T016 [P] Add failing API unit tests for `PATCH /api/v1/settings/{scope}` structured rejection and sanitized error payloads in tests/unit/api_service/api/routers/test_settings_api.py. (FR-001, FR-007, FR-009, FR-010, SCN-002, SCN-006)
- [X] T017 [P] Add failing API unit tests proving invalid write requests preserve current effective values in tests/unit/api_service/api/routers/test_settings_api.py. (FR-010, SCN-006, DESIGN-REQ-011)
- [X] T018 [P] Add failing hermetic integration tests for write-time rejection of invalid values, invalid references, and invalid cross-setting combinations in tests/integration/api/test_settings_overrides_contract.py. (FR-002, FR-003, FR-004, FR-005, FR-006, SCN-001, SCN-002, SCN-003, SCN-004)
- [X] T019 [P] Add failing hermetic integration tests for effective preview diagnostics and readiness diagnostics using the same validation codes as write rejection in tests/integration/api/test_settings_effective_values_contract.py. (FR-008, FR-009, SCN-005, SC-004, DESIGN-REQ-005)
- [X] T020 [P] Add failing contract coverage for Settings API validation boundaries and diagnostic details in tests/integration/api/test_settings_effective_values_contract.py based on specs/341-server-side-validation/contracts/settings-validation-contract.md. (FR-008, FR-009, SC-004, DESIGN-REQ-010)

### Red-First Confirmation

- [X] T021 Run focused unit tests with `pytest tests/unit/services/test_settings_catalog.py tests/unit/api_service/api/routers/test_settings_api.py tests/unit/specs/test_mm656_traceability.py -q` and confirm T008-T017 fail for missing MM-656 validation behavior rather than fixture errors. (FR-001 through FR-012, SC-001 through SC-005)
- [X] T022 Run focused integration tests with `pytest tests/integration/api/test_settings_overrides_contract.py tests/integration/api/test_settings_effective_values_contract.py -m 'integration_ci' -q` and confirm T018-T020 fail for missing MM-656 API/boundary behavior rather than environment errors. (SCN-001 through SCN-006, DESIGN-REQ-003 through DESIGN-REQ-005)

### Conditional Fallback For Implemented-Unverified Rows

- [X] T023 If T015 exposes missing traceability, update specs/341-server-side-validation/spec.md, specs/341-server-side-validation/plan.md, specs/341-server-side-validation/research.md, specs/341-server-side-validation/data-model.md, specs/341-server-side-validation/contracts/settings-validation-contract.md, specs/341-server-side-validation/quickstart.md, and specs/341-server-side-validation/tasks.md to preserve MM-656, FR-012, SC-005, and the original preset brief. (FR-012, SC-005)

### Implementation

- [X] T024 Define boundary-aware validation result models, validation boundary constants, structured error helpers, and redaction-safe detail helpers in api_service/services/settings_catalog.py. (FR-001, FR-007, FR-009, DESIGN-REQ-006, DESIGN-REQ-010)
- [X] T025 Extend descriptor value validation for boolean, string, number/integer, enum, list, object, and SecretRef values plus string/list/object constraints in api_service/services/settings_catalog.py. (FR-002, FR-003, SC-001, SC-002, DESIGN-REQ-003, DESIGN-REQ-007, DESIGN-REQ-009)
- [X] T026 Implement SecretRef backend validation, referenced managed secret checks, provider profile reference checks, and redacted diagnostics in api_service/services/settings_catalog.py. (FR-004, FR-010, SCN-003, DESIGN-REQ-001)
- [X] T027 Implement compact workspace policy evaluation and cross-setting validation for allowed runtimes, allowed providers, canary rules, publication modes, SecretRef backend policy, and maintenance-mode conflicts in api_service/services/settings_catalog.py. (FR-005, FR-006, SCN-004, SC-003, DESIGN-REQ-002, DESIGN-REQ-004)
- [X] T028 Wire shared validation boundaries into catalog descriptor generation, write request handling, pre-persistence checks, effective-value preview, launch/operation validation helpers, and readiness diagnostics in api_service/services/settings_catalog.py. (FR-008, SCN-005, SC-004, DESIGN-REQ-005, DESIGN-REQ-011)
- [X] T029 Update Settings API route error mapping to return normalized `SettingsError` payloads with per-setting key, scope, code, boundary, blocks, and sanitized details in api_service/api/routers/settings.py. (FR-001, FR-007, FR-009, FR-010, SCN-002, SCN-006)
- [X] T030 Update settings diagnostics and effective value response behavior so invalid persisted or previewed values expose matching validation codes without plaintext leakage in api_service/services/settings_catalog.py and api_service/api/routers/settings.py. (FR-004, FR-008, FR-009, FR-010, SCN-003, SCN-005)
- [X] T031 Ensure rejected write requests remain atomic and preserve existing override values by updating persistence flow and audit behavior in api_service/services/settings_catalog.py. (FR-010, SCN-006, DESIGN-REQ-011)
- [X] T032 Update or add any required API schema tests and imports after validation contract changes in tests/unit/api_service/api/routers/test_settings_api.py and tests/unit/services/test_settings_catalog.py. (FR-009, DESIGN-REQ-010)

### Story Validation

- [X] T033 Run focused unit tests with `pytest tests/unit/services/test_settings_catalog.py tests/unit/api_service/api/routers/test_settings_api.py tests/unit/specs/test_mm656_traceability.py -q` and fix failures in api_service/services/settings_catalog.py, api_service/api/routers/settings.py, tests/unit/services/test_settings_catalog.py, tests/unit/api_service/api/routers/test_settings_api.py, and tests/unit/specs/test_mm656_traceability.py until the story unit suite passes. (FR-001 through FR-012, SC-001, SC-002, SC-005)
- [X] T034 Run focused integration tests with `pytest tests/integration/api/test_settings_overrides_contract.py tests/integration/api/test_settings_effective_values_contract.py -m 'integration_ci' -q` and fix failures in api_service/services/settings_catalog.py, api_service/api/routers/settings.py, tests/integration/api/test_settings_overrides_contract.py, and tests/integration/api/test_settings_effective_values_contract.py until the story integration suite passes. (SCN-001 through SCN-006, SC-003, SC-004)
- [X] T035 Validate the independent MM-656 story against specs/341-server-side-validation/spec.md and specs/341-server-side-validation/quickstart.md by confirming valid values pass, invalid values fail with structured errors, previews and readiness diagnostics match, rejected writes preserve current values, and no sensitive fallback occurs. (FR-001 through FR-012, SCN-001 through SCN-006, SC-001 through SC-005, DESIGN-REQ-001 through DESIGN-REQ-011)

**Checkpoint**: The MM-656 story is functional, covered by unit and integration tests, and independently testable.

---

## Phase 4: Polish And Verification

**Purpose**: Strengthen the completed story without adding hidden scope.

- [X] T036 [P] Review api_service/services/settings_catalog.py for duplicated validation branches after MM-656 implementation and refactor only within the settings validation boundary. (FR-008, DESIGN-REQ-010)
- [X] T037 [P] Review api_service/api/routers/settings.py for sanitized, consistent error responses and remove superseded ad hoc validation mapping that conflicts with the shared contract. (FR-009, FR-010, DESIGN-REQ-006)
- [X] T038 [P] Update specs/341-server-side-validation/contracts/settings-validation-contract.md if implementation changes the final validation code vocabulary or boundary names while preserving MM-656 traceability. (FR-012, SC-005)
- [X] T039 Run `./tools/test_unit.sh` and fix any MM-656-related unit regressions in api_service/services/settings_catalog.py, api_service/api/routers/settings.py, tests/unit/services/test_settings_catalog.py, tests/unit/api_service/api/routers/test_settings_api.py, and tests/unit/specs/test_mm656_traceability.py. (FR-011, SC-001, SC-002, SC-005)
- [X] T040 Run `./tools/test_integration.sh` when Docker is available, or record the exact Docker/socket blocker in specs/341-server-side-validation/verification.md if unavailable in the managed container. (SCN-001 through SCN-006, SC-003, SC-004)
- [X] T041 Run quickstart validation from specs/341-server-side-validation/quickstart.md and record command results or blockers in specs/341-server-side-validation/verification.md. (FR-011, SC-001 through SC-005)
- [ ] T042 Run `/moonspec-verify` for specs/341-server-side-validation/spec.md and preserve the final verification report with coverage for MM-656, FR-001 through FR-012, SCN-001 through SCN-006, SC-001 through SC-005, and DESIGN-REQ-001 through DESIGN-REQ-011. (FR-012, SC-005)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies.
- **Foundational (Phase 2)**: depends on Phase 1; blocks story test and implementation work.
- **Story (Phase 3)**: depends on Phase 2; tests and red-first confirmation must precede implementation.
- **Polish And Verification (Phase 4)**: depends on story implementation and focused tests passing.

### Within The Story

- T008-T020 must be written before T021-T022.
- T021-T022 must confirm red-first failures before T024-T032 production work.
- T023 is conditional and only runs if traceability verification fails.
- T024 must precede T025-T031 because it defines shared validation result contracts.
- T025-T027 may proceed after T024, but T028 depends on their rule surfaces.
- T029-T030 depend on T024 and the validation outputs from T025-T028.
- T031 depends on T028 and must be complete before story validation.
- T033-T035 validate the complete story before Phase 4.

### Parallel Opportunities

- T004-T007 can run in parallel because they prepare different test files.
- T008-T015 can be authored in parallel by file section, but all touch `tests/unit/services/test_settings_catalog.py` except T015, so coordinate same-file edits carefully.
- T016-T020 can run in parallel across API unit and integration files.
- T025-T027 can run in parallel after T024 if file ownership is coordinated, but they all touch `api_service/services/settings_catalog.py`; prefer serial edits unless using disjoint patches.
- T036-T038 can run in parallel after story validation because they touch different files.

## Parallel Example

```bash
# Independent test authoring after Phase 2:
Task: "T015 Add failing traceability guard in tests/unit/specs/test_mm656_traceability.py"
Task: "T016 Add failing API unit tests in tests/unit/api_service/api/routers/test_settings_api.py"
Task: "T018 Add failing hermetic integration tests in tests/integration/api/test_settings_overrides_contract.py"

# Independent polish after story validation:
Task: "T037 Review settings router error responses in api_service/api/routers/settings.py"
Task: "T038 Update contract vocabulary in specs/341-server-side-validation/contracts/settings-validation-contract.md"
```

## Implementation Strategy

1. Preserve the single-story scope from specs/341-server-side-validation/spec.md.
2. Complete setup and foundational fixture tasks.
3. Write all unit and integration tests first.
4. Run focused tests and confirm red-first failures for missing MM-656 behavior.
5. Implement shared validation contracts and rules in the settings service.
6. Wire API routes, effective preview, and diagnostics to the shared validation behavior.
7. Re-run focused unit and integration tests until green.
8. Run full unit verification and hermetic integration verification when available.
9. Run `/moonspec-verify` and preserve final evidence.

## Notes

- This task list covers exactly one story: Validate Setting Changes Before They Take Effect.
- TDD is required: unit and integration tests are authored and confirmed failing before production code work.
- Existing behavior is not removed from scope; it changes planned work from complete implementation to partial completion plus verification.
- No tasks create additional specs or process future stories.
- Secret values must remain redacted in tests, diagnostics, errors, artifacts, and verification output.
