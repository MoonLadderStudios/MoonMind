# Tasks: GitHub Token Permission Improvements

**Input**: Design documents from `/specs/294-github-token-permissions/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/github-token-permission-contract.md, quickstart.md

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped by phase around a single user story so the work stays focused, traceable, and independently testable.

**Source Traceability**: Original request is preserved verbatim in `specs/294-github-token-permissions/spec.md`. In-scope source mappings are DESIGN-REQ-001 through DESIGN-REQ-007.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- Integration tests: `./tools/test_integration.sh`
- Final verification: `/speckit.verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Path Conventions

- Backend/runtime code lives under `moonmind/`
- API/control-plane code lives under `api_service/`
- Unit tests live under `tests/unit/`
- Hermetic integration tests live under `tests/integration/`
- Canonical docs live under `docs/`

## Source Traceability Summary

| Traceability Item | Plan Status | Task Coverage |
| --- | --- | --- |
| DESIGN-REQ-001, FR-001, FR-002, FR-003, SCN-001, SC-001 | partial/missing | T004, T008, T014, T015, T022, T027, T028, T029, T030, T031, T045 |
| DESIGN-REQ-002, FR-004, FR-005, FR-006, FR-007, SCN-002, SC-002, SC-003 | missing/partial | T005, T009, T016, T017, T023, T024, T032, T033, T034, T035, T045 |
| DESIGN-REQ-003, FR-008, FR-009, FR-010, FR-016 | missing | T010, T018, T036, T037, T038, T046 |
| DESIGN-REQ-004, FR-011, FR-012, SCN-004, SC-004 | partial/missing | T011, T019, T025, T039, T040, T045 |
| DESIGN-REQ-005, FR-013, FR-014, SCN-003, SC-005 | partial/missing | T012, T020, T041, T042, T045 |
| DESIGN-REQ-006, FR-015, FR-016, SCN-005, SC-006 | missing | T013, T021, T026, T043, T044, T045 |
| DESIGN-REQ-007, FR-017, SCN-006, SC-007 | partial/missing | T046, T047, T051 |
| FR-018 | missing | T004-T026, T045, T049, T052, T053 |

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the existing repo structure and test entry points are ready for TDD implementation.

- [ ] T001 Review active feature inputs in specs/294-github-token-permissions/spec.md, specs/294-github-token-permissions/plan.md, specs/294-github-token-permissions/research.md, specs/294-github-token-permissions/data-model.md, specs/294-github-token-permissions/contracts/github-token-permission-contract.md, and specs/294-github-token-permissions/quickstart.md
- [ ] T002 [P] Confirm focused unit test entry points exist or create placeholder files for this story in tests/unit/auth/test_github_credentials.py, tests/unit/publish/test_publish_service_github_auth.py, tests/unit/workflows/adapters/test_github_service.py, and tests/unit/indexers/test_github_indexer.py
- [ ] T003 [P] Confirm integration test locations exist or create placeholder files for this story in tests/integration/api/test_github_token_probe.py and tests/integration/temporal/test_github_publish_readiness_boundaries.py

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Define the shared boundaries that the story tests and implementation will target.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [ ] T004 [P] Define the expected canonical resolver test fixture shape for FR-001/FR-002/DESIGN-REQ-001 in tests/unit/auth/test_github_credentials.py
- [ ] T005 [P] Define the publish command runner fixture for FR-004/FR-005/FR-006/DESIGN-REQ-002 in tests/unit/publish/test_publish_service_github_auth.py
- [ ] T006 [P] Define mocked GitHub HTTP response helpers for FR-011/FR-013/FR-014/FR-015 in tests/unit/workflows/adapters/test_github_service.py
- [ ] T007 [P] Define hermetic integration fixtures for publish/readiness/probe boundary tests covering SCN-001 through SCN-005 in tests/integration/temporal/test_github_publish_readiness_boundaries.py

**Checkpoint**: Foundation ready - story test and implementation work can now begin.

---

## Phase 3: Story - Use Fine-Grained GitHub Credentials Reliably

**Summary**: As a MoonMind operator, I want GitHub indexing, publishing, pull request readiness, and credential validation to use the GitHub token I configured for the target repository so that fine-grained personal access tokens work predictably without hidden dependency on ambient `git` or `gh` credentials.

**Independent Test**: Configure a fine-grained GitHub credential for a selected repository, run repository indexing, publish a branch and create a pull request, evaluate PR readiness with one optional permission missing, and run a targeted token probe. The story passes when every operation uses the configured credential without pre-existing machine-level `git` or `gh` auth, secret values remain redacted, and missing permissions are reported with specific actionable diagnostics.

**Traceability**: FR-001 through FR-018, SCN-001 through SCN-006, SC-001 through SC-007, DESIGN-REQ-001 through DESIGN-REQ-007

