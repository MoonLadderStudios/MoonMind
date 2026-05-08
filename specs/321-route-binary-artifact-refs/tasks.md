# Tasks: Route Binary Inputs Through Authorized Artifact Refs

**Input**: Design documents from `/work/agent_jobs/mm:59d3265d-03a5-445d-8ce4-876c3355a9de/repo/specs/321-route-binary-artifact-refs/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/binary-artifact-ref-contract.md`, `quickstart.md`

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks cover exactly one story: submit binary inputs as authorized artifact references for MM-628.

**Source Traceability**: MM-628 and DESIGN-REQ-002, DESIGN-REQ-007, DESIGN-REQ-020, and DESIGN-REQ-022 are preserved from `spec.md`.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh tests/unit/api/routers/test_executions.py tests/unit/api/routers/test_temporal_artifacts.py tests/unit/workflows/temporal/test_artifacts.py tests/unit/agents/codex_worker/test_attachment_materialization.py`
- Frontend unit tests: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`
- Integration tests: `pytest tests/integration/temporal/test_task_shaped_submission_normalization.py tests/integration/temporal/test_temporal_artifact_authorization.py tests/contract/test_temporal_execution_api.py -m 'integration_ci' -q --tb=short`
- Full unit verification: `./tools/test_unit.sh`
- Full integration verification: `./tools/test_integration.sh`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel because it touches different files and does not depend on incomplete work.
- Every task includes an exact file path and relevant requirement, scenario, success criterion, or source ID.

## Requirement Status Coverage

- Already verified, final traceability only: FR-001, FR-002, FR-003, FR-005, FR-007, FR-011, FR-012, SC-001, SC-002, SC-006, DESIGN-REQ-002.
- Verification tests plus conditional fallback: FR-004, FR-009, SC-005, DESIGN-REQ-007.
- Code-and-test completion required if tests expose gaps: FR-006, FR-008, FR-010, SC-003, SC-004, DESIGN-REQ-020, DESIGN-REQ-022.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm task inputs and preserve the existing artifact/task test surfaces before story work.

- [ ] T001 Confirm active feature artifacts and traceability for MM-628 in specs/321-route-binary-artifact-refs/spec.md, specs/321-route-binary-artifact-refs/plan.md, specs/321-route-binary-artifact-refs/research.md, specs/321-route-binary-artifact-refs/data-model.md, specs/321-route-binary-artifact-refs/contracts/binary-artifact-ref-contract.md, and specs/321-route-binary-artifact-refs/quickstart.md (FR-012, SC-006)
- [ ] T002 [P] Confirm existing frontend attachment upload tests still identify artifact create, complete, and submit ordering in frontend/src/entrypoints/task-create.test.tsx (FR-001, FR-002, FR-003, FR-005, SC-001, SC-002, DESIGN-REQ-002)
- [ ] T003 [P] Confirm existing API and worker attachment test fixtures are reusable in tests/unit/api/routers/test_executions.py, tests/unit/api/routers/test_temporal_artifacts.py, tests/unit/workflows/temporal/test_artifacts.py, and tests/unit/agents/codex_worker/test_attachment_materialization.py (FR-004, FR-006, FR-008, FR-009, FR-010)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Prepare shared test helpers and contract coverage before story behavior tests.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [ ] T004 Add shared unit-test helpers for completed, pending, failed, deleted, and wrong-owner Temporal artifacts in tests/unit/api/routers/test_executions.py (FR-004, FR-006, SC-003, DESIGN-REQ-007, DESIGN-REQ-022)
- [ ] T005 [P] Add shared artifact authorization fixtures for owner, non-owner, service principal, and execution-linked reads in tests/unit/workflows/temporal/test_artifacts.py (FR-008, FR-010, SC-004, DESIGN-REQ-020)
- [ ] T006 [P] Add worker materialization service-authorization test doubles in tests/unit/agents/codex_worker/test_attachment_materialization.py (FR-009, SC-005, DESIGN-REQ-020, DESIGN-REQ-022)
- [ ] T007 [P] Add contract fixture data for structured binary attachment refs in tests/contract/test_temporal_execution_api.py (FR-005, FR-006, FR-010, DESIGN-REQ-007, DESIGN-REQ-020)

