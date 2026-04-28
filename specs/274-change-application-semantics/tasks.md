# Tasks: Change Application, Reload, Restart, and Recovery Semantics

**Input**: Design documents from `/work/agent_jobs/mm:7d65d02a-6c47-4328-b4b3-1486da6438a4/repo/specs/274-change-application-semantics/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/settings-application-semantics.md, quickstart.md

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement production code until they pass.

**Organization**: One story only: operators can understand when settings take effect and recover from backup/restore reference gaps so runtime behavior changes are visible, durable, and safe.

**Source Traceability**: MM-544; FR-001 through FR-013; SC-001 through SC-007; DESIGN-REQ-016, DESIGN-REQ-019, DESIGN-REQ-025.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh tests/unit/services/test_settings_catalog.py tests/unit/api_service/api/routers/test_settings_api.py --ui-args frontend/src/components/settings/GeneratedSettingsSection.test.tsx`
- Integration tests: `./tools/test_integration.sh`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm existing settings test harnesses and generated artifacts are ready.

- [X] T001 Verify the active feature artifacts exist and preserve `MM-544` in `specs/274-change-application-semantics/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/settings-application-semantics.md`, and `quickstart.md` (FR-013, SC-007)
- [X] T002 Confirm focused backend test command can collect settings tests with `./tools/test_unit.sh tests/unit/services/test_settings_catalog.py tests/unit/api_service/api/routers/test_settings_api.py` before adding new assertions
- [X] T003 Confirm focused UI test command can collect `frontend/src/components/settings/GeneratedSettingsSection.test.tsx` through `./tools/test_unit.sh --ui-args frontend/src/components/settings/GeneratedSettingsSection.test.tsx`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish shared test fixtures and contract anchors before story implementation.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [ ] T004 Add reusable test registry entries for immediate, next-task, worker-reload, process-restart, and manual-operation apply modes in `tests/unit/services/test_settings_catalog.py` (FR-003, FR-007, DESIGN-REQ-019)
- [ ] T005 Add reusable API test fixtures for settings actor/workspace identity and persisted audit reads in `tests/unit/api_service/api/routers/test_settings_api.py` (FR-004, SC-002)
- [X] T006 [P] Extend frontend descriptor fixture builders with apply mode, activation state, affected systems, and completion guidance fields in `frontend/src/components/settings/GeneratedSettingsSection.test.tsx` (FR-006, SC-004)
- [ ] T007 Review `frontend/src/generated/openapi.ts` update workflow and record whether generated OpenAPI refresh is needed after backend schema changes in `specs/274-change-application-semantics/quickstart.md` (contracts/settings-application-semantics.md)

**Checkpoint**: Foundation ready; story test and implementation work can start.

---

## Phase 3: Story - Understand and Apply Setting Changes Safely

**Summary**: As an operator, I want settings to declare how changes take effect and expose recovery gaps so runtime behavior changes are visible, durable, and safe.

**Independent Test**: Exercise the settings catalog, write or preview flows, runtime consumer refresh behavior, restart-required visibility, and backup/restore reference diagnostics to confirm apply modes, structured change events, consumer-visible refresh outcomes, pending activation state, and broken reference reporting all remain traceable to `MM-544`.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, FR-012, FR-013, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, SC-007, DESIGN-REQ-016, DESIGN-REQ-019, DESIGN-REQ-025.

**Test Plan**:

- Unit: descriptor validation, apply mode consistency, audit/change event fields, activation diagnostics, restored-reference diagnostics, backup-safe serialization.
- Integration: settings API catalog/audit/diagnostics contract, UI display of apply semantics and recovery diagnostics, operations/runtime observable status where covered by existing hermetic integration fixtures.

### Unit Tests (write first) ⚠️

> Write these tests FIRST. Run them, confirm they FAIL for the expected reason, then implement only enough code to make them pass.

- [X] T008 [P] Add failing unit tests that descriptor generation requires explicit `apply_mode` and consistent reload/restart metadata in `tests/unit/services/test_settings_catalog.py` (FR-001, FR-003, FR-007, SC-001, DESIGN-REQ-016, DESIGN-REQ-019)
- [X] T009 [P] Add failing unit tests that committed override and reset audit/change events include event type, key, scope, source, apply mode, actor, timestamp, and affected systems in `tests/unit/services/test_settings_catalog.py` (FR-004, SC-002, DESIGN-REQ-019)
- [X] T010 [P] Add failing unit tests for activation state diagnostics covering current value, pending value, active state, affected process/worker, and completion guidance in `tests/unit/services/test_settings_catalog.py` (FR-006, FR-007, FR-008, SC-004)
- [ ] T011 [P] Add failing unit tests for sanitized restored-reference diagnostics for missing SecretRef, provider profile, and supported OAuth/reference gaps in `tests/unit/services/test_settings_catalog.py` (FR-009, FR-010, FR-011, SC-005, DESIGN-REQ-025)
- [ ] T012 [P] Add failing unit tests that late validation diagnostics remain visible after persistence for missing or disabled references in `tests/unit/services/test_settings_catalog.py` (FR-002, FR-012, SC-006, DESIGN-REQ-016)
- [ ] T013 Run `./tools/test_unit.sh tests/unit/services/test_settings_catalog.py` and confirm T008-T012 fail for missing MM-544 behavior, not fixture or syntax errors

