# Feature Specification: Scalable Codex Worker

**Feature Branch**: `007-scalable-codex-worker`  
**Created**: 2025-11-27  
**Status**: Draft  
**Input**: User description: "create a spec based on docs/CodexCliWorkers.md covering the functionality that has not been implemented yet, such as a shared volume for authentication and a single codex worker service that can be scaled. soec number should start with 007"

## Clarifications

### Session 2025-11-27
- Q: How should the system handle volume access when multiple worker replicas are deployed? → A: **Share one volume** across all replicas (Simplest; relies on CLI file locking).
- Q: How should the worker behave if the auth volume is unauthenticated (missing credentials) at startup? → A: **Crash/Exit container immediately** (Standard for dependencies; forces attention).
- Q: Should the startup pre-flight check verify token validity (network call) or just file existence? → A: **Validation Check (Ping API)** (Slower startup, but guarantees readiness).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Deploy Dedicated Codex Worker (Priority: P1)

As a System Operator, I want to deploy a dedicated worker service that handles only Codex-related tasks, so that resource-intensive or specialized Codex operations do not block general system tasks and can be scaled independently.

**Why this priority**: Critical for system stability and performance isolation as defined in the architecture.

**Independent Test**: Can be tested by inspecting the running services and verifying a worker is listening specifically on the `codex` queue and not processing default queue tasks (or vice versa).

**Acceptance Scenarios**:

1. **Given** a configured MoonMind environment, **When** the system starts, **Then** a `celery_codex_worker` service is running.
2. **Given** the `celery_codex_worker` is running, **When** inspected, **Then** it is listening on the `codex` queue.
3. **Given** a task submitted to the `codex` queue, **When** the worker is active, **Then** it processes the task.

---

### User Story 2 - Persistent Authentication (Priority: P1)

As a System Operator, I want to authenticate the Codex CLI once using a persistent volume, so that the worker can execute authenticated commands indefinitely without requiring manual login for each container restart.

**Why this priority**: Essential for automation; without it, the worker would fail or hang on auth prompts after every restart.

**Independent Test**: Can be tested by manually authenticating the volume, restarting the worker container, and verifying it can still make authenticated API calls.

**Acceptance Scenarios**:

1. **Given** a fresh environment, **When** I create the `codex_auth_volume` and perform the login flow in a setup container, **Then** the credentials are saved to the volume.
2. **Given** an authenticated `codex_auth_volume`, **When** the `celery_codex_worker` starts with this volume mounted, **Then** it passes the pre-flight auth check.
3. **Given** a running worker with the auth volume, **When** a task invokes the Codex CLI, **Then** the CLI executes successfully without prompting for login.

---

### User Story 3 - Non-interactive Execution (Priority: P2)

As a System Operator, I want the Codex worker to run with a configuration that strictly forbids interactive prompts, so that automation workflows never hang indefinitely waiting for user input.

**Why this priority**: Prevents "zombie" tasks that block the queue and require manual intervention.

**Independent Test**: Can be tested by triggering a CLI command that would normally ask for confirmation (e.g., applying a large change) and ensuring it either proceeds (if policy allows) or fails fast, but never prompts.

**Acceptance Scenarios**:

1. **Given** the `celery_codex_worker` environment, **When** the Codex CLI configuration is inspected, **Then** `approval_policy` is set to `"never"`.
2. **Given** a task that requires approval, **When** it runs on the worker, **Then** it executes automatically (or fails if the operation is strictly blocked) without hanging.

### Edge Cases

- What happens if the `codex_auth_volume` is missing or empty? (System MUST crash immediately to signal configuration error).
- What happens if the OAuth token expires? (The pre-flight check or the task should fail gracefully, alerting the operator).
- What happens if multiple workers try to use the same volume? (Multiple workers will concurrently mount and read from the same `codex_auth_volume`, relying on underlying CLI/OS file locking for safety).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST define a `codex` Celery queue in the worker configuration and routing rules.
- **FR-002**: The system MUST provision a persistent Docker volume named `codex_auth_volume` (or similar) for storing Codex credentials.
- **FR-003**: The `celery_codex_worker` service MUST mount the `codex_auth_volume` to the `CODEX_HOME` directory (e.g., `/home/app/.codex`) inside the container.
- **FR-004**: The `celery_codex_worker` service MUST use a container image that includes the Codex CLI and Spec Kit CLI.
- **FR-005**: The worker environment MUST include a managed `.codex/config.toml` with `approval_policy = "never"` to ensure non-interactive mode.
- **FR-006**: The worker startup process MUST perform a pre-flight check by attempting a minimal Codex API call (e.g., `codex whoami` or equivalent) to verify that valid authentication credentials exist and are active. If this check fails, the worker MUST exit with a non-zero status code.
- **FR-007**: The system MUST allow the `celery_codex_worker` service to be scaled (e.g., via Docker Compose `scale` or Kubernetes replicas), though a single instance is the default. All replicas MUST share the same `codex_auth_volume`.

### Key Entities *(include if feature involves data)*

- **Codex Worker Service**: A specialized Celery worker instance dedicated to the `codex` queue.
- **Codex Auth Volume**: A persistent storage volume holding OAuth tokens and CLI configuration.
- **Codex Queue**: A logical job queue for routing Codex-specific tasks.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The `celery_codex_worker` successfully starts and connects *only* to the `codex` queue (verified by logs).
- **SC-002**: Codex CLI commands executed by the worker succeed without user interaction 100% of the time when valid credentials are present.
- **SC-003**: Authentication credentials persist across container restarts/re-deployments (tested by restarting the service and running a command).
- **SC-004**: The worker fails to start (or enters an unhealthy state) within 30 seconds if the auth volume is unauthenticated or the token is invalid, logging a clear error.
