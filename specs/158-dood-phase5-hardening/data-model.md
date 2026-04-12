# Data Model: DooD Phase 5 Hardening

## RunnerProfile

Represents a curated, deployment-owned workload class.

**Fields**

- `id`: Stable profile identifier used by tools and workload requests.
- `kind`: Workload lifecycle class; Phase 5 remains focused on one-shot workloads.
- `image`: Approved workload image reference with explicit tag or digest.
- `entrypoint` / `commandWrapper`: Profile-owned execution wrapper.
- `workdirTemplate`: Approved workspace-relative workdir contract.
- `requiredMounts`: Mounts required for workspace/artifact access.
- `optionalMounts`: Approved cache or toolchain mounts.
- `envAllowlist`: Environment override keys a request may provide.
- `networkPolicy`: Approved network posture; host networking is rejected by default.
- `resources`: Default and maximum resource profile.
- `timeoutSeconds` / `maxTimeoutSeconds`: Default and maximum execution duration.
- `cleanup`: Removal and termination grace policy.
- `devicePolicy`: Approved device access policy; default is no device access.
- `maxConcurrency`: Maximum active workloads allowed for this profile on a worker process.

**Validation Rules**

- Image must include an explicit tag or digest and must not use `latest`.
- Image registry/provenance must match the deployment allowlist.
- Mounts must be approved named volumes with safe absolute targets.
- Managed-runtime auth, credential, or secret volumes must be rejected.
- Environment overrides outside `envAllowlist` must be rejected.
- Resource and timeout overrides must not exceed profile maxima.
- Unsupported network, privileged, or device settings fail closed.

## WorkloadRequest

Represents one executable-tool request to run a Docker-backed workload.

**Fields**

- `profileId`: Selected runner profile.
- `taskRunId`, `stepId`, `attempt`: Producing execution identity.
- `toolName`: Tool that requested the workload.
- `repoDir`: Workspace repo directory.
- `artifactsDir`: Step artifacts directory.
- `command`: Workload command arguments.
- `envOverrides`: Request-scoped environment values limited by the selected profile.
- `timeoutSeconds`: Optional timeout override.
- `resources`: Optional resource overrides.
- `declaredOutputs`: Optional output artifact declarations.
- `sessionId`, `sessionEpoch`, `sourceTurnId`: Optional association metadata only.

**Validation Rules**

- Request workspace paths must remain under the configured workspace root.
- Session association fields must not redefine workload identity.
- Declared outputs must stay under `artifactsDir` and must not claim session continuity artifact classes.
- Raw image, mount, device, or privileged inputs are not accepted by normal workload tools.

## ValidatedWorkloadRequest

Represents a workload request after profile and policy validation.

**Fields**

- `request`: Parsed workload request.
- `profile`: Resolved runner profile.
- `ownership`: Deterministic ownership metadata.
- `containerName`: Deterministic workload container name.

**State Transition**

```text
requested -> validated -> launched -> completed | failed | timed_out | canceled
requested -> denied
launched -> orphaned -> swept
```

## WorkloadOwnershipMetadata

Represents bounded labels and metadata used for traceability and cleanup.

**Fields**

- `kind`: Always workload for DooD workload containers.
- `taskRunId`, `stepId`, `attempt`: Producing execution identity.
- `toolName`: Tool name that requested the workload.
- `workloadProfile`: Selected runner profile.
- `sessionId`, `sessionEpoch`: Optional association metadata.
- `expiresAt`: Operational TTL used by cleanup for launched containers.

**Validation Rules**

- Ownership labels must be deterministic and non-secret.
- Cleanup must require MoonMind workload ownership labels before removal.

## WorkloadResult

Represents bounded outcome metadata for a workload run.

**Fields**

- `requestId`: Workload request/container identifier.
- `profileId`: Selected profile.
- `status`: `succeeded`, `failed`, `timed_out`, or `canceled`.
- `labels`: Ownership labels.
- `exitCode`: Container process exit code when available.
- `startedAt`, `completedAt`, `durationSeconds`: Timing metadata.
- `timeoutReason`: Timeout/cancel reason when applicable.
- `stdoutRef`, `stderrRef`, `diagnosticsRef`: Runtime artifact references.
- `outputRefs`: Declared and runtime output artifact references.
- `metadata`: Bounded workload metadata, artifact publication status, and non-secret diagnostics.

## PolicyDenial

Represents a fail-closed decision that prevents workload launch.

**Fields**

- `reason`: Stable denial category.
- `message`: Human-readable denial summary.
- `details`: Non-secret structured context.
- `retryable`: Whether repeating unchanged input can succeed.

**Allowed Reasons**

- `unknown_profile`
- `disallowed_image_registry`
- `disallowed_env_key`
- `disallowed_mount`
- `resource_request_too_large`
- `missing_fleet_capability`
- `concurrency_limit_exceeded`
- `invalid_profile`
- `invalid_request`

## CleanupRecord

Represents orphan cleanup evidence.

**Fields**

- `inspectedCount`: Number of workload-labeled containers inspected.
- `removedCount`: Number of expired workload containers removed.
- `removedContainerIds`: Bounded identifiers for removed workload containers.
- `skippedCount`: Number of non-expired or malformed workload-labeled containers skipped.
- `reason`: Cleanup trigger or sweep reason.

**Validation Rules**

- Cleanup must never remove containers without MoonMind workload ownership labels.
- Cleanup must skip containers with missing or future expiration metadata.
