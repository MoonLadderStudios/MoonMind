# Tasks: Claude OAuth Authorization and Redaction Guardrails

**Input**: Design documents from `specs/245-claude-oauth-guardrails/`
**Prerequisites**: `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/claude-oauth-guardrails.md`, `quickstart.md`

**Tests**: Unit tests are REQUIRED and must be written or confirmed first. Integration tests are REQUIRED by the story only when changes reach compose-backed API, artifact, worker-topology, or WebSocket seams that unit tests cannot safely prove. Confirm red-first behavior before production changes.

**Organization**: Tasks are grouped around the single MM-482 story: Claude OAuth lifecycle operations, attach tokens, operator-visible outputs, provider-profile payloads, and auth-volume boundaries must enforce authorization, remain secret-free, and treat the Claude auth volume strictly as a credential store.

**Source Traceability**: FR-001 through FR-008, acceptance scenarios 1-6, SC-001 through SC-006, and DESIGN-REQ-004, DESIGN-REQ-009, DESIGN-REQ-013, DESIGN-REQ-016, DESIGN-REQ-017, DESIGN-REQ-018.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api_service/api/routers/test_oauth_sessions.py tests/unit/api_service/api/routers/test_provider_profiles.py tests/unit/services/temporal/runtime/test_terminal_bridge.py tests/unit/workflows/temporal/test_agent_runtime_activities.py tests/unit/services/temporal/runtime/test_launcher.py`
- Integration tests: `./tools/test_integration.sh` only if implementation changes compose-backed API/artifact/worker seams beyond the current in-process guardrail boundaries
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel when tasks touch different files and do not depend on incomplete work.
- Include exact file paths in descriptions.
- Include requirement, scenario, or source IDs when the task implements or validates behavior.

## Phase 1: Setup

**Purpose**: Confirm the active MM-482 artifact set and validation targets before any test or code work begins.

- [X] T001 Confirm `specs/245-claude-oauth-guardrails/` contains `spec.md`, `plan.md`, `research.md`, `data-model.md`, `quickstart.md`, and `contracts/claude-oauth-guardrails.md`
- [X] T002 Confirm focused validation targets exist in `tests/unit/api_service/api/routers/test_oauth_sessions.py`, `tests/unit/api_service/api/routers/test_provider_profiles.py`, `tests/unit/services/temporal/runtime/test_terminal_bridge.py`, `tests/unit/workflows/temporal/test_agent_runtime_activities.py`, and `tests/unit/services/temporal/runtime/test_launcher.py`

---

## Phase 2: Foundational

**Purpose**: Validate that no additional schema, migration, dependency, or test-harness foundation is needed before story work begins.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [X] T003 Confirm no database migration is required because MM-482 uses existing OAuth session rows, provider-profile rows, metadata payloads, and artifact/diagnostic redaction infrastructure in `api_service/db/models.py`
- [X] T004 Confirm no new package dependency is required because MM-482 uses existing FastAPI, SQLAlchemy async ORM, Temporal runtime, terminal bridge, launcher, and pytest infrastructure in `api_service/api/routers/`, `moonmind/workflows/temporal/`, and `moonmind/utils/logging.py`
- [X] T005 Confirm integration remains contingency-only unless the MM-482 fixes change compose-backed API/artifact/worker seams, based on `specs/245-claude-oauth-guardrails/plan.md`, `research.md`, and `quickstart.md`

**Checkpoint**: Foundation ready. Story verification and implementation work can now begin.

---

## Phase 3: Story - Claude OAuth Authorization and Redaction Guardrails

**Summary**: As an operator and platform maintainer, Claude OAuth lifecycle operations and observable outputs must enforce authorization, reject attach-token replay, redact secret-like values, and keep the Claude auth volume treated as a credential store across the full flow.

**Independent Test**: Exercise or simulate authorized and unauthorized Claude OAuth lifecycle actions, attach-token issuance and replay, secret-like terminal/log/artifact output, provider-profile and verification payloads, and auth-volume-related launch/validation metadata. Verify unauthorized actions fail closed, replay is denied, all operator-visible surfaces remain secret-free, and the auth volume is never treated as a workspace or artifact root.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, DESIGN-REQ-004, DESIGN-REQ-009, DESIGN-REQ-013, DESIGN-REQ-016, DESIGN-REQ-017, DESIGN-REQ-018

**Unit Test Plan**:

- Route tests prove owner-scoped authorization for create/attach/cancel/finalize/reconnect and prove attach-token replay denial at the real OAuth session boundary.
- Provider-profile tests prove Claude OAuth profile and verification-adjacent payloads expose refs and metadata only.
- Terminal-bridge tests prove safe metadata stays secret-free and token-like input is not leaked while replay or unsupported-frame cases are denied.
- Activity and launcher tests prove Claude auth diagnostics and auth-volume metadata stay sanitized and separate from workspace/artifact roots.

**Integration Test Plan**:

- If implementation changes compose-backed OAuth session, WebSocket wiring, artifact publication, or managed-runtime launch seams, add or update hermetic integration coverage and run `./tools/test_integration.sh`.
- If implementation remains inside the current route, bridge, provider-profile, activity, and launcher unit boundaries, document why integration did not need to run.

### Unit Tests (write first)

> Write these tests FIRST. Run them, confirm they FAIL for the expected reason, then implement only enough code to make them pass.

- [X] T006 [P] Add failing Claude OAuth authorization route tests for create, cancel, and reconnect-as-repair ownership boundaries covering FR-001, SC-001, and DESIGN-REQ-016 in `tests/unit/api_service/api/routers/test_oauth_sessions.py`
- [X] T007 [P] Add failing Claude OAuth attach-token replay and single-use tests covering FR-002, FR-003, SC-002, DESIGN-REQ-009, and DESIGN-REQ-017 in `tests/unit/api_service/api/routers/test_oauth_sessions.py`
- [X] T008 [P] Add failing Claude OAuth provider-profile and verification-surface no-leak tests covering FR-004, FR-005, SC-003, SC-004, DESIGN-REQ-004, and DESIGN-REQ-013 in `tests/unit/api_service/api/routers/test_provider_profiles.py`
- [X] T009 [P] Add failing Claude OAuth terminal-bridge safe-metadata and replay-adjacent tests covering FR-003, FR-004, SC-002, SC-003, DESIGN-REQ-009, and DESIGN-REQ-013 in `tests/unit/services/temporal/runtime/test_terminal_bridge.py`
- [X] T010 [P] Add failing Claude auth-diagnostics sanitization tests covering FR-004, FR-006, SC-003, SC-005, DESIGN-REQ-013, and DESIGN-REQ-018 in `tests/unit/workflows/temporal/test_agent_runtime_activities.py`
- [X] T011 [P] Add failing Claude auth-volume credential-store boundary tests for launch metadata and workspace separation covering FR-006, SC-005, and DESIGN-REQ-018 in `tests/unit/services/temporal/runtime/test_launcher.py`

### Integration Tests (write before implementation when required)

- [X] T012 Add contingent hermetic integration coverage for MM-482 in `tests/integration/temporal/test_claude_oauth_guardrails.py` only if T006-T011 expose behavior that cannot be proven safely by unit tests, covering FR-007, SC-006, and DESIGN-REQ-018. Not required because the MM-482 gaps were proven and fixed inside existing route, provider-profile, terminal-bridge, activity, and launcher unit boundaries.

### Red-First Confirmation

- [X] T013 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api_service/api/routers/test_oauth_sessions.py tests/unit/api_service/api/routers/test_provider_profiles.py tests/unit/services/temporal/runtime/test_terminal_bridge.py tests/unit/workflows/temporal/test_agent_runtime_activities.py tests/unit/services/temporal/runtime/test_launcher.py` and confirm T006-T011 fail for the expected MM-482 reasons before production changes
- [X] T014 If T012 was required, run `./tools/test_integration.sh` and confirm the new MM-482 integration coverage fails for the expected reason before production changes. Not required because T012 was not needed.

