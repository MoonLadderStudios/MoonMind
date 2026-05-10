# Tasks: Agent Tool-Surface Isolation

**Input**: Design documents from `/work/agent_jobs/mm:84c05579-71b9-4970-a650-3eb2341060d1/repo/specs/335-agent-tool-surface-isolation/`
**Prerequisites**: `spec.md`, `plan.md`, `research.md`, `data-model.md`, `quickstart.md`, `contracts/managed-runtime-isolation-contract.md`, `contracts/publish-reconciliation-contract.md`

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Setup Note**: `.specify/scripts/bash/check-prerequisites.sh --json` failed because the managed branch is `change-jira-issue-mm-680-to-status-in-pr-6478e030`, not a numeric MoonSpec branch. `.specify/feature.json` and the existing artifacts identify `specs/335-agent-tool-surface-isolation` as the active feature directory.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh`
- Integration tests: `./tools/test_integration.sh`
- Final verification: `/moonspec-verify`

**Source Traceability**: Preserves Jira issue `MM-680` and the original Jira preset brief from `spec.md`. Requirement status from `plan.md`: 20 partial, 16 missing, 3 implemented_unverified, 0 implemented_verified.

**Story Summary**: As a MoonMind operator, I want MoonMind-launched agent runtimes to be structurally limited to MoonMind-contracted external-service and publish paths so workflows produce deterministic, attributable outcomes across current and future runtimes.

**Independent Test**: Launch representative managed-agent workflows with a restricted skill contract, attempt Jira and repository bypass actions from inside the agent session, and verify bypasses fail while MoonMind-owned Jira and publish operations complete or reconcile with structured evidence.

## Phase 1: Setup

**Purpose**: Confirm active artifacts and prepare task-specific test files without changing runtime behavior.

- [ ] T001 Verify active feature artifacts exist and preserve MM-680 in specs/335-agent-tool-surface-isolation/spec.md, specs/335-agent-tool-surface-isolation/plan.md, specs/335-agent-tool-surface-isolation/research.md, specs/335-agent-tool-surface-isolation/data-model.md, specs/335-agent-tool-surface-isolation/quickstart.md, and specs/335-agent-tool-surface-isolation/contracts/managed-runtime-isolation-contract.md (FR-012, SC-006)
- [ ] T002 [P] Add task-scope test module placeholder for skill surface contract validation in tests/unit/workflows/skills/test_tool_surface_contracts.py (FR-002, DESIGN-REQ-007)
- [ ] T003 [P] Add task-scope test module placeholder for managed runtime launcher isolation in tests/unit/workflows/temporal/runtime/test_launcher_surface_contracts.py (FR-001, FR-003)
- [ ] T004 [P] Add task-scope test module placeholder for publish lease classification in tests/unit/workflows/temporal/test_publish_branch_lease.py (FR-008, DESIGN-REQ-011)
- [ ] T005 [P] Add task-scope integration test module placeholder for managed runtime isolation in tests/integration/temporal/test_agent_runtime_surface_isolation.py (SCN-001, SCN-002, SCN-003)
- [ ] T006 [P] Add task-scope integration test module placeholder for publish reconciliation in tests/integration/temporal/test_publish_reconciliation.py (SCN-004, SCN-005)

## Phase 2: Foundational

**Purpose**: Establish shared contract and diagnostic test scaffolding that blocks story implementation.

- [ ] T007 [P] Add failing unit tests for normalized SkillSurfaceContract parsing, explicit empty-surface declarations, and fail-closed missing surface fields in tests/unit/workflows/skills/test_tool_surface_contracts.py (FR-002, DESIGN-REQ-012)
- [ ] T008 [P] Add failing unit tests for IsolationDiagnostic redaction and stable reason codes in tests/unit/workflows/temporal/test_isolation_diagnostics.py (FR-009, DESIGN-REQ-014)
- [ ] T009 [P] Add failing unit tests for managed runtime service-identity validation rejecting operator-account OAuth and connector grants in tests/unit/workflows/temporal/runtime/test_launcher_surface_contracts.py (FR-001, DESIGN-REQ-003, DESIGN-REQ-006)
- [ ] T010 [P] Add failing unit tests for direct publish authority contract validation in tests/unit/workflows/temporal/test_agent_runtime_activities.py (FR-005, FR-006, DESIGN-REQ-009)
- [ ] T011 Run `./tools/test_unit.sh tests/unit/workflows/skills/test_tool_surface_contracts.py tests/unit/workflows/temporal/runtime/test_launcher_surface_contracts.py tests/unit/workflows/temporal/test_isolation_diagnostics.py tests/unit/workflows/temporal/test_agent_runtime_activities.py` and confirm T007-T010 fail for the expected missing contract/diagnostic behavior (FR-001, FR-002, FR-005, FR-006, FR-009)
- [ ] T012 Implement SkillSurfaceContract and publish-authority metadata models in moonmind/workflows/skills/tool_plan_contracts.py and moonmind/workflows/skills/contracts.py (FR-002, DESIGN-REQ-007, DESIGN-REQ-012)
- [ ] T013 Implement IsolationDiagnostic model and redaction helpers in moonmind/workflows/temporal/isolation_diagnostics.py (FR-009, DESIGN-REQ-014)
- [ ] T014 Wire contract metadata through resolved skillset outputs in moonmind/workflows/skills/resolver.py and moonmind/workflows/skills/run_projection.py without embedding large skill content in workflow history (FR-002, FR-003, DESIGN-REQ-005)
- [ ] T015 Run `./tools/test_unit.sh tests/unit/workflows/skills/test_tool_surface_contracts.py tests/unit/workflows/temporal/test_isolation_diagnostics.py` and confirm foundational contract and diagnostic tests pass (FR-002, FR-009)

## Phase 3: Story - Isolate Agent Tool Surfaces

**Summary**: Agent sessions cannot use account-level connectors, unmanaged external-service routes, or in-session publish credentials to bypass MoonMind-owned tool and publish boundaries, while MoonMind-owned reconciliation handles pre-existing remote state without terminal workflow failures.

**Independent Test**: Launch representative managed-agent workflows with a restricted skill contract, attempt non-contract Jira/GitHub/egress/publish actions, and verify denials plus publish reconciliation outcomes through sanitized diagnostics.

**Traceability**: FR-001 through FR-012; SCN-001 through SCN-006; SC-001 through SC-006; DESIGN-REQ-001 through DESIGN-REQ-015.

**Unit Test Plan**: Validate contract parsing, launcher diff behavior, service identity guards, egress policy rendering, direct publish denial, GitHub PR adoption, lease-miss classification, and diagnostics redaction.

**Integration Test Plan**: Cover launch rejection, non-contract egress denial, direct publish denial, original incident replay shape, existing PR adoption, lease-miss structured conflict, and runtime parity using hermetic or mocked provider surfaces.

### Unit Tests (write first)

- [ ] T016 [P] Add failing unit tests for launcher surface diff rejection of undeclared tools, MCP servers, connectors, egress destinations, and publish authority in tests/unit/workflows/temporal/runtime/test_launcher_surface_contracts.py (FR-003, FR-004, FR-010, DESIGN-REQ-001, DESIGN-REQ-005, DESIGN-REQ-008)
- [ ] T017 [P] Add failing unit tests for agent runtime request construction carrying sanitized surface contract refs and selected-skill metadata in tests/unit/workflows/temporal/workflows/test_run_agent_dispatch.py (FR-003, FR-010, DESIGN-REQ-012)
- [ ] T018 [P] Add failing unit tests for GitHubService existing PR lookup/adoption before creation in tests/unit/workflows/adapters/test_github_service.py (FR-007, SCN-004, SC-003, DESIGN-REQ-010)
- [ ] T019 [P] Add failing unit tests for repo.create_pr activity returning adopted PR results without raising duplicate validation errors in tests/unit/workflows/temporal/test_jules_activities.py (FR-007, SCN-004)
- [ ] T020 [P] Add failing unit tests for branch publish command construction using lease semantics and returning retryable lease_conflict results in tests/unit/workflows/temporal/test_publish_branch_lease.py (FR-008, SCN-005, SC-004, DESIGN-REQ-011)
- [ ] T021 [P] Add failing unit tests for direct publish attempt denial evidence and publish projection filtering in tests/unit/workflows/temporal/test_agent_runtime_activities.py (FR-005, FR-006, SCN-003, DESIGN-REQ-004, DESIGN-REQ-009)
- [ ] T022 [P] Add failing unit tests for blocked surface diagnostics covering egress_blocked, surface_rejected, direct_publish_denied, pull_request_adopted, and publish_lease_conflict in tests/unit/workflows/temporal/test_isolation_diagnostics.py (FR-009, SC-002, DESIGN-REQ-014)
- [ ] T023 [P] Add verification-only unit test proving MM-680 traceability remains preserved in specs/335-agent-tool-surface-isolation/plan.md and specs/335-agent-tool-surface-isolation/tasks.md in tests/unit/specs/test_mm680_traceability.py (FR-012, SC-006, DESIGN-REQ-015)

### Integration Tests (write first)

- [ ] T024 [P] Add failing integration_ci test for managed runtime launch rejection when operator-account connector grants are present in tests/integration/temporal/test_agent_runtime_surface_isolation.py (FR-001, SCN-001, SC-001, DESIGN-REQ-003, DESIGN-REQ-006)
- [ ] T025 [P] Add failing integration_ci test for non-contract egress denial with sanitized egress_blocked diagnostics in tests/integration/temporal/test_agent_runtime_surface_isolation.py (FR-004, SCN-002, SC-002, DESIGN-REQ-008)
- [ ] T026 [P] Add failing integration_ci test for direct in-session git push, gh pr create, and raw provider mutation denial without external state mutation in tests/integration/temporal/test_agent_runtime_surface_isolation.py (FR-005, FR-006, SCN-003, SC-002, DESIGN-REQ-004, DESIGN-REQ-009)
- [ ] T027 [P] Add failing integration_ci replay test for the MM-680 original incident shape where Jira operations use MoonMind trusted tools and account-level connector paths are unavailable in tests/integration/temporal/test_mm680_original_incident.py (FR-001, FR-003, SCN-001, DESIGN-REQ-001, DESIGN-REQ-003)
- [ ] T028 [P] Add failing integration test for repo.create_pr adopting an existing head/base pull request in tests/integration/temporal/test_publish_reconciliation.py (FR-007, SCN-004, SC-003, DESIGN-REQ-010)
- [ ] T029 [P] Add failing integration test for remote branch lease miss returning retryable structured conflict evidence in tests/integration/temporal/test_publish_reconciliation.py (FR-008, SCN-005, SC-004, DESIGN-REQ-011)
- [ ] T030 [P] Add failing integration_ci runtime parity test proving the same contract validation applies to Codex, Claude Code, Gemini, and future adapter registration fixtures in tests/integration/temporal/test_runtime_parity_surface_contract.py (FR-010, SCN-006, SC-005, DESIGN-REQ-002, DESIGN-REQ-012)

### Red-First Confirmation

- [ ] T031 Run `./tools/test_unit.sh tests/unit/workflows/temporal/runtime/test_launcher_surface_contracts.py tests/unit/workflows/temporal/workflows/test_run_agent_dispatch.py tests/unit/workflows/adapters/test_github_service.py tests/unit/workflows/temporal/test_jules_activities.py tests/unit/workflows/temporal/test_publish_branch_lease.py tests/unit/workflows/temporal/test_agent_runtime_activities.py tests/unit/workflows/temporal/test_isolation_diagnostics.py tests/unit/specs/test_mm680_traceability.py` and confirm T016-T023 fail for expected missing MM-680 behavior before production changes (FR-001 through FR-012)
- [ ] T032 Run `./tools/test_integration.sh` or targeted `pytest tests/integration/temporal/test_agent_runtime_surface_isolation.py tests/integration/temporal/test_mm680_original_incident.py tests/integration/temporal/test_publish_reconciliation.py tests/integration/temporal/test_runtime_parity_surface_contract.py -m integration_ci -q --tb=short` and confirm T024-T030 fail for expected missing MM-680 behavior before production changes (SCN-001 through SCN-006)

### Conditional Fallback for Implemented-Unverified Rows

- [ ] T033 If T023 fails for missing traceability, update specs/335-agent-tool-surface-isolation/spec.md, specs/335-agent-tool-surface-isolation/plan.md, specs/335-agent-tool-surface-isolation/research.md, specs/335-agent-tool-surface-isolation/data-model.md, specs/335-agent-tool-surface-isolation/quickstart.md, specs/335-agent-tool-surface-isolation/contracts/managed-runtime-isolation-contract.md, specs/335-agent-tool-surface-isolation/contracts/publish-reconciliation-contract.md, and specs/335-agent-tool-surface-isolation/tasks.md to preserve MM-680 and the original preset brief (FR-012, SC-006, DESIGN-REQ-015)

### Implementation

- [ ] T034 Implement launch-time service identity and operator connector rejection in moonmind/workflows/temporal/runtime/launcher.py and moonmind/workflows/temporal/activity_runtime.py (FR-001, SCN-001, SC-001, DESIGN-REQ-003, DESIGN-REQ-006)
- [ ] T035 Implement closed runtime surface diff validation against SkillSurfaceContract in moonmind/workflows/temporal/runtime/launcher.py (FR-003, FR-010, SCN-002, SCN-006, DESIGN-REQ-001, DESIGN-REQ-012)
- [ ] T036 Implement runtime request propagation of surface contract refs and sanitized selected-skill metadata in moonmind/workflows/temporal/workflows/run.py and moonmind/workflows/temporal/workflows/agent_run.py (FR-003, FR-010)
- [ ] T037 Implement per-skill egress policy materialization and denied-egress diagnostic emission in moonmind/workflows/temporal/runtime/launcher.py and moonmind/workflows/temporal/workers.py (FR-004, SCN-002, SC-002, DESIGN-REQ-008)
- [ ] T038 Implement direct publish authority removal or neutralization for managed runtime workspaces in moonmind/workflows/temporal/activity_runtime.py and moonmind/agents/codex_worker/worker.py (FR-005, FR-006, SCN-003, DESIGN-REQ-004, DESIGN-REQ-009)
- [ ] T039 Implement direct_publish_denied diagnostics for in-session publish attempts in moonmind/workflows/temporal/isolation_diagnostics.py and moonmind/workflows/temporal/activity_runtime.py (FR-006, FR-009, SCN-003)
- [ ] T040 Implement existing pull request lookup and adoption before creation in moonmind/workflows/adapters/github_service.py (FR-007, SCN-004, SC-003, DESIGN-REQ-010)
- [ ] T041 Update repo.create_pr activity result handling for adopted PRs in moonmind/workflows/temporal/activities/jules_activities.py and moonmind/workflows/temporal/workflows/run.py (FR-007, SCN-004)
- [ ] T042 Implement lease-aware branch publish state, fetch-on-lease-miss, and retryable lease_conflict outputs in moonmind/workflows/temporal/activity_runtime.py (FR-008, SCN-005, SC-004, DESIGN-REQ-011)
- [ ] T043 Wire sanitized isolation and publish diagnostics into workflow/activity outputs and artifact refs in moonmind/workflows/temporal/isolation_diagnostics.py, moonmind/workflows/temporal/activity_runtime.py, and moonmind/workflows/temporal/workflows/run.py (FR-009, DESIGN-REQ-014)
- [ ] T044 Remove superseded internal fallback or denylist-only paths uncovered by T034-T043 in moonmind/workflows/temporal/runtime/launcher.py, moonmind/agents/codex_worker/worker.py, and related tests (DESIGN-REQ-015)

### Story Validation

- [ ] T045 Run `./tools/test_unit.sh tests/unit/workflows/skills/test_tool_surface_contracts.py tests/unit/workflows/temporal/runtime/test_launcher_surface_contracts.py tests/unit/workflows/temporal/workflows/test_run_agent_dispatch.py tests/unit/workflows/adapters/test_github_service.py tests/unit/workflows/temporal/test_jules_activities.py tests/unit/workflows/temporal/test_publish_branch_lease.py tests/unit/workflows/temporal/test_agent_runtime_activities.py tests/unit/workflows/temporal/test_isolation_diagnostics.py tests/unit/specs/test_mm680_traceability.py` and fix failures until the story unit suite passes (FR-001 through FR-012)
- [ ] T046 Run `./tools/test_integration.sh` and fix or document exact environment blockers until required `integration_ci` evidence passes for MM-680 scenarios (SCN-001 through SCN-006, SC-001 through SC-005)
- [ ] T047 Execute the end-to-end quickstart validation in specs/335-agent-tool-surface-isolation/quickstart.md and record evidence in specs/335-agent-tool-surface-isolation/quickstart.md or a local verification artifact (FR-011, SC-005)
- [ ] T048 Confirm no diagnostics, logs, artifacts, or task outputs include raw credentials, tokenized URLs, auth headers, cookies, or private key material in moonmind/workflows/temporal/isolation_diagnostics.py test evidence (FR-009, DESIGN-REQ-014)

## Phase 4: Polish and Verification

**Purpose**: Strengthen the completed single story without adding hidden scope.

- [ ] T049 [P] Update canonical docs only if desired-state behavior changed, keeping migration details in specs/335-agent-tool-surface-isolation/plan.md and specs/335-agent-tool-surface-isolation/tasks.md (DESIGN-REQ-015)
- [ ] T050 [P] Review docs/ManagedAgents/ManagedAgentsGit.md and docs/Steps/SkillAndPlanContracts.md for stale statements after implementation and update only durable desired-state sections if needed (FR-005, FR-007, FR-008)
- [ ] T051 Run `./tools/test_unit.sh` for final full unit verification and record pass/fail evidence in final implementation notes (FR-011, SC-005)
- [ ] T052 Run `./tools/test_integration.sh` for final hermetic integration verification and record pass/fail evidence in final implementation notes (FR-011, SC-005)
- [ ] T053 Run `/moonspec-verify` after implementation and tests pass, using specs/335-agent-tool-surface-isolation/spec.md and the preserved MM-680 Jira preset brief as the final alignment source (FR-012, SC-006)

## Dependencies and Execution Order

### Phase Dependencies

- Setup (Phase 1) has no dependencies.
- Foundational (Phase 2) depends on Phase 1 and blocks story implementation.
- Story (Phase 3) depends on Phase 2 and must follow test-first ordering.
- Polish and Verification (Phase 4) depends on completed implementation plus passing targeted story tests.

### Story Ordering

- T016-T023 and T024-T030 must be authored before production implementation tasks.
- T031 and T032 must confirm red-first failures before T034-T044.
- T033 is conditional and runs only if implemented_unverified traceability checks fail.
- T034-T039 establish runtime isolation before publish reconciliation validation is considered complete.
- T040-T042 implement publish reconciliation and lease conflict behavior.
- T043-T044 complete diagnostics and remove superseded denylist-only or fallback paths.
- T045-T048 validate the story before Phase 4.

### Parallel Opportunities

- T002-T006 can run in parallel because they create different test files.
- T007-T010 can run in parallel because they target different test files.
- T016-T023 can run in parallel after Phase 2 because they target distinct unit test files.
- T024-T030 can run in parallel after Phase 2 because they target distinct integration scenarios.
- T049-T050 can run in parallel after the story is complete.

## Parallel Example

```bash
Task: "Add failing unit tests for launcher surface diff rejection in tests/unit/workflows/temporal/runtime/test_launcher_surface_contracts.py"
Task: "Add failing unit tests for GitHubService existing PR adoption in tests/unit/workflows/adapters/test_github_service.py"
Task: "Add failing integration_ci test for non-contract egress denial in tests/integration/temporal/test_agent_runtime_surface_isolation.py"
```

## Implementation Strategy

1. Preserve the one-story MM-680 scope and original brief traceability.
2. Write unit tests and integration tests first for every missing or partial status row.
3. Confirm red-first failures before modifying production code.
4. Implement contract models, launcher enforcement, egress/publish denial, publish adoption, lease conflict handling, and diagnostics in existing runtime and provider boundaries.
5. Run targeted unit and integration evidence, then full `./tools/test_unit.sh` and `./tools/test_integration.sh`.
6. Run `/moonspec-verify` against the preserved MM-680 source request.

## Coverage Summary

- Code-and-test work: all `missing` and `partial` rows in `plan.md`, including FR-001 through FR-011, SCN-001 through SCN-006, SC-001 through SC-005, and DESIGN-REQ-001 through DESIGN-REQ-014.
- Verification-only / conditional fallback: FR-012, SC-006, DESIGN-REQ-015 via T023, T033, and T053.
- Already verified rows: none.
- Unit coverage: T007-T010, T016-T023, T031, T045, T051.
- Integration coverage: T024-T030, T032, T046, T052.
- Final verification: T053.
