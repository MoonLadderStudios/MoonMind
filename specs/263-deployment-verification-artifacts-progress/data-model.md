# Data Model: Deployment Verification, Artifacts, and Progress

## Deployment Verification Result

- `status`: one of `SUCCEEDED`, `FAILED`, `PARTIALLY_VERIFIED`.
- `updated_services`: services changed or verified as updated.
- `running_services`: structured service state evidence.
- `details`: verification checks, failure reasons, and smoke-check evidence from the runner.

Validation:
- Unsupported status values fail closed.
- `succeeded=True` is treated as `SUCCEEDED`.
- `PARTIALLY_VERIFIED` is allowed only as a final non-success tool result.

## Deployment Evidence Artifact

- `kind`: before-state, command-log, verification, or after-state.
- `payload`: recursively redacted structured mapping.
- `artifactRef`: opaque artifact reference returned by the evidence writer.

Validation:
- Secret-like keys and values are redacted before writing.
- Required refs are returned for phases reached by the lifecycle.

## Deployment Audit Metadata

- `runId`, `workflowId`, `taskId` when available.
- `stack`, `operator`, `operatorRole`, `reason`.
- `requestedImage`, `resolvedDigest`, `mode`, `options`.
- `startedAt`, `completedAt`, `finalStatus`, `failureReason` when applicable.

Validation:
- Metadata is compact and contains no raw credentials.
- Missing optional IDs are omitted or null, not invented.

## Deployment Progress Event

- `state`: documented lifecycle value.
- `message`: short operator-visible progress text.

Validation:
- Detailed command output is excluded from progress events.
- Terminal state matches final status.