### Conditional Fallback Implementation Tasks for `implemented_unverified` Rows

- [X] T015 If T007 or T009 fails because attach-token replay or single-use enforcement is incomplete, update `api_service/api/routers/oauth_sessions.py` and `moonmind/workflows/temporal/runtime/terminal_bridge.py` for FR-002, FR-003, SC-002, DESIGN-REQ-009, and DESIGN-REQ-017. Skipped because the replay and single-use enforcement already passed once the new boundary tests were in place.
- [X] T016 If T008, T010, or T011 fails because operator-visible Claude OAuth surfaces leak secret-like values or raw auth-path details, update `moonmind/utils/logging.py`, `api_service/api/routers/provider_profiles.py`, `api_service/services/provider_profile_service.py`, and `moonmind/workflows/temporal/activity_runtime.py` for FR-004, FR-005, SC-003, SC-004, DESIGN-REQ-004, and DESIGN-REQ-013

### Models / Entities / Validation

- [X] T017 If T006 or T008 fails because session/profile ownership or Claude OAuth metadata validation is incomplete, update `api_service/api/routers/oauth_sessions.py`, `api_service/api/routers/provider_profiles.py`, and any related validation helpers for FR-001, FR-005, SC-001, and DESIGN-REQ-016. Skipped because the new ownership and metadata-validation tests passed without router contract changes.

### Services / Domain Logic

- [X] T018 If T010 or T011 fails because auth-volume metadata is treated as workspace or artifact state, update `moonmind/workflows/adapters/materializer.py`, `moonmind/workflows/adapters/managed_agent_adapter.py`, and `moonmind/workflows/temporal/runtime/launcher.py` for FR-006, SC-005, and DESIGN-REQ-018. Skipped because the added auth-volume boundary tests passed without runtime-materialization changes.
- [X] T019 If T008 fails because ref-only/profile-summary behavior is inconsistent between API and manager payloads, update `api_service/services/provider_profile_service.py` and `api_service/api/routers/provider_profiles.py` for FR-005, SC-004, and DESIGN-REQ-004. Skipped because the new Claude profile-summary assertions passed once validation-failure redaction was fixed.