**Checkpoint**: Foundation ready - story test and implementation work can now begin.

---

## Phase 3: Story - Submit Binary Inputs As Authorized Artifact References

**Summary**: As an operator, I want browser-selected binary inputs uploaded, authorized, finalized, and submitted as lightweight artifact refs so workflow histories and instructions never contain image bytes or storage credentials.

**Independent Test**: Submit a task draft with browser-selected binary inputs, verify upload intents are created and finalized before execution submission, confirm the execution payload contains only structured artifact refs, and validate preview/download plus worker materialization use authorized MoonMind service paths without exposing raw bytes or storage credentials in workflow history or inline instructions.

**Traceability**: FR-001 through FR-012, SC-001 through SC-006, DESIGN-REQ-002, DESIGN-REQ-007, DESIGN-REQ-020, DESIGN-REQ-022.

**Unit Test Plan**:

- API submission validation for pending/failed/deleted/missing/unauthorized binary refs.
- Artifact read policy and execution-scoped link behavior for preview/download and raw reads.
- Worker materialization with service-authorized downloads and failure diagnostics.
- Frontend regression coverage for upload intent, completion ordering, and structured refs.

**Integration Test Plan**:

- Task-shaped execution accepts completed refs and rejects invalid/unauthorized refs before execution creation.
- Artifact preview/download authorization is enforced for linked input attachments.
- Contract boundary proves accepted execution payloads contain structured refs and no raw bytes or credentials.

### Unit Tests (write first)

> Write these tests FIRST. Run them, confirm they FAIL for the expected reason, then implement only enough code to make them pass. For implemented-unverified rows, run verification tests first; skip conditional implementation tasks if the tests pass unchanged.

- [ ] T008 [P] Add failing unit tests for pending, failed, deleted, missing, and mismatched binary input artifact refs in tests/unit/api/routers/test_executions.py (FR-004, FR-006, SC-003, DESIGN-REQ-007, DESIGN-REQ-022)
- [ ] T009 Add failing unit test proving another user's completed input artifact ref is rejected before execution creation in tests/unit/api/routers/test_executions.py (FR-006, SC-003, DESIGN-REQ-020, DESIGN-REQ-022)
- [ ] T010 [P] Add failing unit tests for owner, non-owner, service principal, and execution-linked input artifact read policy in tests/unit/workflows/temporal/test_artifacts.py (FR-008, FR-010, SC-004, DESIGN-REQ-020)
- [ ] T011 [P] Add failing unit test proving worker input attachment materialization uses service-authorized artifact reads and does not expose browser storage credentials in tests/unit/agents/codex_worker/test_attachment_materialization.py (FR-009, SC-005, DESIGN-REQ-020, DESIGN-REQ-022)
- [ ] T012 [P] Add frontend regression test preserving upload-intent, upload-complete, and execution-submit ordering for objective and step binary refs in frontend/src/entrypoints/task-create.test.tsx (FR-001, FR-002, FR-003, FR-005, SC-001, SC-002, DESIGN-REQ-002, DESIGN-REQ-007)

### Integration Tests (write first)

- [ ] T013 [P] Add failing integration test for completed binary refs accepted into task-shaped execution submission in tests/integration/temporal/test_task_shaped_submission_normalization.py (FR-003, FR-004, FR-005, SC-001, SC-002, DESIGN-REQ-007)
- [ ] T014 Add failing integration test for pending, failed, and unauthorized binary refs rejected before execution creation in tests/integration/temporal/test_task_shaped_submission_normalization.py (FR-006, SC-003, DESIGN-REQ-020, DESIGN-REQ-022)
- [ ] T015 [P] Add failing integration test for browser preview/download authorization on execution-linked input attachments in tests/integration/temporal/test_temporal_artifact_authorization.py (FR-007, FR-008, FR-010, SC-004, DESIGN-REQ-020)
- [ ] T016 [P] Add failing contract test proving accepted task-shaped execution payloads contain structured refs only and no raw bytes, presigned URLs, or storage credentials in tests/contract/test_temporal_execution_api.py (FR-002, FR-005, SC-002, DESIGN-REQ-002)