### Integration and Contract Tests (write first) ⚠️

- [X] T014 [P] Add failing API contract tests for `/api/v1/settings/catalog` descriptor `apply_mode`, reload/restart flags, and affected systems in `tests/unit/api_service/api/routers/test_settings_api.py` (FR-003, SC-001, contracts/settings-application-semantics.md)
- [X] T015 [P] Add failing API contract tests for `/api/v1/settings/audit` structured change event metadata in `tests/unit/api_service/api/routers/test_settings_api.py` (FR-004, SC-002)
- [X] T016 [P] Add failing API contract tests for `/api/v1/settings/diagnostics` activation state, completion guidance, and sanitized restored-reference diagnostics in `tests/unit/api_service/api/routers/test_settings_api.py` (FR-006, FR-011, FR-012, SC-004, SC-005)
- [X] T017 [P] Add failing UI tests that `GeneratedSettingsSection` renders apply mode, affected systems, pending activation state, completion guidance, and broken-reference diagnostics without plaintext in `frontend/src/components/settings/GeneratedSettingsSection.test.tsx` (FR-006, FR-008, FR-010, FR-011, SC-004, SC-005)
- [X] T018 Add or extend a hermetic integration test proving a settings change produces observable refresh/reload/pending activation evidence in `tests/integration/temporal/test_system_operations_api.py` or the closest existing settings integration test (FR-005, SC-003)
- [ ] T019 Run focused API/UI tests and the targeted integration test, confirming T014-T018 fail for missing MM-544 behavior before production changes

### Implementation

- [X] T020 Add `apply_mode` and activation-related fields to settings service models in `api_service/services/settings_catalog.py` (FR-003, FR-006, FR-007, contracts/settings-application-semantics.md)
- [X] T021 Implement registry descriptor validation for apply mode, reload/restart consistency, and affected subsystem metadata in `api_service/services/settings_catalog.py` (FR-001, FR-003, DESIGN-REQ-016, DESIGN-REQ-019)
- [X] T022 Populate apply mode, source, affected systems, and actor metadata on settings audit/change events in `api_service/services/settings_catalog.py` (FR-004, SC-002)
- [X] T023 Implement activation state and completion guidance derivation for effective values and diagnostics in `api_service/services/settings_catalog.py` (FR-006, FR-007, FR-008)
- [ ] T024 Extend restored-reference diagnostics for supported missing SecretRef, provider profile, OAuth/reference gaps without exposing plaintext in `api_service/services/settings_catalog.py` (FR-009, FR-010, FR-011, DESIGN-REQ-025)
- [X] T025 Ensure `/api/v1/settings/catalog`, `/api/v1/settings/audit`, and `/api/v1/settings/diagnostics` serialize the new contract fields in `api_service/api/routers/settings.py` without weakening permission checks (FR-002, FR-004, FR-006, FR-012)
- [X] T026 Update `frontend/src/components/settings/GeneratedSettingsSection.tsx` types and rendering for apply mode, affected systems, activation state, pending value, and completion guidance (FR-006, FR-008, SC-004)
- [ ] T027 Update `frontend/src/components/settings/GeneratedSettingsSection.tsx` broken-reference diagnostic presentation to keep restored references clear and secret-safe (FR-010, FR-011, SC-005)
- [ ] T028 Refresh `frontend/src/generated/openapi.ts` if backend schema changes require it, or document why generated OpenAPI is unaffected in `specs/274-change-application-semantics/quickstart.md` (contracts/settings-application-semantics.md)
- [X] T029 Validate the story with `./tools/test_unit.sh tests/unit/services/test_settings_catalog.py tests/unit/api_service/api/routers/test_settings_api.py --ui-args frontend/src/components/settings/GeneratedSettingsSection.test.tsx` and fix failures until focused unit/UI coverage passes
- [X] T030 Run the targeted hermetic integration coverage from T018 or `./tools/test_integration.sh` if the integration path requires the full suite, and fix story-scoped failures