**Test Plan**:

- Unit: resolver precedence, source diagnostics, token redaction, permission profiles, GitHub diagnostic extraction, readiness degradation, token probe classification, indexer resolver use, and publish credential injection.
- Integration: publish branch/PR boundary without ambient auth, readiness optional permission boundary, targeted token probe boundary, and end-to-end service traceability across configured credential sources.

### Unit Tests (write first)

> Write these tests FIRST. Run them, confirm they FAIL for the expected reason, then implement only enough code to make them pass.

- [ ] T008 [P] Add failing unit tests for canonical resolver precedence covering FR-001/FR-002/DESIGN-REQ-001/SC-001 in tests/unit/auth/test_github_credentials.py
- [ ] T009 [P] Add failing unit tests proving resolver source diagnostics are redaction-safe for FR-003/SC-003 in tests/unit/auth/test_github_credentials.py
- [ ] T010 [P] Add failing unit tests for GitHub permission profiles covering FR-008/FR-009/FR-010/DESIGN-REQ-003 in tests/unit/workflows/adapters/test_github_service.py
- [ ] T011 [P] Add failing unit tests for readiness checks 403 and issue reactions 403 degradation covering FR-011/FR-012/DESIGN-REQ-004/SC-004 in tests/unit/workflows/adapters/test_github_service.py
- [ ] T012 [P] Add failing unit tests for sanitized GitHub provider diagnostics covering FR-013/FR-014/DESIGN-REQ-005/SC-005 in tests/unit/workflows/adapters/test_github_service.py
- [ ] T013 [P] Add failing unit tests for targeted token probe output and no classic-scope assumptions covering FR-015/FR-016/DESIGN-REQ-006/SC-006 in tests/unit/workflows/adapters/test_github_service.py
- [ ] T014 [P] Add failing unit tests proving GitHubIndexer uses canonical credential resolution when no constructor token is supplied for FR-001/DESIGN-REQ-001 in tests/unit/indexers/test_github_indexer.py
- [ ] T015 [P] Add failing unit tests for managed runtime GitHub launch resolver parity covering FR-001/FR-002/DESIGN-REQ-001 in tests/unit/workflows/temporal/runtime/test_managed_api_key_resolve.py
- [ ] T016 [P] Add failing unit tests proving PublishService branch push receives explicit token-aware non-interactive auth for FR-004/FR-007/DESIGN-REQ-002/SC-002 in tests/unit/publish/test_publish_service_github_auth.py
- [ ] T017 [P] Add failing unit tests proving PublishService PR creation uses REST or token-injected CLI auth without ambient `gh auth` for FR-005/FR-006/DESIGN-REQ-002/SC-002 in tests/unit/publish/test_publish_service_github_auth.py
- [ ] T018 [P] Add failing unit tests for publish-mode permission checklist contents covering FR-009/FR-016/DESIGN-REQ-003 in tests/unit/workflows/adapters/test_github_service.py

### Integration Tests (write first)

- [ ] T019 [P] Add failing hermetic integration test for PR readiness optional permission degradation covering SCN-004/FR-011/FR-012/DESIGN-REQ-004 in tests/integration/temporal/test_github_publish_readiness_boundaries.py
- [ ] T020 [P] Add failing hermetic integration test for GitHub permission diagnostics crossing the service boundary covering SCN-003/FR-013/FR-014/DESIGN-REQ-005 in tests/integration/temporal/test_github_publish_readiness_boundaries.py
- [ ] T021 [P] Add failing hermetic integration test for targeted token probe output covering SCN-005/FR-015/FR-016/DESIGN-REQ-006 in tests/integration/api/test_github_token_probe.py
- [ ] T022 [P] Add failing integration or boundary test proving Settings token ref reaches runtime GitHub resolution for SCN-001/FR-001/FR-002/DESIGN-REQ-001 in tests/integration/api/test_github_token_probe.py
- [ ] T023 [P] Add failing integration or handler-boundary test proving publish branch mode does not rely on ambient `git` credentials for SCN-002/FR-004/FR-007 in tests/integration/temporal/test_github_publish_readiness_boundaries.py
- [ ] T024 [P] Add failing integration or handler-boundary test proving publish PR mode does not rely on ambient `gh auth` for SCN-002/FR-005/FR-006 in tests/integration/temporal/test_github_publish_readiness_boundaries.py

### Red-First Confirmation

