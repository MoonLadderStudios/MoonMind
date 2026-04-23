# Tasks: Claude OAuth Runtime Launch Materialization

**Input**: Design documents from `specs/244-claude-runtime-launch-materialization/`
**Prerequisites**: `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/claude-runtime-launch.md`, `quickstart.md`

**Tests**: Unit tests are REQUIRED and must be written first. Integration tests are REQUIRED by the story for launch/artifact boundary coverage, but remain contingent on whether the implementation changes compose-backed seams. Confirm red-first behavior before production changes.

**Organization**: Tasks are grouped around the single MM-481 story: Claude OAuth-backed task launch must resolve `claude_anthropic`, materialize the auth volume at `/home/app/.claude`, set Claude home environment consistently, clear competing API-key variables, keep the auth volume out of workspace and artifact-backed paths, and expose only sanitized diagnostics.

**Source Traceability**: FR-001 through FR-008, acceptance scenarios 1-6, SC-001 through SC-005, and DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-015, DESIGN-REQ-017, DESIGN-REQ-018.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/services/temporal/runtime/test_launcher.py tests/unit/workflows/temporal/test_agent_runtime_activities.py tests/unit/agents/codex_worker/test_cli.py`
- Integration tests: `./tools/test_integration.sh` only if launch or artifact behavior changes beyond current in-process boundaries
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel when tasks touch different files and do not depend on incomplete work.
- Include exact file paths in descriptions.
- Include requirement, scenario, or source IDs when the task implements or validates behavior.

## Phase 1: Setup

**Purpose**: Confirm the active MM-481 story inputs and validation targets before test or code work begins.

- [ ] T001 Confirm `specs/244-claude-runtime-launch-materialization/` contains `spec.md`, `plan.md`, `research.md`, `data-model.md`, `quickstart.md`, and `contracts/claude-runtime-launch.md`
- [ ] T002 Confirm focused validation targets exist in `tests/unit/services/temporal/runtime/test_launcher.py`, `tests/unit/workflows/temporal/test_agent_runtime_activities.py`, and `tests/unit/agents/codex_worker/test_cli.py`

---

## Phase 2: Foundational

**Purpose**: Validate that no additional schema, migration, or infrastructure setup is required before story work begins.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [ ] T003 Confirm no database migration is required because MM-481 uses existing provider-profile rows, managed-session payloads, and launcher/session activity boundaries in `api_service/db/models.py` and `moonmind/schemas/agent_runtime_models.py`
- [ ] T004 Confirm no new package dependency is required because MM-481 uses existing FastAPI, Temporal runtime, launcher materializer, and pytest infrastructure in `api_service/main.py`, `moonmind/workflows/temporal/runtime/launcher.py`, and `moonmind/workflows/temporal/activity_runtime.py`
- [ ] T005 Confirm integration remains contingency-only unless launch/artifact behavior changes beyond current unit-testable seams, based on `specs/244-claude-runtime-launch-materialization/plan.md` and `quickstart.md`

**Checkpoint**: Foundation ready. Story test and implementation work can now begin.

---

## Phase 3: Story - Claude OAuth Runtime Launch Materialization

**Summary**: As a task operator, when a Claude run uses the OAuth-backed profile, MoonMind launches `claude_code` with the Claude auth volume materialized as the runtime home and competing API-key variables cleared.

**Independent Test**: Start or simulate a Claude task that selects the `claude_anthropic` profile, then verify the launch resolves that profile before runtime startup, materializes the auth volume at `/home/app/.claude`, sets the Claude home environment, clears competing API-key variables, keeps auth-volume paths and contents out of diagnostics and artifacts, and does not treat the auth volume as a workspace or artifact-backed path.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, SC-001, SC-002, SC-003, SC-004, SC-005, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-015, DESIGN-REQ-017, DESIGN-REQ-018

**Unit Test Plan**:

- Launcher tests prove Claude OAuth-backed launch resolves `claude_anthropic`, applies `clear_env_keys`, sets `CLAUDE_HOME` and `CLAUDE_VOLUME_PATH`, and keeps the auth volume distinct from workspace paths.
- Managed-session activity tests prove safe auth diagnostics for Claude include `profileRef`, `credentialSource`, `runtimeMaterializationMode`, `volumeRef`, and `authMountTarget` without leaking raw auth paths or secrets.
- Claude worker preflight tests prove OAuth Claude launches still require a valid writable `CLAUDE_HOME` and preserve the intended runtime boundary.

**Integration Test Plan**:

- If implementation changes launch-to-artifact publication, worker-topology wiring, or other compose-backed seams, run `./tools/test_integration.sh` and verify the MM-481 launch contract end to end.
- If implementation remains inside current unit-testable launcher/session boundaries, document why the integration contingency was not required.

### Unit Tests (write first)

> Write these tests FIRST. Run them, confirm they fail for the intended reason, then implement only enough code to make them pass.

- [ ] T006 [P] Add failing Claude OAuth launcher test covering FR-001, FR-002, SC-001, DESIGN-REQ-003, and DESIGN-REQ-004 in `tests/unit/services/temporal/runtime/test_launcher.py`
- [ ] T007 [P] Add failing Claude OAuth launcher env-materialization test covering FR-003, FR-004, FR-005, SC-002, SC-003, DESIGN-REQ-015, and DESIGN-REQ-018 in `tests/unit/services/temporal/runtime/test_launcher.py`
- [ ] T008 [P] Add failing Claude auth-volume boundary test covering FR-006, FR-007, SC-004, DESIGN-REQ-017, and DESIGN-REQ-018 in `tests/unit/services/temporal/runtime/test_launcher.py`
- [ ] T009 [P] Add failing Claude managed-session auth-diagnostics test covering FR-001, FR-003, FR-006, SC-001, SC-003, SC-004, and DESIGN-REQ-015 in `tests/unit/workflows/temporal/test_agent_runtime_activities.py`
- [ ] T010 [P] Add failing Claude managed-session sanitization test covering FR-006, FR-007, SC-004, and DESIGN-REQ-017 in `tests/unit/workflows/temporal/test_agent_runtime_activities.py`
- [ ] T011 [P] Add or update Claude OAuth preflight test coverage for `CLAUDE_HOME` launch requirements covering FR-004 and SC-003 in `tests/unit/agents/codex_worker/test_cli.py`

### Integration Tests (write before implementation when required)

- [ ] T012 Add contingent hermetic integration coverage for MM-481 launch/artifact boundaries in the relevant integration suite only if T006-T010 expose behavior that cannot be proven safely by unit tests, covering FR-007 and SC-004 in `tests/integration/` and document the chosen file path in `specs/244-claude-runtime-launch-materialization/tasks.md`

### Red-First Confirmation

- [ ] T013 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/services/temporal/runtime/test_launcher.py tests/unit/workflows/temporal/test_agent_runtime_activities.py tests/unit/agents/codex_worker/test_cli.py` and confirm T006-T011 fail for the expected MM-481 reasons before production changes
- [ ] T014 If T012 was required, run `./tools/test_integration.sh` and confirm the new MM-481 integration coverage fails for the expected reason before production changes

