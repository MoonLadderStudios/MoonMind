# Quickstart: Temporal Rerun Submit

## Goal

Validate that a supported terminal `MoonMind.Run` execution can be rerun from the shared task form without queue-era fallback behavior.

## Preconditions

- `temporalTaskEditing` is enabled in the Mission Control dashboard configuration.
- A terminal `MoonMind.Run` execution exists with `actions.canRerun = true`.
- The execution detail payload contains enough inline or artifact-backed input state to reconstruct task instructions.
- Artifact create/upload endpoints are available for replacement input content.

## Manual Runtime Validation

1. Open the Temporal task detail page for a terminal `MoonMind.Run` execution with rerun capability.
2. Click **Rerun**.
3. Confirm the browser opens `/tasks/new?rerunExecutionId=<workflowId>`.
4. Confirm the page title is **Rerun Task** and the primary action is **Rerun Task**.
5. Confirm reconstructed fields are reviewable, including task instructions, repository, runtime, model, effort, publish mode, and primary skill when present.
6. Modify the instructions or another supported field.
7. Submit the form.
8. Confirm the request uses the source execution update path with `updateName = "RequestRerun"`.
9. Confirm artifact-backed or oversized input content creates a new input artifact reference and does not reuse the historical artifact as the replacement.
10. Confirm success returns to a Temporal detail view for the source or latest execution context.

## Negative Runtime Validation

- Open rerun mode for an unsupported workflow type and confirm submission is blocked.
- Open rerun mode for a terminal execution where `actions.canRerun = false` and confirm submission is blocked.
- Simulate a missing or malformed input artifact and confirm the form shows an explicit reconstruction failure.
- Simulate artifact creation failure and confirm no rerun update request is sent.
- Simulate backend stale-state rejection and confirm the error is shown without redirecting.
- Confirm no primary rerun path uses `/tasks/queue/new`, `editJobId`, or queue resubmit language.

## Automated Validation

Run focused frontend coverage during iteration:

```bash
npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx
```

Run final unit verification:

```bash
./tools/test_unit.sh
```

If frontend dependency resolution in the managed runtime does not expose npm script binaries on `PATH`, use the local binaries installed by the unit runner:

```bash
./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx
./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json
./node_modules/.bin/eslint -c frontend/eslint.config.mjs frontend/src/entrypoints/task-create.tsx frontend/src/entrypoints/task-create.test.tsx
```

## Expected Results

- Rerun mode submits `RequestRerun`; edit mode continues submitting `UpdateInputs`.
- Rerun mode never calls task creation or queue-era routes.
- Replacement input artifacts are created when required and historical artifacts remain immutable.
- Accepted reruns return operators to Temporal execution detail context.
- Rejected reruns show explicit operator-facing errors and do not redirect.
