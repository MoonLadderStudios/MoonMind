# Tasks: Claude Manual Auth API

**Input**: Design documents from `/specs/236-claude-manual-auth-api/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and route-level integration-style tests are REQUIRED. The implementation already exists on this branch; tasks preserve the TDD traceability and current verification evidence instead of regenerating code.

**Organization**: Tasks are grouped by phase around the single MM-447 backend manual-auth story.

**Source Traceability**: MM-447; DESIGN-REQ-002, DESIGN-REQ-004, DESIGN-REQ-006, DESIGN-REQ-010, DESIGN-REQ-012, DESIGN-REQ-014; FR-001 through FR-014; SC-001 through SC-007.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh tests/unit/workflows/adapters/test_secret_redaction.py`
- Integration tests: `./tools/test_unit.sh tests/unit/api_service/api/routers/test_provider_profiles.py`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Path Conventions

- **Backend API**: `api_service/api/routers/`
- **Runtime adapters**: `moonmind/workflows/adapters/`
- **Backend tests**: `tests/unit/api_service/api/routers/`
- **Adapter tests**: `tests/unit/workflows/adapters/`

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the existing backend route and adapter test harnesses are ready for the MM-447 story.

- [X] T001 Confirm provider profile route tests run through `./tools/test_unit.sh tests/unit/api_service/api/routers/test_provider_profiles.py`
- [X] T002 Confirm secret resolver tests run through `./tools/test_unit.sh tests/unit/workflows/adapters/test_secret_redaction.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish existing provider profile, managed secret, and secret resolver boundaries before story validation.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [X] T003 Inspect provider profile authorization and row serialization boundaries in `api_service/api/routers/provider_profiles.py` for FR-002, FR-012, DESIGN-REQ-012
- [X] T004 Inspect Managed Secret storage model in `api_service/db/models.py` and provider profile fields in `api_service/db/models.py` for FR-005, FR-006, FR-007
- [X] T005 Inspect runtime secret resolution boundary in `moonmind/workflows/adapters/secret_boundary.py` for FR-013

**Checkpoint**: Foundation ready - story test and implementation evidence can be validated.

---

## Phase 3: Story - Secret-Safe Claude Manual Auth Commit

**Summary**: As Mission Control, I want a dedicated Claude manual-auth backend commit path so that Claude Anthropic token enrollment updates provider profile readiness without exposing submitted token material.

**Independent Test**: Submit a Claude Anthropic token to the manual-auth commit path for a supported `claude_code` Anthropic provider profile, then verify token validation, Managed Secret-only storage, profile conversion to `secret_ref` and `api_key_env`, provider profile manager sync, `db://` secret resolution, and secret-free success/failure responses.

**Traceability**: MM-447, FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, FR-012, FR-013, FR-014, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, SC-007, DESIGN-REQ-002, DESIGN-REQ-004, DESIGN-REQ-006, DESIGN-REQ-010, DESIGN-REQ-012, DESIGN-REQ-014

**Test Plan**:

- Unit: token-shape failure, managed secret persistence assertions, profile mutation assertions, `db://` resolver behavior, redaction metadata behavior
- Integration-style route: ASGI provider profile route tests with test database and monkeypatched upstream token validation / provider profile manager sync

### Unit Tests (write first)

- [X] T006 [P] Add route test for successful manual-auth commit storing raw token only in Managed Secrets in `tests/unit/api_service/api/routers/test_provider_profiles.py` for FR-001, FR-004, FR-005, SC-001, DESIGN-REQ-010, DESIGN-REQ-012
- [X] T007 [P] Add route test assertions that successful commit response and fetched profile omit the submitted token in `tests/unit/api_service/api/routers/test_provider_profiles.py` for FR-009, FR-012, SC-001, SC-002, DESIGN-REQ-012
- [X] T008 [P] Add route test assertions that the provider profile is converted to `secret_ref`, `api_key_env`, cleared volume fields, secret refs, clear env keys, and env template in `tests/unit/api_service/api/routers/test_provider_profiles.py` for FR-006, FR-007, SC-002, DESIGN-REQ-004
- [X] T009 [P] Add route test for malformed token rejection without persistence in `tests/unit/api_service/api/routers/test_provider_profiles.py` for FR-011, SC-003
- [X] T010 [P] Add route test for unauthorized caller rejection before token validation or persistence in `tests/unit/api_service/api/routers/test_provider_profiles.py` for FR-002, FR-011, SC-004
- [X] T011 [P] Add route test for unsupported provider profile rejection before token validation or persistence in `tests/unit/api_service/api/routers/test_provider_profiles.py` for FR-003, FR-011, SC-004
- [X] T012 [P] Add `db://` slug secret resolver test in `tests/unit/workflows/adapters/test_secret_redaction.py` for FR-013, SC-005
- [X] T013 Run `./tools/test_unit.sh tests/unit/api_service/api/routers/test_provider_profiles.py` and `./tools/test_unit.sh tests/unit/workflows/adapters/test_secret_redaction.py` to confirm the new tests fail before implementation, or record current branch evidence if implementation already exists

