# Research: Task Details Edit and Rerun Actions

## REQ-001 Capability Contract

Decision: implemented_verified.
Evidence: `moonmind/schemas/temporal_models.py`, `frontend/src/generated/openapi.ts`.
Rationale: `canEditForRerun` belongs in the execution action capability model because `docs/UI/TaskDetailsPage.md` requires explicit capability fields as the source of truth.
Alternatives considered: infer edit-for-rerun visibility from terminal status in React; rejected because the UI must not infer unavailable actions from status alone.
Test implications: API unit tests and OpenAPI type regeneration.

## REQ-002 Terminal Edit-For-Rerun Statuses

Decision: implemented_verified.
Evidence: `api_service/api/routers/executions.py`, `tests/unit/api/routers/test_executions.py`.
Rationale: Terminal rerunnable statuses use the same workflow type, feature flag, and original snapshot gates as rerun so a failed task can offer **Edit task** and **Rerun** together.
Alternatives considered: expose edit-for-rerun only for failed tasks; rejected because the source design allows terminal completed/canceled/timed-out/terminated edit-for-rerun behavior.
Test implications: API unit coverage for failed enabled and missing-snapshot disabled cases.

## REQ-003 Non-Terminal Update Inputs

Decision: implemented_verified.
Evidence: executing-state assertions in `tests/unit/api/routers/test_executions.py`.
Rationale: Live input updates and edit-for-rerun are different actions. Running executions can expose `canUpdateInputs` without implying terminal edit-for-rerun.
Alternatives considered: reuse `canUpdateInputs` for all edit buttons; rejected because terminal failed executions are not mutated in place.
Test implications: API unit coverage.

## REQ-004 Through REQ-007 Task Details Rendering

Decision: implemented_verified.
Evidence: `frontend/src/entrypoints/task-detail.tsx`, `frontend/src/entrypoints/task-detail.test.tsx`, `frontend/src/lib/temporalTaskEditing.ts`.
Rationale: The Task Details page now renders **Edit task** when either update-in-place or edit-for-rerun is available, and separately renders **Rerun** when rerun is available.
Alternatives considered: keep the old **Edit** label; rejected because the source design specifies **Edit task**.
Test implications: frontend unit tests for running edit href, failed edit-for-rerun href, and additive **Rerun** visibility.

## Unit And Integration Strategy

Decision: unit tests are required; new hermetic integration tests are not required for this story.
Evidence: the change is limited to API serialization and frontend rendering, with no workflow/activity contract, database migration, or compose service change.
Rationale: Unit tests cover the behavior boundary directly. Existing full unit runner and focused Vitest tests provide regression evidence.
Alternatives considered: add integration_ci coverage; deferred because no compose-backed behavior changed.
Test implications: run targeted API tests, focused frontend tests, TypeScript typecheck, OpenAPI generation, and full unit verification.
