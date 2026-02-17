# Research: Generic Task Container Runner

## Decision 1: Represent container execution in canonical task payload as `task.container`

- **Decision**: Add a new optional `task.container` object under existing task schema (`extra="allow"` already supports additive fields).
- **Rationale**: This keeps payloads backward compatible while allowing image/command/workdir/env/resource options to flow through existing canonical task handling.
- **Alternatives considered**:
  - New top-level job type (`container_exec`): rejected because it duplicates task lifecycle logic and fragments queue behavior.
  - Runtime-specific fields (`task.unreal`, `task.unity`, etc.): rejected because it does not meet arbitrary repo/task toolchain switching needs.

## Decision 2: Execute container tasks inside `CodexWorker._run_execute_stage`

- **Decision**: Branch inside execute stage when `task.container.enabled=true`, run a container-specific command path, then return the same `WorkerExecutionResult` type.
- **Rationale**: Reuses current prepare/publish lifecycle and artifact upload/event emission plumbing without introducing a second worker daemon.
- **Alternatives considered**:
  - New standalone container worker service: rejected for this scope due migration overhead and duplicate queue semantics.

## Decision 3: Use deterministic `docker run` wrapper with payload-driven image/command

- **Decision**: Build a standardized wrapper (`name`, labels, mounts, env metadata) while allowing arbitrary `image` and `command` values from payload.
- **Rationale**: Meets arbitrary execution requirement while preserving operational observability and artifact contracts.
- **Alternatives considered**:
  - Raw pass-through shell command from payload: rejected due unsafe parsing and brittle quoting behavior.

## Decision 4: Timeout handling and cleanup

- **Decision**: Implement explicit timeout handling in worker for container execution; on timeout, attempt `docker stop <name>` and mark task failed with metadata/log artifact.
- **Rationale**: Matches existing queue lifecycle expectations and avoids hanging execution paths.
- **Alternatives considered**:
  - Rely only on external orchestration timeout: rejected because worker must own task terminal status and cleanup best effort.

## Decision 5: Compose/runtime wiring

- **Decision**: Add worker-facing `DOCKER_HOST` default and include `docker` capability in default worker capability list used for container-enabled pools.
- **Rationale**: Aligns worker runtime configuration with docker-proxy architecture and capability routing.
- **Alternatives considered**:
  - Keep compose unchanged and require manual env setup only: rejected because docs target an operationally ready baseline.
