# Tasks: Claude OAuth Verification and Profile Registration

**Input**: Design documents from `specs/243-claude-oauth-verification/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/claude-oauth-verification.md, quickstart.md

**Tests**: Unit tests and route-boundary tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement production code until they pass.

**Organization**: Tasks are grouped around the single MM-480 story: Claude OAuth finalization verifies account-auth material, registers or updates `claude_anthropic`, syncs Provider Profile Manager, and exposes only secret-free metadata.

**Source Traceability**: FR-001 through FR-011, acceptance scenarios 1-6, SC-001 through SC-006, and DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-016, DESIGN-REQ-018.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/auth/test_volume_verifiers.py tests/unit/api_service/api/routers/test_oauth_sessions.py`
- Integration tests: route-boundary async pytest coverage through `tests/unit/api_service/api/routers/test_oauth_sessions.py`; run `./tools/test_integration.sh` only if API/artifact lifecycle behavior changes
- Final verification: `/speckit.verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup

**Purpose**: Confirm the existing project structure and validation targets for this story.

- [X] T001 Confirm `specs/243-claude-oauth-verification/` contains `spec.md`, `plan.md`, `research.md`, `data-model.md`, `quickstart.md`, and `contracts/claude-oauth-verification.md`
- [X] T002 Confirm focused test targets exist in `tests/unit/auth/test_volume_verifiers.py` and `tests/unit/api_service/api/routers/test_oauth_sessions.py`

---

## Phase 2: Foundational

**Purpose**: Validate no new schema, migration, dependency, or fixture foundation is needed before story work.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [X] T003 Confirm no database migration is required because MM-480 uses existing OAuth session and provider profile tables in `api_service/db/models.py`
- [X] T004 Confirm no new dependency is required because MM-480 uses existing FastAPI, SQLAlchemy, Docker-volume verifier, and pytest infrastructure

**Checkpoint**: Foundation ready - story test and implementation work can now begin.

---

## Phase 3: Story - Claude OAuth Verification and Profile Registration

**Summary**: As an operator, I can finalize a Claude OAuth session and have MoonMind verify the auth volume, register or update the OAuth-backed provider profile, and expose only secret-free verification metadata.

**Independent Test**: Complete or simulate finalization for a Claude OAuth session with known account-auth artifacts under the mounted Claude home, verify secret-free metadata, confirm `claude_anthropic` OAuth-volume registration and `claude_code` manager sync, and verify unauthorized attempts are rejected.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-016, DESIGN-REQ-018

**Test Plan**:

- Unit: Claude verifier path selection, qualifying `settings.json`, rejection of non-qualifying settings, compact secret-free metadata, and no raw path/value leakage.
- Route-boundary: Claude finalization order, profile fields, manager sync, failed verification no-mutation behavior, and unauthorized finalize no verification or mutation.

### Unit Tests (write first)

> Write these tests FIRST. Run them, confirm they FAIL for the expected reason, then implement only enough code to make them pass.

- [X] T005 [P] Add failing Claude verifier test for mounted-home `credentials.json` covering FR-002, FR-003, SC-001, DESIGN-REQ-004 in `tests/unit/auth/test_volume_verifiers.py`
- [X] T006 [P] Add failing Claude verifier test for qualifying `settings.json` account-auth evidence covering FR-003, SC-001, DESIGN-REQ-004 in `tests/unit/auth/test_volume_verifiers.py`
- [X] T007 [P] Add failing Claude verifier test rejecting non-qualifying `settings.json` covering FR-003 and edge case coverage in `tests/unit/auth/test_volume_verifiers.py`
- [X] T008 [P] Add failing Claude verifier no-leak assertions covering FR-004, FR-005, SC-002, DESIGN-REQ-013 in `tests/unit/auth/test_volume_verifiers.py`

### Route-Boundary Tests (write first)

- [X] T009 [P] Add failing successful Claude finalization route test covering FR-001, FR-007, FR-008, FR-010, SC-003, SC-004, DESIGN-REQ-003, DESIGN-REQ-014, DESIGN-REQ-016 in `tests/unit/api_service/api/routers/test_oauth_sessions.py`
- [X] T010 [P] Add failing unauthorized Claude finalize route test covering FR-009, SC-005, DESIGN-REQ-018 in `tests/unit/api_service/api/routers/test_oauth_sessions.py`
- [X] T011 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/auth/test_volume_verifiers.py tests/unit/api_service/api/routers/test_oauth_sessions.py` and confirm T005-T010 fail for the expected reason before production changes