**Checkpoint**: The story is functional, covered by unit/API/UI/integration tests, and independently testable.

---

## Phase 4: Polish & Verification

**Purpose**: Strengthen the completed story without adding hidden scope.

- [ ] T031 [P] Review `specs/274-change-application-semantics/spec.md`, `plan.md`, `tasks.md`, and `quickstart.md` for `MM-544` traceability and update only if implementation evidence changed (FR-013, SC-007)
- [ ] T032 [P] Add edge-case test coverage for inconsistent descriptor apply metadata and missing restored references in `tests/unit/services/test_settings_catalog.py` if not fully covered by T008-T012 (Edge Cases, DESIGN-REQ-016, DESIGN-REQ-025)
- [ ] T033 [P] Add UI edge-case coverage for long activation guidance and diagnostic text fitting without overlap in `frontend/src/components/settings/GeneratedSettingsSection.test.tsx` (SC-004, UI contract)
- [X] T034 Run full unit verification with `./tools/test_unit.sh`
- [X] T035 Run hermetic integration verification with `./tools/test_integration.sh` when Docker is available; if unavailable, record the exact environment blocker in verification notes
- [ ] T036 Run `/moonspec-verify` for `specs/274-change-application-semantics/spec.md` and address any `ADDITIONAL_WORK_NEEDED` findings before final handoff

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: starts immediately.
- **Foundational (Phase 2)**: depends on Setup and blocks story implementation.
- **Story (Phase 3)**: depends on Foundational; tests must be written and confirmed failing before implementation.
- **Polish (Phase 4)**: depends on focused story tests passing.

### Within The Story

- T008-T012 unit tests before T020-T024 service implementation.
- T014-T018 contract/UI/integration tests before T025-T027 API/UI implementation.
- T013 and T019 red-first confirmations before any production changes.
- T020-T024 service work before T025 API serialization and T026-T027 UI rendering.
- T029-T030 validation before polish tasks.

### Parallel Opportunities

- T006 and T007 can run in parallel after T004-T005.
- T008-T012 can be authored in parallel in the same test file only with coordination; otherwise serialize to avoid conflicts.
- T014-T018 can run in parallel because they touch API tests, UI tests, and integration tests separately.
- T026 and T027 can be implemented together after backend schema fields are stable.
- T031-T033 can run in parallel after implementation passes focused tests.

---

## Implementation Strategy

1. Preserve `MM-544` traceability in every artifact and final evidence.
2. Write backend unit tests for apply mode, events, activation state, validation timing, and restored-reference safety.
3. Write API/UI/integration tests for the public behavior operators observe.
4. Confirm tests fail for missing MM-544 behavior.
5. Implement the settings service model and validation changes.
6. Expose the contract through settings API routes and Mission Control UI.
7. Run focused unit/UI/API tests, then integration and full unit verification.
8. Finish with `/moonspec-verify` against the original MM-544-preserving spec.

## Notes

- This task list covers one story only.
- `implemented_unverified` rows in `plan.md` still require verification tests before claiming completion.
- Do not add a new persistent table unless existing settings audit/diagnostic surfaces cannot represent required event evidence.
- Do not expose raw managed secret plaintext, OAuth state blobs, decrypted files, generated credential config, large artifacts, or workflow payloads in any settings surface.
- Verification evidence on 2026-04-28:
  - Red-first backend/API run before production changes: `./tools/test_unit.sh tests/unit/services/test_settings_catalog.py tests/unit/api_service/api/routers/test_settings_api.py` failed for missing `apply_mode`, activation, audit, and diagnostics fields.
  - Red-first frontend/API wrapper run before production changes: `./tools/test_unit.sh --ui-args frontend/src/components/settings/GeneratedSettingsSection.test.tsx` failed for the new MM-544 assertions.
  - Focused story verification: `./tools/test_unit.sh tests/unit/services/test_settings_catalog.py tests/unit/api_service/api/routers/test_settings_api.py --ui-args components/settings/GeneratedSettingsSection.test.tsx` passed with 48 backend/API tests and 7 UI tests.
  - Targeted integration verification: `python -m pytest -q tests/integration/temporal/test_system_operations_api.py -q --tb=short` passed with 2 tests.
  - Full unit verification: `./tools/test_unit.sh` passed with 4171 Python tests, 16 subtests, 1 xpass, and 460 frontend tests.
  - Full hermetic integration wrapper was blocked because Docker is unavailable in this managed container: `docker info --format '{{.ServerVersion}}'` failed with missing `/var/run/docker.sock`.
