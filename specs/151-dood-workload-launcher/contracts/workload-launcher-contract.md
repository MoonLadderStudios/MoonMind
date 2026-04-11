# Contract: Workload Launcher Activity

## Purpose

Define the Phase 2 runtime boundary for launching one Docker-backed workload container from a validated workload request. This contract is internal to MoonMind's control plane and does not create a public API endpoint.

## Activity Boundary

**Activity name**: `workload.run`

**Owning fleet**: Existing Docker-capable `agent_runtime` fleet

**Capability**: `docker_workload`

**Input**: A workload request payload that can be validated by the runner-profile registry.

**Output**: A bounded workload result payload.

## Input Contract

The activity accepts either:

- a workload request payload directly, or
- an envelope with a `request` field containing the workload request payload.

Required request fields:

- `profileId`
- `taskRunId`
- `stepId`
- `attempt`
- `toolName`
- `repoDir`
- `artifactsDir`
- `command`

Optional request fields:

- `envOverrides`
- `timeoutSeconds`
- `resources`
- `sessionId`
- `sessionEpoch`
- `sourceTurnId`

## Validation Contract

Before launching a container, MoonMind must:

- resolve the selected runner profile;
- reject unknown profiles;
- reject workspace paths outside the configured workspace root;
- reject environment overrides outside the profile allowlist;
- reject timeout and resource overrides above profile maxima;
- preserve session association metadata as grouping context only.

## Launch Contract

The launcher must derive:

- deterministic container name;
- deterministic `moonmind.*` labels;
- profile-approved workspace and cache mounts;
- selected network policy;
- selected CPU, memory, and shared-memory controls;
- selected entry behavior and command arguments;
- task repository workdir.

The request must not provide arbitrary image, mount, device, or Docker socket access.

## Result Contract

The result includes:

- `requestId`
- `profileId`
- `status`
- `labels`
- `exitCode` when available
- `startedAt`
- `completedAt`
- `durationSeconds`
- `timeoutReason` when applicable
- artifact reference fields reserved for later phases
- bounded diagnostics metadata

Status values:

- `succeeded`
- `failed`
- `timed_out`
- `canceled`

## Cleanup Contract

On normal completion:

- remove the container when cleanup policy requires removal.

On timeout:

- mark the result as timed out;
- attempt bounded stop;
- attempt bounded termination;
- remove the container when cleanup policy requires removal.

On cancellation:

- attempt bounded stop;
- attempt bounded termination;
- remove the container when cleanup policy requires removal;
- propagate cancellation to the caller.

## Orphan Lookup Contract

Cleanup utilities must support lookup by MoonMind ownership labels, including:

- `moonmind.kind=workload`
- `moonmind.task_run_id`
- `moonmind.step_id`
- `moonmind.attempt`
- `moonmind.tool_name`
- `moonmind.workload_profile`

## Non-Goals

- Exposing `container.run_workload` or `unreal.run_tests` tool definitions.
- Publishing stdout/stderr as durable artifacts.
- Implementing a bounded helper service lifecycle.
- Treating workload containers as managed session containers or `MoonMind.AgentRun` instances.