### Implementation

- [X] T012 Update Claude credential artifact definitions and command generation for mounted-home `credentials.json` and qualifying `settings.json` in `moonmind/workflows/temporal/runtime/providers/volume_verifiers.py` covering FR-002, FR-003, SC-001, DESIGN-REQ-004
- [X] T013 Update verifier output parsing for Claude `settings.json` qualification without leaking values in `moonmind/workflows/temporal/runtime/providers/volume_verifiers.py` covering FR-004, FR-005, SC-002, DESIGN-REQ-013
- [X] T014 If T009 fails after verifier fixes, update Claude finalization behavior in `api_service/api/routers/oauth_sessions.py` so verification precedes profile mutation, `claude_anthropic` stores OAuth-volume fields only, and manager sync targets `claude_code` covering FR-001, FR-007, FR-008, FR-010, DESIGN-REQ-003, DESIGN-REQ-014, DESIGN-REQ-016. Skipped because T009 passed after verifier fixes.
- [X] T015 If T010 fails, update authorization/finalize guard behavior in `api_service/api/routers/oauth_sessions.py` so unauthorized finalize attempts do not verify, register, or mutate profiles covering FR-009, DESIGN-REQ-018. Skipped because T010 passed after verifier fixes.
- [X] T016 Run the focused unit command until all MM-480 tests pass: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/auth/test_volume_verifiers.py tests/unit/api_service/api/routers/test_oauth_sessions.py`

**Checkpoint**: The MM-480 story is functionally complete, covered by verifier and route-boundary tests, and independently testable.

---

## Phase 4: Polish And Verification

**Purpose**: Strengthen the completed story without expanding scope.

- [X] T017 [P] Review `specs/243-claude-oauth-verification/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/claude-oauth-verification.md`, `quickstart.md`, and `tasks.md` for MM-480 traceability covering FR-011
- [X] T018 Run final unit verification with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- [X] T019 Run `./tools/test_integration.sh` only if route/API/artifact lifecycle behavior changed; otherwise document why it was not required. Not run because MM-480 changed verifier logic and unit-tested route behavior only, with no compose/API artifact lifecycle change.
- [X] T020 Run `/speckit.verify` equivalent by checking implementation, tests, and artifacts against the original MM-480 request and produce the final verification report

---

## Dependencies & Execution Order

### Phase Dependencies

- Setup (Phase 1): no dependencies.
- Foundational (Phase 2): depends on Setup completion.
- Story (Phase 3): depends on Foundational completion.
- Polish (Phase 4): depends on focused story tests passing.

### Within The Story

- T005-T010 must be written before production changes.
- T011 must confirm red-first behavior before T012-T015.
- T012-T013 are required for partial verifier requirements.
- T014-T015 are conditional fallback tasks if route-boundary tests expose gaps.
- T016 validates story completion before polish.

### Parallel Opportunities

- T005-T008 can be authored together within `tests/unit/auth/test_volume_verifiers.py` only if coordinated as one file edit.
- T009-T010 can be authored together within `tests/unit/api_service/api/routers/test_oauth_sessions.py` only if coordinated as one file edit.
- T017 can run after implementation while final validation commands are prepared.

## Implementation Strategy

1. Confirm existing artifacts and no new infrastructure needs.
2. Add verifier and route-boundary tests first.
3. Run focused tests and preserve the red-first output.
4. Complete the Claude verifier behavior.
5. Apply route fallback changes only if route-boundary tests fail after verifier work.
6. Run focused validation, then full unit validation.
7. Write final verification with MM-480 traceability.