- [ ] T025 Run `pytest tests/unit/workflows/adapters/test_github_service.py -q` and confirm T010-T013 and T018 fail for missing permission profile, diagnostics, readiness degradation, and token probe behavior
- [ ] T026 Run `pytest tests/unit/auth/test_github_credentials.py tests/unit/indexers/test_github_indexer.py tests/unit/workflows/temporal/runtime/test_managed_api_key_resolve.py -q` and confirm T008-T009 and T014-T015 fail for missing canonical resolver behavior
- [ ] T027 Run `pytest tests/unit/publish/test_publish_service_github_auth.py -q` and confirm T016-T017 fail for missing publish credential injection
- [ ] T028 Run `pytest tests/integration/api/test_github_token_probe.py tests/integration/temporal/test_github_publish_readiness_boundaries.py -q` and confirm T019-T024 fail for the intended missing boundary behavior

### Implementation

- [ ] T029 Create canonical GitHub credential source and resolution models for FR-001/FR-002/FR-003 in moonmind/auth/github_credentials.py
- [ ] T030 Implement canonical GitHub credential resolver precedence and redaction-safe diagnostics for FR-001/FR-002/FR-003/DESIGN-REQ-001 in moonmind/auth/github_credentials.py
- [ ] T031 Add `GH_TOKEN`, `WORKFLOW_GITHUB_TOKEN`, and `MOONMIND_GITHUB_TOKEN_REF` resolver support without raw-token persistence for FR-002/SC-001 in moonmind/config/settings.py
- [ ] T032 Migrate GitHubService token resolution to the canonical resolver for FR-001/FR-002/DESIGN-REQ-001 in moonmind/workflows/adapters/github_service.py
- [ ] T033 Migrate GitHubIndexer to use canonical credential resolution when no explicit token is provided for FR-001/DESIGN-REQ-001 in moonmind/indexers/github_indexer.py
- [ ] T034 Migrate managed runtime GitHub launch helpers to the canonical resolver while preserving launch-boundary materialization for FR-001/FR-002/DESIGN-REQ-001 in moonmind/workflows/temporal/runtime/managed_api_key_resolve.py
- [ ] T035 Implement token-aware publish branch push with non-interactive credential materialization and redaction for FR-004/FR-007/DESIGN-REQ-002 in moonmind/publish/service.py
- [ ] T036 Replace PublishService ambient `gh pr create` behavior with REST PR creation or explicit token-injected CLI env for FR-005/FR-006/DESIGN-REQ-002 in moonmind/publish/service.py
- [ ] T037 Wire publish handler/runtime callers to provide repository context and resolved GitHub credential metadata for FR-004/FR-005/SCN-002 in moonmind/agents/codex_worker/handlers.py
- [ ] T038 Add GitHub permission profile definitions for indexing, publish, readiness, and full PR automation for FR-008/FR-009/FR-010/DESIGN-REQ-003 in moonmind/workflows/adapters/github_service.py
- [ ] T039 Implement targeted token probe service behavior for selected repository and mode-specific checklist output for FR-015/FR-016/DESIGN-REQ-006 in moonmind/workflows/adapters/github_service.py
- [ ] T040 Expose a MoonMind-owned token probe API or internal service boundary for FR-015/FR-016/SCN-005 in api_service/api/routers/settings.py
- [ ] T041 Implement sanitized GitHub permission diagnostic extraction for REST failures covering FR-013/FR-014/DESIGN-REQ-005 in moonmind/workflows/adapters/github_service.py
- [ ] T042 Implement optional evidence degradation for checks and issue reactions covering FR-011/FR-012/DESIGN-REQ-004 in moonmind/workflows/adapters/github_service.py
- [ ] T043 Ensure publish, probe, and GitHub API failure paths apply token-like redaction for FR-007/SC-003 in moonmind/publish/sanitization.py and moonmind/utils/logging.py
- [ ] T044 Update or add runtime boundary wiring so Temporal-facing GitHub publish/readiness invocations preserve existing invocation compatibility for FR-018 in moonmind/workflows/temporal/activity_runtime.py

### Story Validation

- [ ] T045 Run focused unit tests `pytest tests/unit/auth/test_github_credentials.py tests/unit/workflows/adapters/test_github_service.py tests/unit/publish/test_publish_service_github_auth.py tests/unit/indexers/test_github_indexer.py tests/unit/workflows/temporal/runtime/test_managed_api_key_resolve.py -q` and fix failures in moonmind/auth/github_credentials.py, moonmind/workflows/adapters/github_service.py, moonmind/publish/service.py, moonmind/indexers/github_indexer.py, and moonmind/workflows/temporal/runtime/managed_api_key_resolve.py
- [ ] T046 Run focused integration tests `pytest tests/integration/api/test_github_token_probe.py tests/integration/temporal/test_github_publish_readiness_boundaries.py -q` and fix failures in api_service/api/routers/settings.py, moonmind/workflows/adapters/github_service.py, moonmind/publish/service.py, and moonmind/workflows/temporal/activity_runtime.py

**Checkpoint**: The story is fully functional, covered by unit and integration tests, and testable independently.

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: Strengthen the completed story without adding hidden scope.

