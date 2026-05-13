# Quickstart: Editable Full Retry Workflow

Traceability: MM-644.

## Prerequisites

- Local repo dependencies installed through the standard test runner.
- Unit tests must run through `./tools/test_unit.sh`.
- Hermetic integration tests must run through `./tools/test_integration.sh` when validating `integration_ci` coverage.
- No external provider credentials are required for planned tests.

## Test-First Validation Plan

1. Add UI unit tests for Task Detail and Create page.
   - Verify a failed execution with `canEditForRerun: true` links Edit task to `/tasks/new?rerunExecutionId=<id>&mode=edit`.
   - Verify edit-for-rerun mode loads the authoritative snapshot artifact and populates authoring fields.
   - Verify representative edits produce a `RequestRerun` update with changed payload.
   - Verify invalid authoring state blocks submission before the update call.
   - Verify missing/unreadable snapshot paths show operator-readable blocked state.

2. Add Python unit tests for API/action/provenance boundaries.
   - Verify action capability disabled reasons for missing, unreadable, unauthorized, and insufficient snapshots.
   - Verify edited full retry provenance uses `kind=edited_full_retry` with pinned source workflow/run IDs.
   - Verify exact rerun and edited full retry remain distinguishable.
   - Verify Resume-shaped source metadata is stripped from edited full retry.

3. Add hermetic integration tests for Temporal execution service behavior.
   - Start from a failed source execution with snapshot/progress/resume-like metadata.
   - Submit a changed `RequestRerun` representing edited full retry.
   - Assert a new execution is created for the terminal source.
   - Assert the new execution starts from the beginning and has no imported progress/checkpoint/resume refs.
   - Assert the new execution has its own task input snapshot reflecting edited input.
   - Assert the source execution state, snapshot, artifact refs, ledger/progress refs, and checkpoint refs remain unchanged.

## Focused Commands

UI-focused iteration:

```bash
./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx frontend/src/entrypoints/task-detail.test.tsx
```

Python unit iteration:

```bash
./tools/test_unit.sh tests/unit/api/routers/test_executions.py tests/unit/workflows/tasks/test_task_contract.py
```

Hermetic integration iteration:

```bash
./tools/test_integration.sh
```

Final unit verification:

```bash
./tools/test_unit.sh
```

## End-to-End Acceptance Check

A complete MM-644 implementation passes when:

- Edit task opens edit-for-rerun from a failed execution with an authoritative snapshot.
- The form is populated from the source snapshot and permits normal authoring edits.
- A changed edited retry creates a new execution from the beginning.
- The new execution has its own authoritative snapshot and `edited_full_retry` provenance.
- Source execution evidence remains immutable.
- Completed progress, Resume refs, and checkpoints are not imported into the new run.
- Ineligible snapshot cases are blocked with operator-readable reasons before retry work starts.
