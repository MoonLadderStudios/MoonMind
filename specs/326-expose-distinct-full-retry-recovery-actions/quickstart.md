# Quickstart: Expose Distinct Full Retry Recovery Actions

Traceability: MM-632, FR-001 through FR-013.

## Purpose

Validate that failed task recovery exposes Edit task, Rerun, and Resume as separate user intents, that exact Rerun uses original input unchanged, and that full retry paths do not import Resume progress.

## Prerequisites

- Local Python and Node dependencies prepared by the repository test tooling.
- No external provider credentials required for planned unit tests.
- Hermetic integration tests should use local fixtures only and must be marked `integration_ci` if they are intended for required CI.

## Unit Test Strategy

Run focused Python route and service tests first:

```bash
./tools/test_unit.sh tests/unit/api/routers/test_executions.py tests/unit/workflows/temporal/test_temporal_service.py
```

Run focused frontend tests through the repo test runner:

```bash
./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx
./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx
```

Expected unit coverage:

- Task Detail renders Edit task, Rerun, and Resume independently for every capability combination.
- Edit task navigates to edit-for-rerun and loads editable values from the authoritative snapshot.
- Exact Rerun rejects or omits edited task/input mutation fields.
- Resume rejects edited task/input mutation fields.
- API/service preserve source execution state and produce safe disabled reasons.

## Integration Test Strategy

Add or update hermetic integration coverage for a failed `MoonMind.Run` execution with:

- authoritative original task input snapshot,
- optional Resume checkpoint evidence,
- failed terminal source state,
- step ledger or equivalent completed-step projection.

Run:

```bash
./tools/test_integration.sh
```

Expected integration coverage:

- Failed task detail response exposes independent action capabilities.
- Edited full retry creates a new from-beginning execution with a new snapshot and no imported completed progress.
- Exact Rerun creates a from-beginning execution with original input unchanged.
- Exact Rerun execution does not contain `resumeSource`, `resumeCheckpointRef`, preserved steps, or completed prior-progress imports.
- Resume unavailable cases return operator-readable reasons for missing, stale, unauthorized, or inconsistent checkpoint evidence.
- Source failed execution state, snapshot, step ledger, artifacts, and checkpoint refs are unchanged after recovery action attempts.

## End-To-End Story Check

1. Start with a failed task that has an authoritative task input snapshot.
2. Confirm failed task details show Edit task and Rerun as distinct actions.
3. Add valid Resume checkpoint evidence and confirm Resume from failed step appears separately.
4. Use Edit task, change authoring fields, and confirm the new execution starts from the beginning with its own snapshot.
5. Use Rerun and confirm the new execution starts from the beginning with original input unchanged.
6. Confirm neither full retry path imports completed progress from the failed source run.
7. Attempt Resume with invalid evidence and confirm it fails with an operator-readable unavailable reason.
8. Confirm `MM-632` and the original Jira preset brief remain present in MoonSpec artifacts and final verification evidence.

## Final Verification Commands

Before closing implementation, run the full required unit suite:

```bash
./tools/test_unit.sh
```

Run hermetic integration tests when Docker/compose is available:

```bash
./tools/test_integration.sh
```
