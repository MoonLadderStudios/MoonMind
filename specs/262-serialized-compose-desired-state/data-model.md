# Data Model: Serialized Compose Desired-State Execution

## DeploymentDesiredState

Represents the durable desired image state for an allowlisted deployment stack.

Fields:
- `stack`: allowlisted stack identifier.
- `imageRepository`: repository portion of the desired image.
- `requestedReference`: requested tag or digest reference.
- `resolvedDigest`: digest-pinned reference when known.
- `reason`: optional administrator-provided reason for the update.
- `operator`: initiating principal when available.
- `createdAt`: UTC timestamp when desired state is persisted.
- `sourceRunId`: workflow/run/idempotency identifier when available.

Validation rules:
- `stack`, `imageRepository`, and `requestedReference` are required.
- `resolvedDigest` remains distinct from `requestedReference`.
- The store boundary must not accept caller-selected arbitrary file paths.

## DeploymentUpdateLifecycle

Represents the ordered lifecycle for one deployment update invocation.

States:
- `LOCKED`: per-stack lock acquired.
- `BEFORE_CAPTURED`: before-state evidence captured.
- `DESIRED_STATE_PERSISTED`: desired state durably written.
- `PULLED`: pull command completed.
- `UPDATED`: up command completed.
- `VERIFIED`: verification proved the desired state.
- `FAILED`: lifecycle failed or verification did not prove success.
- `RELEASED`: per-stack lock released.

Ordering rules:
- `LOCKED` must precede every side effect.
- `BEFORE_CAPTURED` must precede `DESIRED_STATE_PERSISTED`.
- `DESIRED_STATE_PERSISTED` must precede `PULLED` and `UPDATED`.
- `VERIFIED` must precede a `SUCCEEDED` result.
- `RELEASED` happens after terminal result construction or failure.

## DeploymentCommandPlan

Represents typed Compose command arguments built from validated tool inputs and policy.

Fields:
- `runnerMode`: `privileged_worker` or `ephemeral_updater_container`.
- `pullArgs`: command arguments for image pull.
- `upArgs`: command arguments for service recreation.

Validation rules:
- `changed_services` mode never includes `--force-recreate`.
- `force_recreate` mode includes `--force-recreate`.
- `removeOrphans` controls only `--remove-orphans`.
- `wait` controls only `--wait`.
- No shell snippets, caller paths, arbitrary flags, or runner image choices are represented.

## DeploymentUpdateResult

Represents the typed tool result.

Fields:
- `status`: `SUCCEEDED` or `FAILED`.
- `stack`
- `requestedImage`
- `resolvedDigest`
- `updatedServices`
- `runningServices`
- `beforeStateArtifactRef`
- `afterStateArtifactRef`
- `commandLogArtifactRef`
- `verificationArtifactRef`

Validation rules:
- `status` is `SUCCEEDED` only when verification succeeds.
- Evidence refs are populated from the artifact writer when their lifecycle steps run.
- `FAILED` verification results still include verification evidence when available.