### Red-First Confirmation

- [ ] T017 Run `./tools/test_unit.sh tests/unit/api/routers/test_executions.py tests/unit/workflows/temporal/test_artifacts.py tests/unit/agents/codex_worker/test_attachment_materialization.py` and confirm T008-T011 fail for the expected missing authorization/finalization/materialization behavior before implementation (FR-004, FR-006, FR-008, FR-009, FR-010)
- [ ] T018 Run `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx` and confirm T012 either passes as existing verified behavior or fails for the exact ordering regression to fix (FR-001, FR-002, FR-003, FR-005)
- [ ] T019 Run `pytest tests/integration/temporal/test_task_shaped_submission_normalization.py tests/integration/temporal/test_temporal_artifact_authorization.py tests/contract/test_temporal_execution_api.py -m 'integration_ci' -q --tb=short` and confirm T013-T016 fail for expected missing authorization/link-scope proof before implementation (SC-001, SC-002, SC-003, SC-004)

### Conditional Fallback Implementation

- [ ] T020 If T008 or T013 exposes missing finalized-ref handling, update input attachment status validation in api_service/api/routers/executions.py so only complete binary artifacts can be submitted and pending, failed, deleted, missing, content-type mismatched, and size-mismatched refs fail explicitly (FR-004, FR-006, SC-003, DESIGN-REQ-007)
- [ ] T021 If T009 or T014 exposes missing ownership checks, update input attachment submission authorization in api_service/api/routers/executions.py and moonmind/workflows/temporal/artifacts.py so unauthorized artifact refs are rejected before execution creation or linking (FR-006, SC-003, DESIGN-REQ-020, DESIGN-REQ-022)
- [ ] T022 If T010 or T015 exposes incomplete preview/download permission semantics, update read policy and router behavior in moonmind/workflows/temporal/artifacts.py and api_service/api/routers/temporal_artifacts.py so browser reads require owner, execution view permission, or service authorization (FR-007, FR-008, FR-010, SC-004, DESIGN-REQ-020)
- [ ] T023 If T011 exposes missing service authorization in worker materialization, update moonmind/agents/codex_worker/worker.py and the worker queue artifact read path so input attachment downloads use service-authorized MoonMind reads and fail explicitly without browser credentials (FR-009, SC-005, DESIGN-REQ-020, DESIGN-REQ-022)
- [ ] T024 If T014 or T015 exposes cross-execution reuse, update execution artifact linking in api_service/api/routers/executions.py and moonmind/workflows/temporal/artifacts.py so `input.attachment` links are execution-scoped and cannot be reused outside the authorized execution context (FR-010, SC-003, SC-004, DESIGN-REQ-020)

### Story Validation

- [ ] T025 Run focused unit commands `./tools/test_unit.sh tests/unit/api/routers/test_executions.py tests/unit/api/routers/test_temporal_artifacts.py tests/unit/workflows/temporal/test_artifacts.py tests/unit/agents/codex_worker/test_attachment_materialization.py` and `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`, then fix failures in api_service/api/routers/executions.py, api_service/api/routers/temporal_artifacts.py, moonmind/workflows/temporal/artifacts.py, moonmind/agents/codex_worker/worker.py, or frontend/src/entrypoints/task-create.tsx (FR-001 through FR-011)
- [ ] T026 Run focused integration command `pytest tests/integration/temporal/test_task_shaped_submission_normalization.py tests/integration/temporal/test_temporal_artifact_authorization.py tests/contract/test_temporal_execution_api.py -m 'integration_ci' -q --tb=short`, then fix boundary failures in api_service/api/routers/executions.py, api_service/api/routers/temporal_artifacts.py, moonmind/workflows/temporal/artifacts.py, or moonmind/agents/codex_worker/worker.py (SC-001 through SC-005, DESIGN-REQ-007, DESIGN-REQ-020, DESIGN-REQ-022)
- [ ] T027 Confirm MM-628 and the original Jira preset brief remain preserved in specs/321-route-binary-artifact-refs/spec.md, specs/321-route-binary-artifact-refs/plan.md, specs/321-route-binary-artifact-refs/research.md, specs/321-route-binary-artifact-refs/data-model.md, specs/321-route-binary-artifact-refs/contracts/binary-artifact-ref-contract.md, specs/321-route-binary-artifact-refs/quickstart.md, and specs/321-route-binary-artifact-refs/tasks.md (FR-012, SC-006)

