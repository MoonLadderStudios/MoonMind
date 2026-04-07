# Feature Specification: codex-managed-session-plane-phase4

**Feature Branch**: `131-codex-managed-session-plane-phase4`
**Created**: 2026-04-06
**Status**: Draft
**Input**: User description: "Implement Phase 4 of the Codex Managed Session Plane MVP plan using test-driven development."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Launch A Transitional Codex Session Container (Priority: P1)

The agent-runtime fleet needs to launch one task-scoped Codex session container through Docker, using the existing MoonMind image as a transitional session image, so the new path is container-first instead of worker-local.

**Why this priority**: This is the architectural guardrail for the rest of the managed-session plane. If launch still falls back to a worker-local Codex process, the new path is incorrect even if later layers are added.

**Independent Test**: Invoke `agent_runtime.launch_session` with a Codex managed-session request and confirm the controller launches a distinct Docker container, records the container identity, and returns a typed managed-session handle without using `ManagedRuntimeLauncher`.

**Acceptance Scenarios**:

1. **Given** a launch request for a managed Codex session, **When** `agent_runtime.launch_session` runs, **Then** MoonMind launches a separate Docker container from the provided image reference and returns a typed session handle with distinct container identity.
2. **Given** the session launch uses the current MoonMind image, **When** the controller validates the request, **Then** it treats the image as a session-container image rather than as another worker process.
3. **Given** the worker has no session-controller implementation, **When** the activity is invoked, **Then** it fails fast instead of attempting the worker-local managed-runtime launcher path.

---

### User Story 2 - Control Codex Through The Session Container Boundary (Priority: P1)

The session controller needs to execute Codex session actions inside the launched container, using the Codex app-server protocol from within the container, so MoonMind sends control actions to the container rather than spawning Codex locally in the worker.

**Why this priority**: Phase 4 exists to prove the container boundary and control transport before a dedicated Codex image is built.

**Independent Test**: Launch a session container, send a simple turn through the controller, verify the turn completes through in-container Codex app-server control, then clear and terminate the session.

**Acceptance Scenarios**:

1. **Given** a launched session container, **When** `send_turn` is invoked, **Then** the controller executes the request inside the session container and returns a typed turn response without launching Codex in the worker process.
2. **Given** a launched session container, **When** `clear_session` is invoked, **Then** the controller preserves the session identity, advances the epoch, rotates the logical thread boundary, and keeps the container alive.
3. **Given** a launched session container, **When** `terminate_session` is invoked, **Then** the controller stops and removes the session container and returns a terminal typed handle.

---

### User Story 3 - Bootstrap The Real Controller In The Agent Runtime Fleet (Priority: P2)

The Temporal agent-runtime worker needs to construct and inject the concrete managed-session controller so the session activities use the Docker-backed path by default.

**Why this priority**: The launch/controller implementation is not useful if the worker never wires it into `TemporalAgentRuntimeActivities`.

**Independent Test**: Build runtime activities for the `agent_runtime` fleet and assert the concrete session controller is injected alongside the existing run store, supervisor, and launcher dependencies.

**Acceptance Scenarios**:

1. **Given** the agent-runtime worker starts, **When** `_build_agent_runtime_deps()` runs, **Then** it constructs the concrete managed-session controller with Docker command execution support.
2. **Given** runtime activity handlers are built, **When** `TemporalAgentRuntimeActivities` is instantiated, **Then** the injected `session_controller` is the concrete Docker-backed implementation.
3. **Given** session activity methods are called through the worker, **When** control flows execute, **Then** no code path routes through `ManagedRuntimeLauncher.launch()`.

### Edge Cases

- Docker is unavailable or the launch command fails before the session container becomes ready.
- The session container starts but does not satisfy the readiness contract before timeout.
- The logical MoonMind `threadId` differs from the underlying Codex app-server thread identifier and must remain stable at the MoonMind boundary.
- A terminate request arrives after the container has already exited or been removed.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: MoonMind MUST implement a concrete Docker-backed managed-session controller for Codex managed sessions.
- **FR-002**: `agent_runtime.launch_session` MUST launch a separate Docker container from the `imageRef` provided in the typed request.
- **FR-003**: The Phase 4 launcher MUST treat the existing MoonMind image as a transitional session image only; the orchestration path MUST remain image-agnostic.
- **FR-004**: The concrete controller MUST execute session control actions against the launched container boundary and MUST NOT launch Codex locally in the worker.
- **FR-005**: The container startup contract MUST validate and use the typed workspace mount paths: repo workspace, session workspace, artifact spool path, and Codex home path.
- **FR-006**: The container MUST expose a readiness contract that the launcher can poll before reporting the session as ready.
- **FR-007**: The container-side session control implementation MUST use `codex app-server` protocol semantics internally rather than `codex exec` as the primary turn transport.
- **FR-008**: Session activity control responses MUST preserve the typed managed-session contracts already defined in Phase 3.
- **FR-009**: `clear_session` MUST preserve `sessionId` and `containerId`, advance `sessionEpoch`, and rotate the MoonMind logical `threadId`.
- **FR-010**: The agent-runtime worker bootstrap MUST inject the concrete session controller into `TemporalAgentRuntimeActivities`.

### Key Entities

- **Transitional Session Container**: A task-scoped Docker container launched from the current MoonMind image but treated as a dedicated Codex session runtime.
- **Docker Managed Session Controller**: The worker-side implementation of the managed-session control boundary that launches, inspects, controls, and terminates session containers.
- **Container Session Runtime**: The in-container entrypoint that validates mounts, manages readiness, and translates MoonMind control actions into Codex app-server requests.
- **Logical Thread Mapping**: The MoonMind-owned mapping from logical `threadId` values to vendor-native Codex app-server thread identifiers.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A focused test can launch a managed session through the concrete controller and verify a distinct Docker container identity is returned.
- **SC-002**: A focused test can send a simple turn through the launched container and receive a typed session turn response without using a worker-local Codex subprocess path.
- **SC-003**: A focused test can clear and terminate the launched session while preserving the typed managed-session state rules.
- **SC-004**: Worker bootstrap tests prove the agent-runtime fleet injects the concrete managed-session controller by default.
