# Quickstart: Single Authored Branch Field

## Scope

Validate MM-668 against `specs/336-single-authored-branch-field/spec.md`.

## Focused Unit/UI Iteration

Frontend create/edit/rerun behavior:

```bash
./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx
```

Python task contract and runtime boundary behavior:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh \
  tests/unit/workflows/tasks/test_task_contract.py \
  tests/unit/api/routers/test_executions.py \
  tests/unit/agents/codex_worker/test_worker.py
```

Expected test additions:
- New authored Create-page submissions include `task.git.branch` and omit legacy branch fields.
- Edit/rerun patch creation strips legacy branch fields.
- Target-only legacy reconstruction does not promote `targetBranch` to active `branch`.
- Two-branch branch-publish reconstruction surfaces a warning.
- Task contract rejects or strips active `targetBranch` for new authored submissions according to the final contract decision.
- Worker preparation does not read authored `git.targetBranch` as active branch input.

## Focused Integration Verification

Task-shaped submission and normalization routes:

```bash
./tools/test_integration.sh
```

For local focused iteration before the full suite, use the same compose-backed pytest service that `./tools/test_integration.sh` uses and keep the `integration_ci` marker:

```bash
docker compose -f docker-compose.test.yaml run --rm pytest \
  bash -lc "pytest tests/integration/api/test_task_contract_normalization.py tests/integration/temporal/test_task_shaped_submission_normalization.py -m 'integration_ci' --tb=short"
```

Expected integration additions:
- New submitted task payloads reject `targetBranch` aliases.
- Canonical task snapshots and persisted original task input snapshots omit active `targetBranch`.
- Legacy reconstruction evidence preserves warnings/metadata without active target-branch semantics.

## End-to-End Story Check

1. Create a new task with publish mode `branch` and a selected branch.
2. Confirm the emitted task payload contains `task.git.branch` and `task.publish.mode`.
3. Confirm the emitted task payload and persisted authored snapshot do not contain active `startingBranch` or `targetBranch`.
4. Reconstruct a legacy snapshot with only `startingBranch`; confirm it becomes the authored branch with no warning.
5. Reconstruct a legacy target-only snapshot; confirm the target value is shown only as historical/warning context and is not submitted as active branch.
6. Reconstruct a legacy two-branch branch-publish snapshot; confirm a warning appears and subsequent submission omits legacy fields.
7. Confirm runtime preparation uses only authored `task.git.branch` and generated runtime branch metadata remains diagnostic.

## Final Verification

Before `/moonspec-verify`, run:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

When Docker is available:

```bash
./tools/test_integration.sh
```