### Conditional Fallback Implementation Tasks for `implemented_unverified` Rows

- [ ] T015 If T006 or T009 fails because Claude OAuth-backed launch/session work does not carry `claude_anthropic` profile resolution or safe diagnostic metadata through startup, update `moonmind/workflows/adapters/managed_agent_adapter.py` and `moonmind/workflows/temporal/activity_runtime.py` for FR-001, SC-001, DESIGN-REQ-004
- [ ] T016 If T008 or T010 fails because operator-visible metadata leaks auth-path details or treats the auth volume like workspace/artifact state, update `moonmind/workflows/temporal/activity_runtime.py` and `moonmind/workflows/temporal/runtime/launcher.py` for FR-006, FR-007, SC-004, DESIGN-REQ-017, DESIGN-REQ-018

### Models / Entities / Validation

- [ ] T017 If T006 or T007 fails because OAuth-home profile validation is incomplete, update `moonmind/schemas/agent_runtime_models.py` for FR-002, FR-003, DESIGN-REQ-003, and DESIGN-REQ-015

### Services / Domain Logic

- [ ] T018 If T007 fails because launch shaping does not preserve the OAuth-home profile contract or clear all competing keys, update `moonmind/workflows/adapters/materializer.py` for FR-002, FR-005, SC-002, DESIGN-REQ-003, and DESIGN-REQ-018
- [ ] T019 If T007 or T011 fails because Claude OAuth launches do not set or validate Claude home paths consistently, update `moonmind/agents/base/adapter.py`, `moonmind/agents/codex_worker/runtime_mode.py`, and `moonmind/agents/codex_worker/cli.py` for FR-004 and SC-003

