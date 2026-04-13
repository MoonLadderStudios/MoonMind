# Data Model: DooD Bounded Helper Containers

## Bounded Helper Workload

Represents a short-lived non-agent service container owned by one task step.

**Fields**:
- `kind`: helper workload discriminator such as `bounded_service`.
- `containerName`: deterministic helper container name derived from task run, step, and attempt.
- `taskRunId`: owner task execution identifier.
- `stepId`: owner step identifier.
- `attempt`: owner step attempt number.
- `toolName`: executable tool that requested the helper.
- `profileId`: selected helper-capable runner profile.
- `ttlSeconds`: explicit helper lifetime window for this launch.
- `expiresAt`: computed expiration timestamp for cleanup.
- `artifactsDir`: step artifacts directory.
- `sessionContext`: optional grouping context only, never identity.
- `status`: lifecycle status such as starting, ready, unhealthy, stopping, stopped, expired, canceled, or failed.

**Validation Rules**:
- Must include owner task, owner step, attempt, profile, artifacts directory, TTL, and readiness contract.
- TTL must be positive and no greater than the selected profile maximum.
- Must not use session identifiers as helper identity.
- Must use curated profile policy for image, mounts, env, network, device, resources, and cleanup.

**State Transitions**:
- requested -> starting -> ready -> stopping -> stopped
- requested -> starting -> unhealthy -> stopping -> stopped
- requested/starting/ready/unhealthy -> canceled -> stopping -> stopped
- ready/unhealthy -> expired -> swept

## Helper Runner Profile

Curated operator-owned policy for a helper class.

**Fields**:
- `id`: profile identifier.
- `kind`: must indicate helper capability.
- `image`: approved image reference from allowed registry policy.
- `entrypoint` / command wrapper: approved command shape.
- `workspaceMounts` / cache mounts: approved mount policy.
- `envAllowlist`: allowed environment override names.
- `networkPolicy`: allowed networking posture.
- `resourceProfile`: default and maximum resources.
- `helperTtlSeconds`: default helper TTL.
- `maxHelperTtlSeconds`: maximum allowed helper TTL.
- `readinessProbe`: required bounded readiness contract.
- `cleanupPolicy`: stop/kill/remove policy.
- `devicePolicy`: explicit device policy; default no implicit devices.

**Validation Rules**:
- Helper-capable profiles must include readiness and TTL policy.
- Profiles must not permit auth volumes or unsafe Docker socket mounts.
- Profiles must not default to privileged, host networking, or implicit device access.

## Helper Request

Validated request to start a bounded helper.

**Fields**:
- `profileId`, `taskRunId`, `stepId`, `attempt`, `toolName`.
- `repoDir`, `artifactsDir`.
- `command` / args intent constrained by profile.
- `envOverrides` constrained by allowlist.
- `ttlSeconds`.
- `timeoutSeconds` and resource overrides where permitted.
- Optional `sessionId`, `sessionEpoch`, `sourceTurnId` as grouping context.

**Validation Rules**:
- Reject missing owner step or TTL before launch.
- Reject TTL beyond profile maximum.
- Reject disallowed env, mounts, resources, network, device, or profile values.
- Reject use of helper request as managed-session identity.

## Helper Readiness Result

Evidence that the helper is usable or unhealthy.

**Fields**:
- `status`: ready, unhealthy, timed_out, canceled, or failed.
- `attempts`: number of readiness attempts.
- `startedAt`, `completedAt`, `durationSeconds`.
- `stdoutRef`, `stderrRef`, `diagnosticsRef` for bounded artifacts.
- `details`: non-secret bounded status details.

**Validation Rules**:
- Must not include raw secrets, prompts, transcripts, or unbounded logs.
- Must be publishable even when readiness fails.

## Helper Teardown Result

Evidence for explicit helper shutdown.

**Fields**:
- `status`: stopped, failed, canceled, or timed_out.
- `reason`: completion, cancellation, timeout, expired cleanup, or operator cleanup.
- `cleanupActions`: stop, kill, remove attempts and outcomes.
- `startedAt`, `completedAt`, `durationSeconds`.
- `diagnosticsRef` and cleanup metadata.

**Validation Rules**:
- Teardown must be best-effort and diagnosable.
- Cleanup must target helper ownership only.

## Helper Cleanup Record

Result of TTL-based orphan helper sweep.

**Fields**:
- `inspectedCount`, `removedCount`, `skippedFreshCount`, `skippedUnrelatedCount`.
- `removedContainerIds` or bounded identifiers.
- `ownershipBasis`: label and TTL basis used for removal.
- `completedAt`.

**Validation Rules**:
- Must only remove expired MoonMind-owned helpers.
- Must preserve fresh helpers, one-shot workload containers, session containers, and unrelated containers.