### Integration Tests (write first)

- [X] T014 [P] Preserve provider profile manager sync assertion in `tests/unit/api_service/api/routers/test_provider_profiles.py` for FR-008, DESIGN-REQ-014
- [X] T015 [P] Preserve route-level failure assertions that unsupported, unauthorized, or malformed inputs do not leak submitted token material in `tests/unit/api_service/api/routers/test_provider_profiles.py` for FR-002, FR-003, FR-011, FR-012, DESIGN-REQ-012
- [X] T016 Run `./tools/test_unit.sh tests/unit/api_service/api/routers/test_provider_profiles.py` and confirm route-level MM-447 scenarios pass after implementation

### Implementation

- [X] T017 Add request/response models for Claude manual-auth commit in `api_service/api/routers/provider_profiles.py` for FR-001, FR-009
- [X] T018 Implement `POST /api/v1/provider-profiles/{profile_id}/manual-auth/commit` in `api_service/api/routers/provider_profiles.py` for FR-001, DESIGN-REQ-010
- [X] T019 Enforce provider profile management authorization and Claude Anthropic profile support in `api_service/api/routers/provider_profiles.py` for FR-002, FR-003, SC-004
- [X] T020 Implement local token shape validation and upstream validation boundary in `api_service/api/routers/provider_profiles.py` for FR-004, FR-011
- [X] T021 Implement managed secret upsert for submitted token material in `api_service/api/routers/provider_profiles.py` for FR-005
- [X] T022 Implement provider profile conversion to secret-reference launch shape in `api_service/api/routers/provider_profiles.py` for FR-006, FR-007, FR-010, DESIGN-REQ-004, DESIGN-REQ-006
- [X] T023 Implement provider profile manager sync and secret-free readiness response in `api_service/api/routers/provider_profiles.py` for FR-008, FR-009, DESIGN-REQ-014
- [X] T024 Preserve non-secret Claude auth metadata through response redaction while keeping token/key fields redacted in `moonmind/utils/logging.py` for FR-012
- [X] T025 Implement `db://` slug support in `moonmind/workflows/adapters/secret_boundary.py` for FR-013
- [X] T026 Run focused route and resolver commands and fix failures until MM-447 story evidence passes

**Checkpoint**: The story is functional, covered by focused backend route/adapter tests, and testable independently.

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: Strengthen the completed story without adding hidden scope.

- [X] T027 [P] Confirm no raw submitted token appears in route success response, fetched profile response, malformed-token response, unauthorized response, unsupported-profile response, or provider profile manager-shaped payload evidence in `tests/unit/api_service/api/routers/test_provider_profiles.py` for FR-011, FR-012, SC-001, SC-002, SC-003, SC-004
- [X] T028 [P] Confirm manual-auth backend path does not require or invoke `api_service/api/routers/oauth_sessions.py` volume-first finalization semantics for FR-010, SC-006, DESIGN-REQ-006
- [X] T029 Run `./tools/test_unit.sh` for final required unit verification
- [X] T030 Run `/moonspec-verify` to validate final implementation against MM-447, FR-001 through FR-014, SC-001 through SC-007, and DESIGN-REQ-002/004/006/010/012/014

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on setup completion and blocks story implementation
- **Story (Phase 3)**: Depends on foundational checks
- **Polish (Phase 4)**: Depends on focused story tests and implementation evidence

### Within The Story

- Unit and route tests T006-T012 should be written before implementation tasks T017-T025 when starting from scratch.
- Current branch already contains implementation and passing tests, so T013 records resume evidence rather than rerunning red-first history.
- T017-T023 must remain ordered because request/response models, authorization, validation, secret write, profile mutation, and sync/response are sequential route behavior.
- T025 runtime resolver support must exist before claiming launch materialization readiness.
- T030 final verification runs only after focused and full unit tests pass.

### Parallel Opportunities

- T006-T012 can be authored in parallel because they cover separate assertions and files.
- T027 and T028 can run in parallel after focused tests pass.

---

## Parallel Example: Story Phase

```bash
Task: "Add route success/token-redaction assertions in tests/unit/api_service/api/routers/test_provider_profiles.py"
Task: "Add db:// resolver assertion in tests/unit/workflows/adapters/test_secret_redaction.py"
```

---

## Implementation Strategy

### Resume-Aware Story Delivery

1. Preserve the single-story spec for MM-447.
2. Use the plan's requirement status table to recognize existing verified implementation and tests.
3. Keep completed tasks marked `[X]` where current branch evidence satisfies them.
4. Run focused provider profile route tests and resolver tests.
5. Run full `./tools/test_unit.sh`.
6. Run `/moonspec-verify` and use its findings as the final authority.

---

## Notes

- MM-447 and the Jira preset brief are the canonical source for this story.
- MM-448 is recorded as a Jira blocker dependency in the orchestration input, but this runtime mode request explicitly selected the MM-447 brief as the orchestration input.
- Do not broaden scope to frontend drawer behavior from MM-446 or runtime launch behavior from MM-448.
- Do not add documentation-only work unless explicitly requested.