### Endpoints / Public Contracts / Runtime Boundaries

- [ ] T020 If T006 or T007 fails because the seeded Claude OAuth profile contract is wrong or incomplete, update `api_service/main.py` and `moonmind/workflows/temporal/runtime/providers/registry.py` for FR-002, FR-003, SC-001, SC-003, DESIGN-REQ-003, and DESIGN-REQ-015

### Integration Wiring

- [ ] T021 If any MM-481 test shows the runtime launcher does not propagate auth-mount targets or workspace separation correctly, update `moonmind/workflows/temporal/runtime/launcher.py` for FR-003, FR-007, SC-003, SC-004, and DESIGN-REQ-018

### Story Validation

- [ ] T022 Run the focused MM-481 unit validation command until all new Claude launch-materialization tests pass: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/services/temporal/runtime/test_launcher.py tests/unit/workflows/temporal/test_agent_runtime_activities.py tests/unit/agents/codex_worker/test_cli.py`
- [ ] T023 Run `./tools/test_integration.sh` only if T012/T014 were required by changed launch/artifact seams; otherwise record the explicit reason integration coverage was not required for MM-481 in `specs/244-claude-runtime-launch-materialization/tasks.md`

**Checkpoint**: The MM-481 story is functionally complete, independently testable, and validated against the Claude OAuth launch contract.

---

## Phase 4: Polish And Verification

**Purpose**: Strengthen the completed story without expanding scope.

- [ ] T024 [P] Review `specs/244-claude-runtime-launch-materialization/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/claude-runtime-launch.md`, `quickstart.md`, and `tasks.md` for MM-481 traceability covering FR-008
- [ ] T025 Run final unit verification with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- [ ] T026 If launch/artifact behavior changed beyond unit-testable seams, run final hermetic integration verification with `./tools/test_integration.sh`; otherwise preserve the documented no-integration rationale covering FR-007 and SC-004
- [ ] T027 Run `/moonspec-verify` after implementation and tests pass, using `specs/244-claude-runtime-launch-materialization/spec.md` and the preserved MM-481 Jira preset brief as the final alignment source

---

## Dependencies & Execution Order

### Phase Dependencies

- Setup (Phase 1): no dependencies.
- Foundational (Phase 2): depends on Setup completion.
- Story (Phase 3): depends on Foundational completion.
- Polish (Phase 4): depends on story validation passing.

### Within The Story

- T006-T011 must be written before any production code changes.
- T012 is contingent and only applies when unit tests expose a launch/artifact seam that requires hermetic integration proof.
- T013 and T014 must confirm red-first behavior before T015-T021.
- T015-T021 are conditional fallback implementation tasks and should be executed only when the corresponding verification tests expose a real gap.
- T022 must pass before T023 and any polish work.

### Parallel Opportunities

- T006-T008 can be authored together in `tests/unit/services/temporal/runtime/test_launcher.py` if coordinated as one file edit.
- T009-T010 can be authored together in `tests/unit/workflows/temporal/test_agent_runtime_activities.py` if coordinated as one file edit.
- T011 can run in parallel with T006-T010 because it targets `tests/unit/agents/codex_worker/test_cli.py`.
- T024 can run in parallel with final verification command preparation after T022 completes.

## Implementation Strategy

1. Confirm the active MM-481 artifacts and validation targets.
2. Add failing Claude launcher, managed-session, and CLI preflight tests first.
3. Run the focused red-first validation and preserve the failure evidence.
4. Apply only the conditional production changes exposed by the failing tests.
5. Re-run focused validation until the MM-481 story passes.
6. Run full unit verification and hermetic integration verification only when required by the changed seam.
7. Finish with `/moonspec-verify` using the preserved MM-481 preset brief as the alignment source.
