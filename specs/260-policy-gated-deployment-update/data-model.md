# Data Model: Policy-Gated Deployment Update API

## DeploymentUpdateRequest

- `stack`: allowlisted deployment stack key. Required.
- `image.repository`: allowlisted image repository for the selected stack. Required.
- `image.reference`: syntactically valid image reference or digest. Required.
- `mode`: policy-permitted update mode. Required.
- `removeOrphans`, `wait`, `runSmokeCheck`, `pauseWork`, `pruneOldImages`: typed boolean options.
- `reason`: non-empty operator reason. Required.

Validation rules:
- Unknown fields are rejected.
- Unknown stacks, unapproved repositories, invalid references, unsupported modes, and empty reasons are rejected before workflow or tool execution.
- Arbitrary shell commands, Compose files, host paths, and updater runner image choices are not part of the model and are rejected as unknown fields.

## DeploymentUpdateRun

- `deploymentUpdateRunId`: generated deployment update run identifier.
- `taskId`: task or run handle available at submission time.
- `workflowId`: workflow handle available at submission time.
- `status`: `QUEUED`.

State transitions:
- `QUEUED` is the only state emitted by this story's submission API.

## DeploymentStackState

- `stack`: allowlisted stack key.
- `projectName`: Compose project name.
- `configuredImage`: configured image reference for the stack.
- `runningImages`: service image observations with service name, image, image ID, and digest when known.
- `services`: service state observations with service name, state, and health when known.
- `lastUpdateRunId`: last deployment update run identifier when known.

## ImageTargetPolicy

- `repository`: allowed image repository.
- `allowedReferences`: configured allowed mutable references.
- `recentTags`: known recent tags when available.
- `digestPinningRecommended`: true when mutable tag usage is allowed and digest pinning should be preferred.

## Persistence

No new persistent table is introduced for `MM-518`. This story defines typed API and policy boundaries; durable execution history and richer deployment state can be layered behind the same contract in later stories.
