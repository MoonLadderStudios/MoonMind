# Research: Single Authored Branch Field

## Planning Setup

Decision: Use the active feature directory from `.specify/feature.json` and generate plan artifacts directly.
Evidence: `.specify/scripts/bash/setup-plan.sh --json` failed because the current branch is `change-jira-issue-mm-668-to-status-in-pr-d6e0f381`, not a numeric Speckit branch; `specs/336-single-authored-branch-field/spec.md` exists and passes the specify gate.
Rationale: The feature directory is already selected and the plan stage should not be blocked by branch naming when the managed workflow has provided an active spec.
Alternatives considered: Rename or checkout a numeric branch; rejected because this step is limited to planning artifacts and should not mutate git branch state.
Test implications: None beyond final artifact verification.

## FR-001 / FR-002 / FR-003 / DESIGN-REQ-009 New Authored Submission Shape

Decision: Treat the Create-page authored submission path as implemented and verified, but preserve it through regression tests when adjacent logic changes.
Evidence: `frontend/src/entrypoints/task-create.tsx` builds `task.git.branch` for submissions and `frontend/src/entrypoints/task-create.test.tsx` asserts a created task has `task.git == { branch: "feature/create-page" }` and does not contain `targetBranch` or `startingBranch`. The same submission path preserves `publish.mode` and top-level `publishMode`.
Rationale: Existing code and tests already cover the primary operator-authored UI path, so implementation should avoid churn there except where needed to keep edit/rerun paths consistent.
Alternatives considered: Rebuild the create branch form; rejected because current evidence already satisfies the single authored branch requirement for direct create.
Test implications: Final verification plus focused regression if touched.

## FR-004 / FR-010 Canonical Task Contract Cleanup

Decision: Treat canonical task contract behavior as partial because some current tests still encode `task.git.targetBranch` normalization into `branch`.
Evidence: `tests/integration/api/test_task_contract_normalization.py` and `tests/unit/workflows/tasks/test_task_contract.py` include target-branch normalization expectations, while `tests/unit/api/routers/test_executions.py` already rejects task-shaped `targetBranch` aliases at the API boundary.
Rationale: MM-668 is stricter than earlier normalization behavior: new authored contracts should not accept `targetBranch` as active branch input. The implementation should update the canonical task contract and tests to reject or strip active `targetBranch` for new submissions, while preserving historical metadata only where a legacy snapshot reconstruction path explicitly carries it.
Alternatives considered: Keep normalizing `targetBranch` to `branch`; rejected because that silently treats a legacy field as active authored input.
Test implications: Unit tests in `tests/unit/workflows/tasks/test_task_contract.py` and integration tests in `tests/integration/api/test_task_contract_normalization.py`.

## FR-005 Required Branch Intent

Decision: Treat required branch validation as implemented_unverified pending MM-668-specific tests.
Evidence: Create-page code validates publish mode and submits an explicit `git.branch` when a branch is selected, but the current tests do not prove that a missing required branch cannot be backfilled from legacy branch fields.
Rationale: This requirement is about preventing hidden fallback from legacy input. Verification should fail first if any path can derive active branch from `startingBranch` or `targetBranch` when `git.branch` is required.
Alternatives considered: Classify as missing; rejected because UI and API validation scaffolding already exists.
Test implications: Unit/UI test for create/edit branch-required behavior plus integration coverage for task-shaped submissions.

## FR-006 / SC-003 Legacy `startingBranch` Normalization

Decision: Treat safe `startingBranch` normalization as implemented_verified.
Evidence: `frontend/src/lib/temporalTaskEditing.ts` returns `startingBranch` as the reconstructed branch when no conflicting target branch exists, and existing task-create tests cover legacy reconstruction behavior.
Rationale: This behavior directly matches MM-668 for reconstructable legacy snapshots.
Alternatives considered: Remove `startingBranch` handling entirely; rejected because the spec requires safe legacy normalization.
Test implications: Preserve with regression tests while changing target-only and two-branch behavior.

## FR-007 / FR-008 / SC-004 Legacy `targetBranch` Is Historical Only

Decision: Treat target-branch handling as partial and requiring implementation changes.
Evidence: `frontend/src/lib/temporalTaskEditing.ts` currently returns `branch: targetBranch` for target-only legacy snapshots with a warning. `moonmind/agents/codex_worker/worker.py` reads `git.get("targetBranch")` as `new_branch_input`, which can influence active runtime branch preparation.
Rationale: The spec requires legacy `targetBranch` never to drive active editing, rerun, resubmission, or runtime preparation. Runtime-owned generated head/working branch metadata may still be emitted, but task-authored `git.targetBranch` must not be accepted as active input.
Alternatives considered: Warn but still prefill the target branch as active branch; rejected because it violates the historical-only rule.
Test implications: Frontend unit/UI tests for target-only reconstruction, worker unit tests for runtime preparation, and integration tests for submitted task payloads.

## FR-009 / FR-011 / SC-005 Reconstruction Warning Evidence

Decision: Treat warning behavior as implemented_unverified.
Evidence: `frontend/src/lib/temporalTaskEditing.ts` emits a warning for legacy two-branch non-PR cases and `frontend/src/entrypoints/task-create.test.tsx` checks the warning text for one reconstructed draft.
Rationale: The visible warning exists, but the implementation needs stronger proof that warning state survives edit/rerun and that unreconstructable snapshots cannot be silently submitted as equivalent.
Alternatives considered: Mark implemented_verified; rejected because current evidence is frontend-local and does not cover submission output.
Test implications: UI reconstruction tests plus integration verification of edit/rerun payload shape.

## Runtime Planning Boundary

Decision: Keep runtime-owned head/working branch metadata separate from authored branch input.
Evidence: `docs/Tasks/TaskPublishing.md` distinguishes authored base branch from runtime-owned PR head branch. `moonmind/agents/codex_worker/worker.py` emits `resolved.targetBranch` and `workingBranch` metadata during preparation.
Rationale: MM-668 forbids authored `targetBranch` as active input; it does not forbid runtime-owned branch metadata after planning. The implementation should rename or isolate authored-vs-runtime concepts where needed so generated target/head branches are not confused with user-authored fields.
Alternatives considered: Remove all `targetBranch` output everywhere; rejected because runtime diagnostics may need a generated work/head branch, and the source design allows runtime-managed PR head branches.
Test implications: Worker boundary tests should assert authored `git.targetBranch` is ignored/rejected while generated runtime branch metadata remains deterministic and redacted.

## Testing Strategy

Decision: Use frontend unit/UI tests for authoring and reconstruction, Python unit tests for task contract and worker preparation, and hermetic integration tests for API/task-shaped submission normalization.
Evidence: Existing tests are already located in `frontend/src/entrypoints/task-create.test.tsx`, `tests/unit/workflows/tasks/test_task_contract.py`, `tests/unit/api/routers/test_executions.py`, `tests/unit/agents/codex_worker/test_worker.py`, `tests/integration/api/test_task_contract_normalization.py`, and `tests/integration/temporal/test_task_shaped_submission_normalization.py`.
Rationale: These surfaces match the risk boundaries in the spec: UI authored contract, API normalization/rejection, snapshot reconstruction, and runtime planning.
Alternatives considered: Only frontend tests; rejected because runtime planning is explicitly in scope. Only backend tests; rejected because operator-facing reconstruction warnings are a UI behavior.
Test implications: Final verification should run `./tools/test_unit.sh` and, when Docker is available, `./tools/test_integration.sh`; focused iteration can target the files above.