- [ ] T047 [P] Update desired-state GitHub token profile guidance for FR-017/DESIGN-REQ-007/SC-007 in docs/Security/SettingsSystem.md
- [ ] T048 [P] Update managed GitHub auth and publish semantics for FR-004/FR-005/FR-017 in docs/ManagedAgents/ManagedAgentsGit.md and docs/Tasks/TaskPublishing.md
- [ ] T049 [P] Add edge-case unit coverage for wrong resource owner, excluded repository, pending org approval, SSH remote, and token-like provider body redaction covering edge cases from specs/294-github-token-permissions/spec.md in tests/unit/workflows/adapters/test_github_service.py
- [ ] T050 [P] Add docs or comments for any Temporal-facing cutover or compatibility decision for FR-018 in specs/294-github-token-permissions/plan.md
- [ ] T051 Run quickstart validation commands from specs/294-github-token-permissions/quickstart.md and record any deviations in specs/294-github-token-permissions/verification.md
- [ ] T052 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` and fix story-related failures in moonmind/ and api_service/
- [ ] T053 Run `./tools/test_integration.sh` and fix story-related failures in moonmind/ and api_service/
- [ ] T054 Run `/speckit.verify` to validate the final implementation against the original feature request in specs/294-github-token-permissions/spec.md

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately.
- **Foundational (Phase 2)**: Depends on Setup completion - blocks story work.
- **Story (Phase 3)**: Depends on Foundational completion.
- **Polish (Phase 4)**: Depends on the story being functionally complete and tests passing.

### Within The Story

- Unit tests T008-T018 must be written before implementation tasks T029-T044.
- Integration tests T019-T024 must be written before implementation tasks T029-T044.
- Red-first confirmation tasks T025-T028 must run before production code tasks T029-T044.
- Resolver implementation T029-T034 should land before publish, indexer, and runtime migration tasks that consume it.
- Publish implementation T035-T037 should land before publish boundary validation T045-T046.
- Permission profiles and diagnostics T038-T042 should land before token probe and readiness validation T045-T046.
- Story validation T045-T046 must pass before polish and full-suite tasks T047-T054.

### Parallel Opportunities

- T002-T003 can run in parallel after T001.
- T004-T007 can run in parallel because they touch different test fixtures.
- T008-T018 can run in parallel by test file ownership.
- T019-T024 can run in parallel by integration boundary ownership.
- T047-T050 can run in parallel after story validation because they touch docs, tests, and feature artifacts separately.

---

## Parallel Example: Story Phase

```bash
# Launch independent unit test authoring together:
Task: "T008 Add failing resolver precedence tests in tests/unit/auth/test_github_credentials.py"
Task: "T016 Add failing publish branch auth tests in tests/unit/publish/test_publish_service_github_auth.py"
Task: "T011 Add failing readiness degradation tests in tests/unit/workflows/adapters/test_github_service.py"

# Launch independent integration test authoring together:
Task: "T021 Add failing token probe boundary test in tests/integration/api/test_github_token_probe.py"
Task: "T023 Add failing publish branch boundary test in tests/integration/temporal/test_github_publish_readiness_boundaries.py"
```

---

## Implementation Strategy

### Test-Driven Story Delivery

1. Complete Phase 1 setup and Phase 2 fixtures.
2. Write all unit tests T008-T018 and integration tests T019-T024.
3. Run red-first confirmation T025-T028 and confirm failures are due to missing planned behavior.
4. Implement canonical resolver and migrate consumers T029-T034.
5. Implement publish credential handling T035-T037.
6. Implement permission profiles, diagnostics, readiness degradation, and token probe T038-T044.
7. Run focused validation T045-T046 until green.
8. Update docs and edge-case coverage T047-T051.
9. Run full unit and integration suites T052-T053.
10. Run `/speckit.verify` in T054.

### Requirement Status Handling

- Code-and-test work: all `missing` and `partial` rows from `plan.md`, including FR-001 through FR-018, SCN-001 through SCN-006, SC-001 through SC-007, and DESIGN-REQ-001 through DESIGN-REQ-007.
- Verification-only work: none; no `implemented_unverified` rows are present in `plan.md`.
- Conditional fallback implementation work: none; no `implemented_unverified` rows are present in `plan.md`.
- Already verified work: none; no `implemented_verified` rows are present in `plan.md`.

## Notes

- This task list covers exactly one story: `Use Fine-Grained GitHub Credentials Reliably`.
- Unit and integration tests are mandatory and precede implementation work.
- Red-first confirmation tasks T025-T028 must complete before production code changes.
- No live GitHub credentials are required for required CI; use mocked GitHub responses in unit and hermetic integration tests.
- Provider verification with real credentials may be added later as manual/nightly coverage, but it is outside the required story tasks.