**Checkpoint**: The story is fully testable independently when focused unit tests, focused integration tests, and traceability checks pass.

---

## Phase 4: Polish & Verification

**Purpose**: Strengthen the completed story without expanding scope.

- [ ] T028 [P] Review security-sensitive error messages and logs in api_service/api/routers/executions.py, api_service/api/routers/temporal_artifacts.py, and moonmind/workflows/temporal/artifacts.py so authorization failures do not expose credentials or storage paths (FR-006, FR-008, DESIGN-REQ-020)
- [ ] T029 [P] Update implementation notes in specs/321-route-binary-artifact-refs/research.md only if implementation discovers different evidence or status classifications during T020-T024 (FR-012, SC-006)
- [ ] T030 Run full unit verification with `./tools/test_unit.sh` from repository root (FR-001 through FR-012)
- [ ] T031 Run full hermetic integration verification with `./tools/test_integration.sh` from repository root (SC-001 through SC-005)
- [ ] T032 Run `/moonspec-verify` for specs/321-route-binary-artifact-refs/spec.md after implementation and tests pass, preserving MM-628 and DESIGN-REQ-002, DESIGN-REQ-007, DESIGN-REQ-020, and DESIGN-REQ-022 in verification output (FR-012, SC-006)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup completion and blocks story test/implementation work.
- **Story (Phase 3)**: Depends on Foundational completion.
- **Polish & Verification (Phase 4)**: Depends on focused story validation passing.

### Within The Story

- T008-T016 must be written before implementation tasks.
- T017-T019 must run before T020-T024.
- T020-T024 are conditional fallback implementation tasks and are skipped only when their verification tests pass without code changes.
- T025-T027 validate the complete one-story slice before polish.
- T030-T032 run only after focused unit and integration validation passes.

### Parallel Opportunities

- T002 and T003 can run in parallel.
- T005, T006, and T007 can run in parallel after T004 starts if helpers do not overlap.
- T008, T010, T011, T012, T015, and T016 touch different files and can be authored in parallel.
- T013 and T014 share `tests/integration/temporal/test_task_shaped_submission_normalization.py` and should not be authored in parallel with each other.
- T020-T024 touch overlapping authorization boundaries and should be sequenced after red-first results identify the exact gaps.
- T028 and T029 can run in parallel after T025-T027.

---

## Parallel Example: Story Phase

```bash
# Safe parallel test authoring after Phase 2:
Task: "T008 Add failing API input-ref validation tests in tests/unit/api/routers/test_executions.py"
Task: "T010 Add failing artifact read-policy tests in tests/unit/workflows/temporal/test_artifacts.py"
Task: "T011 Add failing worker materialization authorization test in tests/unit/agents/codex_worker/test_attachment_materialization.py"
Task: "T016 Add failing structured-ref-only contract test in tests/contract/test_temporal_execution_api.py"
```

---

## Implementation Strategy

### Test-Driven Story Delivery

1. Complete setup and shared fixture tasks.
2. Write unit and integration tests for partial and implemented-unverified rows first.
3. Run red-first commands and record whether tests fail for the expected missing behavior or pass as existing verified behavior.
4. Execute conditional fallback implementation tasks only for failed verification areas.
5. Re-run focused unit and integration commands.
6. Run full unit and integration suites.
7. Run `/moonspec-verify` and preserve MM-628 traceability.

### Requirement Status Handling

- `implemented_verified` rows receive regression and final validation coverage only.
- `implemented_unverified` rows receive verification tests plus conditional fallback implementation tasks.
- `partial` rows receive failing tests, red-first confirmation, and implementation tasks to close the gap.
- No task broadens scope beyond MM-628's binary artifact-ref submission, authorization, preview/download, and worker materialization boundaries.
