# Feature Specification: Generic Task Container Runner

**Feature Branch**: `020-generic-container-runner`  
**Created**: 2026-02-17  
**Status**: Draft  
**Input**: User description: "Implement the system described in docs/DockerOutOfDocker.md. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Run Arbitrary Task Containers (Priority: P1)

As an operator, I can submit a queue task that specifies a container image and command, and the worker executes that container against the checked-out repository workspace.

**Why this priority**: This is the core behavior required to support Unity, .NET, Unreal, and other project-specific toolchains without baking them into workers.

**Independent Test**: Submit a task with `task.container.enabled=true`, a public image, and a command; verify worker launches the container, exits with success/failure based on command exit code, and records artifacts.

**Acceptance Scenarios**:

1. **Given** a task includes `task.container` with `enabled=true`, `image`, and `command`, **When** a Docker-capable worker executes the task, **Then** the worker launches an ephemeral container using the provided image and command with mounted job workspace.
2. **Given** the container command exits with non-zero status, **When** execution completes, **Then** the task is marked failed and logs/artifacts are still uploaded.

---

### User Story 2 - Switch Toolchains Per Repository and Per Task (Priority: P2)

As a platform user, I can run different repositories (or different tasks in one repository) using different container images and commands without changing worker code.

**Why this priority**: This enables repo-by-repo and task-by-task switching for heterogeneous stacks.

**Independent Test**: Execute two tasks targeting different repositories (or two tasks on one repo) where one uses a .NET image and another uses a Unity/Unreal image; verify both are executed through the same generic container path.

**Acceptance Scenarios**:

1. **Given** tasks specify different `task.container.image` values, **When** workers process those tasks, **Then** each task runs using its requested image.
2. **Given** tasks specify different `task.container.command` arrays, **When** workers process those tasks, **Then** each task executes its requested command without worker-side toolchain branching.

---

### User Story 3 - Preserve Queue Lifecycle Guarantees for Container Runs (Priority: P3)

As an operator, I can rely on the same queue lifecycle guarantees for containerized tasks (events, timeout handling, cleanup, and artifact upload) as existing task execution.

**Why this priority**: Operational safety and observability are required for production rollout.

**Independent Test**: Run container tasks that succeed, fail, and time out; verify emitted events, artifact paths, and cleanup behavior are consistent and inspectable.

**Acceptance Scenarios**:

1. **Given** a container task starts execution, **When** the run begins and ends, **Then** the worker emits start/finish events with image/command summary and exit metadata.
2. **Given** a task timeout is reached, **When** the worker enforces timeout, **Then** the container is stopped and the task result is marked failed with timeout details.

### Edge Cases

- Container image pull fails because image is unavailable or registry access fails.
- `task.container.command` is empty or malformed.
- Requested container workdir does not exist.
- Container exits before writing expected artifacts.
- Worker process crashes mid-run and must not leave orphaned containers in normal retry flow.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Task payloads MUST support an optional `task.container` object with required fields `enabled`, `image`, and `command`.
- **FR-002**: `task.container.command` MUST support arbitrary command execution (list form) and MUST be passed to `docker run` without toolchain-specific branching in worker code.
- **FR-003**: When `task.container.enabled=true`, workers MUST execute the task in an ephemeral Docker container instead of local CLI execution path.
- **FR-004**: Worker container execution MUST mount job workspace paths so repository files and artifact output directories are available inside the runner container.
- **FR-005**: Workers MUST support task-level image selection so different repositories/tasks can use different runner images without worker redeploys.
- **FR-006**: Workers MUST enforce `docker` capability matching for container-enabled tasks.
- **FR-007**: Worker execution MUST support container timeout and terminate container execution when timeout is exceeded.
- **FR-008**: Worker execution MUST persist logs and metadata artifacts for both successful and failed container runs.
- **FR-009**: Worker execution MUST emit queue events for container execution start and finish including image, command summary, duration, and exit status.
- **FR-010**: System configuration MUST support Docker API access through `DOCKER_HOST` + `docker-proxy` for workers that run container tasks.
- **FR-011**: Automated tests MUST validate container payload normalization/validation, execution command construction, and success/failure lifecycle behavior.

### Key Entities *(include if feature involves data)*

- **ContainerTaskSpec**: Canonical task payload subsection defining container execution inputs (`enabled`, `image`, `command`, `env`, `workdir`, `timeoutSeconds`, `resources`, `artifactsSubdir`, `pull`, `cacheVolumes`).
- **ContainerExecutionRequest**: Worker-internal normalized execution request built from canonical payload and workspace context.
- **ContainerExecutionResult**: Outcome model containing image, command summary, start/end timestamps, exit code, duration, and timeout flag.
- **ContainerArtifactLayout**: Standardized artifact output structure rooted at `${job_root}/artifacts/<artifactsSubdir>`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of valid `task.container.enabled=true` tasks execute through the container runtime path rather than local CLI path.
- **SC-002**: At least two distinct images (for example `.NET` and `Unity/Unreal`) can be executed by the same worker binary in one test run.
- **SC-003**: 100% of failed container tasks include captured execution logs and metadata artifacts describing failure reason.
- **SC-004**: Unit/integration test suite additions for this feature pass in CI using the project-standard test command.
