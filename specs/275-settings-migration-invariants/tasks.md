# Tasks: Settings Migration Invariants

**Input**: Design documents from `/work/agent_jobs/mm:be976054-2c25-4b91-8814-1aeff6eb3ee0/repo/specs/275-settings-migration-invariants/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/settings-migration-invariants.md, quickstart.md

**Tests**: Unit tests and API boundary tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement production code until they pass.

**Organization**: One story only: maintainers can evolve Settings System descriptors safely because migration, deprecation, type-change, and invariant behavior is enforced by deterministic tests and diagnostics.

**Source Traceability**: MM-546; FR-001 through FR-011; SC-001 through SC-006; DESIGN-REQ-020, DESIGN-REQ-021, DESIGN-REQ-024, DESIGN-REQ-027, DESIGN-REQ-028.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh tests/unit/services/test_settings_catalog.py tests/unit/api_service/api/routers/test_settings_api.py`
- Integration tests: `./tools/test_integration.sh`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm active artifacts and existing settings test harness.

- [X] T001 Create `specs/275-settings-migration-invariants/spec.md` and checklist preserving `MM-546` and source design IDs (FR-011, SC-006)
- [X] T002 Create `plan.md`, `research.md`, `data-model.md`, `contracts/settings-migration-invariants.md`, `quickstart.md`, and `tasks.md` for the single story (FR-011, SC-006)
- [X] T003 Run focused settings tests before new assertions to establish baseline: `./tools/test_unit.sh tests/unit/services/test_settings_catalog.py tests/unit/api_service/api/routers/test_settings_api.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish test fixtures for historical override rows and migration rules.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [X] T004 Add reusable migration-rule test fixtures in `tests/unit/services/test_settings_catalog.py` (FR-002, FR-003, FR-004, DESIGN-REQ-021)
- [X] T005 Add reusable API test setup for historical settings override rows in `tests/unit/api_service/api/routers/test_settings_api.py` (FR-003, SC-002)

**Checkpoint**: Foundation ready; story test and implementation work can start.

---

## Phase 3: Story - Settings Evolution Safety Gate

**Summary**: As a maintainer, I want Settings System migration rules, non-goals, and invariants to be enforced by tests and diagnostics so that future catalog changes cannot silently weaken operator intent, validation, or secret safety.

**Independent Test**: Run focused settings service and API tests that prove rename migrations, removed/deprecated keys, type-change schema gates, and catalog invariants behave deterministically.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, DESIGN-REQ-020, DESIGN-REQ-021, DESIGN-REQ-024, DESIGN-REQ-027, DESIGN-REQ-028.

**Test Plan**:

- Unit: migration rule validation, old-key resolution, deprecated-key diagnostics, schema mismatch failure, invariant snapshot.
- API: write rejection for deprecated keys, effective-value migration response, diagnostics response without raw values.

### Unit Tests (write first) ⚠️

> Write these tests FIRST. Run them, confirm they FAIL for the expected reason, then implement only enough code to make them pass.

- [X] T006 [P] Add failing unit test for rename migration preserving an old workspace override under the new key in `tests/unit/services/test_settings_catalog.py` (FR-002, FR-005, SC-001, DESIGN-REQ-021)
- [X] T007 [P] Add failing unit test for removed/deprecated old-key diagnostics without raw value exposure in `tests/unit/services/test_settings_catalog.py` (FR-003, FR-005, SC-002, DESIGN-REQ-021)
- [X] T008 [P] Add failing unit test for schema-version mismatch requiring explicit type migration in `tests/unit/services/test_settings_catalog.py` (FR-004, SC-003)
- [X] T009 [P] Add failing invariant regression test for descriptor exposure, declared scopes, validation, SecretRef safety, provider profile refs, source explainability, reset inheritance, operator locks/read-only behavior, audit, and intentional catalog changes in `tests/unit/services/test_settings_catalog.py` (FR-001, FR-006, FR-007, FR-008, FR-009, FR-010, SC-004, SC-005, DESIGN-REQ-020, DESIGN-REQ-024, DESIGN-REQ-027, DESIGN-REQ-028)
- [X] T010 Run `./tools/test_unit.sh tests/unit/services/test_settings_catalog.py` and confirm T006-T009 fail for missing MM-546 behavior, not syntax or fixture errors

### API Tests (write first) ⚠️

- [X] T011 [P] Add failing API test for rejected writes to deprecated keys in `tests/unit/api_service/api/routers/test_settings_api.py` (FR-003, SC-002)
- [X] T012 [P] Add failing API test for effective-value migration response and migration diagnostic in `tests/unit/api_service/api/routers/test_settings_api.py` (FR-002, FR-005, SC-001)
- [X] T013 [P] Add failing API test for diagnostics including deprecated historical override evidence without plaintext in `tests/unit/api_service/api/routers/test_settings_api.py` (FR-003, FR-005, FR-010, SC-002, SC-005)
- [X] T014 Run focused API tests and confirm T011-T013 fail for missing MM-546 behavior

### Implementation

- [X] T015 Add `SettingMigrationRule` and migration-rule validation to `api_service/services/settings_catalog.py` (FR-002, FR-003, FR-004, DESIGN-REQ-021)
- [X] T016 Implement old-key override resolution for explicit rename rules in `api_service/services/settings_catalog.py` (FR-002, SC-001)
- [X] T017 Implement removed/deprecated key write rejection and safe diagnostics for historical rows in `api_service/services/settings_catalog.py` and `api_service/api/routers/settings.py` (FR-003, FR-005, SC-002)
- [X] T018 Implement schema-version compatibility checks for type-changed persisted overrides in `api_service/services/settings_catalog.py` (FR-004, SC-003)
- [X] T019 Add invariant helper/test coverage without weakening existing SecretRef, provider profile, reset, audit, or permission behavior in `tests/unit/services/test_settings_catalog.py` (FR-006, FR-007, FR-008, FR-009, FR-010, DESIGN-REQ-024, DESIGN-REQ-027, DESIGN-REQ-028)
- [X] T020 Run focused validation `./tools/test_unit.sh tests/unit/services/test_settings_catalog.py tests/unit/api_service/api/routers/test_settings_api.py` and fix story-scoped failures

**Checkpoint**: The story is functional, covered by unit/API tests, and independently testable.

---

## Phase 4: Polish & Verification

**Purpose**: Strengthen the completed story without adding hidden scope.

- [X] T021 Review `specs/275-settings-migration-invariants/spec.md`, `plan.md`, `tasks.md`, `quickstart.md`, and final evidence for `MM-546` traceability (FR-011, SC-006)
- [X] T022 Run full unit verification with `./tools/test_unit.sh`
- [X] T023 Run hermetic integration verification with `./tools/test_integration.sh` when Docker is available; if unavailable, record the exact blocker
- [X] T024 Run `/moonspec-verify` for `specs/275-settings-migration-invariants/spec.md` and address any additional-work findings before final handoff

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: starts immediately.
- **Foundational (Phase 2)**: depends on Setup and blocks story implementation.
- **Story (Phase 3)**: depends on Foundational; tests must be written and confirmed failing before implementation.
- **Polish (Phase 4)**: depends on focused story tests passing.

### Parallel Opportunities

- T006-T009 can be authored in parallel only with coordination because they share one service test file.
- T011-T013 can be authored in parallel only with coordination because they share one API test file.
- T015-T018 are serialized through the service contract to avoid hidden compatibility shims.

## Implementation Strategy

1. Preserve `MM-546` traceability in every artifact and final evidence.
2. Write failing service and API tests for migration/deprecation/type-change behavior.
3. Implement explicit migration rules in the settings service.
4. Keep raw values out of diagnostics and API errors.
5. Run focused validation, then full unit verification, then integration if Docker is available.

## Verification Evidence

- Baseline focused settings validation before MM-546 tests: `./tools/test_unit.sh tests/unit/services/test_settings_catalog.py tests/unit/api_service/api/routers/test_settings_api.py` passed with 51 Python tests and 460 frontend tests.
- Red-first run after adding MM-546 tests: `./tools/test_unit.sh tests/unit/services/test_settings_catalog.py tests/unit/api_service/api/routers/test_settings_api.py` failed during collection because `SettingMigrationRule` was not implemented.
- Focused story validation after implementation: `./tools/test_unit.sh tests/unit/services/test_settings_catalog.py tests/unit/api_service/api/routers/test_settings_api.py` passed with 58 Python tests and 460 frontend tests.
- Full unit verification: `./tools/test_unit.sh` passed with 4,187 Python tests, 16 subtests, 1 xpass, and 460 frontend tests.
- Hermetic integration verification: `./tools/test_integration.sh` was blocked because Docker is unavailable in this managed container: `dial unix /var/run/docker.sock: connect: no such file or directory`.
- MoonSpec helper limitation: `.specify/scripts/bash/check-prerequisites.sh --json --require-tasks --include-tasks` could not resolve the managed-run branch because it does not start with the numeric spec prefix; verification used the explicit feature directory `specs/275-settings-migration-invariants`.