### Endpoints / Public Contracts / Runtime Boundaries

- [X] T020 If T006 or T007 fails because lifecycle authorization or reconnect-as-repair behavior is incomplete for Claude OAuth, update `api_service/api/routers/oauth_sessions.py` for FR-001, FR-002, FR-003, SC-001, SC-002, and DESIGN-REQ-016. Skipped because the new lifecycle authorization and replay tests passed without changing the OAuth session router.
- [X] T021 If T010 or T011 fails because launch/activity boundaries still surface raw auth-volume details, update `moonmind/workflows/temporal/activity_runtime.py` and `moonmind/workflows/temporal/runtime/launcher.py` for FR-004, FR-006, FR-007, SC-003, SC-005, and DESIGN-REQ-018. Skipped because the added launch/activity boundary checks passed without further code changes.

### Integration Wiring

- [X] T022 If T012 becomes necessary, wire the minimum integration fixtures and assertions in `tests/integration/temporal/test_claude_oauth_guardrails.py` and document the seam-specific rationale in `specs/245-claude-oauth-guardrails/tasks.md`. Not required because T012 was not needed.

### Story Validation

- [X] T023 Run the focused MM-482 unit validation command until all new Claude guardrail tests pass: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api_service/api/routers/test_oauth_sessions.py tests/unit/api_service/api/routers/test_provider_profiles.py tests/unit/services/temporal/runtime/test_terminal_bridge.py tests/unit/workflows/temporal/test_agent_runtime_activities.py tests/unit/services/temporal/runtime/test_launcher.py`
- [X] T024 Run `./tools/test_integration.sh` only if T012/T014/T022 were required by changed compose-backed seams; otherwise record the explicit no-integration rationale for MM-482 in `specs/245-claude-oauth-guardrails/tasks.md`. Not run because MM-482 was completed entirely inside existing unit-testable boundaries and did not change any compose-backed seam.

**Checkpoint**: The MM-482 story is functionally complete, independently testable, and validated against the Claude OAuth guardrail contract.

---

## Phase 4: Polish And Verification

**Purpose**: Strengthen the completed story without expanding scope.

- [X] T025 [P] Review `specs/245-claude-oauth-guardrails/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/claude-oauth-guardrails.md`, `quickstart.md`, and `tasks.md` for MM-482 traceability covering FR-008
- [X] T026 Run final unit verification with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- [X] T027 If compose-backed seams changed, run final hermetic integration verification with `./tools/test_integration.sh`; otherwise preserve the documented no-integration rationale covering FR-007 and SC-006. Not run because MM-482 did not require any compose-backed integration seam change.
- [X] T028 Run `/moonspec-verify` after implementation and tests pass, using `specs/245-claude-oauth-guardrails/spec.md` and the preserved MM-482 Jira preset brief as the final alignment source

---

## Dependencies & Execution Order

### Phase Dependencies

- Setup (Phase 1): no dependencies.
- Foundational (Phase 2): depends on Setup completion.
- Story (Phase 3): depends on Foundational completion.
- Polish (Phase 4): depends on story validation passing.

### Within The Story

- T006-T011 must be written before any production code changes.
- T012 is contingent and only applies when unit tests expose a compose-backed seam that requires hermetic proof.
- T013 and T014 must confirm red-first behavior before T015-T022.
- T015-T022 are conditional fallback implementation tasks and should be executed only when the corresponding verification tests expose a real gap.
- T023 must pass before T024 and any polish work.

### Parallel Opportunities

- T006-T007 can be authored together in `tests/unit/api_service/api/routers/test_oauth_sessions.py` if coordinated as one file edit.
- T008 can run in parallel because it targets `tests/unit/api_service/api/routers/test_provider_profiles.py`.
- T009 can run in parallel because it targets `tests/unit/services/temporal/runtime/test_terminal_bridge.py`.
- T010 can run in parallel because it targets `tests/unit/workflows/temporal/test_agent_runtime_activities.py`.
- T011 can run in parallel because it targets `tests/unit/services/temporal/runtime/test_launcher.py`.
- T025 can run in parallel with final verification command preparation after T023 completes.

## Implementation Strategy

1. Confirm the active MM-482 artifacts and validation targets.
2. Add failing Claude guardrail tests first across routes, provider-profile serialization, terminal bridge, activities, and launcher boundaries.
3. Run the focused red-first validation and preserve the failure evidence.
4. Apply only the conditional production changes exposed by the failing tests.
5. Re-run focused validation until the MM-482 story passes.
6. Run full unit verification and hermetic integration verification only when required by the changed seam.
7. Finish with `/moonspec-verify` using the preserved MM-482 preset brief as the final alignment source.
