# Feature Specification: Docker-Out-of-Docker Workload Launcher

**Feature Branch**: `150-dood-workload-launcher`
**Created**: 2026-04-11
**Status**: Implemented
**Input**: Implement Phase 2 using test-driven development of the MoonMind Docker-out-of-Docker phased plan.

## User Scenarios & Testing

### User Story 1 - Launch a validated workload container (Priority: P1)

MoonMind operators need the existing Docker-capable `agent_runtime` worker fleet to launch one-shot workload containers from a validated Phase 1 `WorkloadRequest`, so specialized tools can run against `agent_workspaces` without giving Codex session containers direct Docker authority.

**Independent Test**: Unit tests build a validated request, run it through `DockerWorkloadLauncher` with mocked Docker processes, and assert deterministic Docker arguments, labels, mounts, environment, resource flags, captured streams, and cleanup.

**Acceptance Scenarios**:

1. **Given** a valid request and runner profile, **When** the launcher runs it, **Then** Docker receives deterministic `run` arguments with the workload container name, ownership labels, profile mounts, env overrides, network policy, resource flags, workdir, image, and command.
2. **Given** the workload completes, **When** cleanup policy removes containers on exit, **Then** the launcher calls `docker rm -f` and returns bounded `WorkloadResult` metadata.

### User Story 2 - Bound timeout and cleanup behavior (Priority: P1)

MoonMind engineers need timed-out workload containers to be stopped, killed, and removed, so routine failed workloads do not leave orphan containers behind.

**Independent Test**: A mocked never-ending Docker process times out and proves `docker stop`, `docker kill`, and `docker rm -f` are invoked.

### User Story 3 - Route workload execution to the Docker-capable fleet (Priority: P2)

MoonMind engineers need `workload.run` to be a separate control-plane activity on the current `agent_runtime` fleet, so Phase 2 does not overload managed-session verbs.

**Independent Test**: Activity catalog and worker-topology tests assert `workload.run` resolves to `mm.activity.agent_runtime` with the `docker_workload` capability.

## Requirements

- **FR-001**: MoonMind MUST add a `DockerWorkloadLauncher` that accepts a profile-validated workload request and executes Docker through the configured Docker CLI / `DOCKER_HOST` environment.
- **FR-002**: The launcher MUST construct deterministic container names and ownership labels from the Phase 1 contract.
- **FR-003**: The launcher MUST mount profile-declared workspace and cache volumes only, including `agent_workspaces` and curated cache volumes such as Unreal cache volumes when present in the profile.
- **FR-004**: The launcher MUST apply profile/request network, environment, workdir, resource, entrypoint, wrapper, image, and command settings without accepting free-form images or mounts from the request.
- **FR-005**: The launcher MUST capture bounded stdout/stderr metadata, exit code, start/completion time, duration, timeout reason, and selected profile/image metadata.
- **FR-006**: Timeout and cancellation paths MUST stop/kill containers and remove ephemeral containers according to cleanup policy.
- **FR-007**: A Docker cleanup utility MUST support stop, kill, rm, and orphan lookup by ownership labels.
- **FR-008**: Worker routing MUST expose a `docker_workload` capability on the existing `agent_runtime` fleet and bind `workload.run` separately from `agent_runtime.launch_session`.

## Success Criteria

- **SC-001**: Focused unit tests for launcher construction, timeout cleanup, orphan lookup, and worker routing pass.
- **SC-002**: `workload.run` is routable on `mm.activity.agent_runtime` and no session-plane activity verb is reused for workload launches.
- **SC-003**: The DooD remaining-work tracker marks Phase 2 complete while leaving Phases 3 through 7 pending.
