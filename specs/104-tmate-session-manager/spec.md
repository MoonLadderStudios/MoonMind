# Feature Specification: TmateSessionManager — Shared Abstraction

**Feature Branch**: `104-tmate-session-manager`
**Created**: 2026-03-24
**Status**: Draft
**Input**: User description: "Implement Phase 1 of docs/ManagedAgents/UniversalTmateOAuth.md: Extract the shared TmateSessionManager abstraction from TmateSessionArchitecture.md, refactor both OAuth session activities and ManagedRuntimeLauncher to use it, and wire self-hosted server configuration — unifying the tmate lifecycle strategy across both use cases."

## Source Document Requirements

The following requirements are extracted from the source contract documents:

- [docs/ManagedAgents/UniversalTmateOAuth.md](../../docs/ManagedAgents/UniversalTmateOAuth.md)
- [docs/Temporal/TmateSessionArchitecture.md](../../docs/Temporal/TmateSessionArchitecture.md)

| ID | Source | Requirement |
|---|---|---|
| DOC-REQ-001 | TmateSessionArchitecture.md §4.1 | `TmateSessionManager` MUST be located at `moonmind/workflows/temporal/runtime/tmate_session.py` |
| DOC-REQ-002 | TmateSessionArchitecture.md §4.2 | The manager MUST provide a `TmateEndpoints` dataclass with fields: `session_name`, `socket_path`, `attach_ro`, `attach_rw`, `web_ro`, `web_rw` |
| DOC-REQ-003 | TmateSessionArchitecture.md §4.2 | The manager MUST support `is_available()`, `start()`, `teardown()`, `endpoints` property, and `exit_code_path` property |
| DOC-REQ-004 | TmateSessionArchitecture.md §4.2 | `start()` MUST create socket dir and config file, launch tmate via subprocess, wait for readiness via `tmate wait tmate-ready`, and extract all four endpoint types |
| DOC-REQ-005 | TmateSessionArchitecture.md §4.3 | The manager MUST support `TmateServerConfig` for self-hosted server (host, port, rsa fingerprint, ed25519 fingerprint) sourced from environment variables |
| DOC-REQ-006 | TmateSessionArchitecture.md §4.3 | When `TmateServerConfig` is provided, the manager MUST write `set-option` directives into a per-session config file |
| DOC-REQ-007 | TmateSessionArchitecture.md §4.4 | `ManagedRuntimeLauncher` MUST be refactored from inline tmate logic to delegate to `TmateSessionManager` |
| DOC-REQ-008 | TmateSessionArchitecture.md §4.4 / UniversalTmateOAuth.md §6.2.F | `oauth_session_activities.py` MUST be refactored from Docker-exec polling to use `TmateSessionManager` or its endpoint extraction |
| DOC-REQ-009 | TmateSessionArchitecture.md §3 | Session lifecycle states MUST follow: DISABLED → STARTING → READY → (ENDED / REVOKED / ERROR) |
| DOC-REQ-010 | TmateSessionArchitecture.md §6.1 | `teardown()` MUST remove socket, config, and exit-code files |
| DOC-REQ-011 | UniversalTmateOAuth.md §6.2.F | Both runtime wrapping and OAuth sessions MUST share the same `TmateSessionManager` abstraction — no split strategy |
| DOC-REQ-012 | TmateSessionArchitecture.md §4.2 | `start()` MUST accept optional `command`, `env`, `cwd`, `exit_code_capture`, and `timeout_seconds` parameters |
| DOC-REQ-013 | UniversalTmateOAuth.md §14.6 / TmateSessionArchitecture.md §7 | Self-hosted server config MUST be sourced from `MOONMIND_TMATE_SERVER_HOST`, `MOONMIND_TMATE_SERVER_PORT`, `MOONMIND_TMATE_SERVER_RSA_FINGERPRINT`, `MOONMIND_TMATE_SERVER_ED25519_FINGERPRINT` |

## User Scenarios & Testing

### User Story 1 — Developer uses TmateSessionManager to start and manage a tmate session (Priority: P1)

A developer working on the ManagedRuntimeLauncher or OAuth session activities calls `TmateSessionManager` to create a tmate session. The manager handles socket creation, config file generation, subprocess launch, readiness detection, and endpoint extraction — replacing the inline logic that was previously duplicated across two code paths.

**Why this priority**: This is the foundational abstraction. Without it, the two tmate consumers continue to drift apart, accumulating divergent bugs and inconsistent behavior.

**Independent Test**: Can be tested by instantiating `TmateSessionManager`, calling `start()` with a mock command, and verifying endpoint extraction and teardown.

**Acceptance Scenarios**:

1. **Given** tmate binary is on PATH, **When** `TmateSessionManager.start(command=["echo", "hello"])` is called, **Then** the manager creates a per-session socket, waits for readiness, and returns a `TmateEndpoints` with all available endpoint fields populated.
2. **Given** tmate binary is NOT on PATH, **When** `TmateSessionManager.is_available()` is called, **Then** it returns `False`.
3. **Given** a running tmate session, **When** `teardown()` is called, **Then** the session process is terminated and all socket/config/exit-code files are cleaned up.

---

### User Story 2 — ManagedRuntimeLauncher uses TmateSessionManager instead of inline logic (Priority: P1)

The existing `ManagedRuntimeLauncher.launch()` method (~100 lines of inline tmate logic) is refactored to delegate tmate lifecycle management to `TmateSessionManager`. The launcher creates a manager instance, calls `start()` with the agent command, and passes the returned endpoints to the supervisor.

**Why this priority**: This eliminates the inline tmate logic from the launcher, which is the larger and more complex of the two consumers.

**Independent Test**: Can be tested by running the launcher in test mode and verifying that it produces the same endpoint structure as before.

**Acceptance Scenarios**:

1. **Given** a managed agent run with tmate available, **When** the launcher starts the run, **Then** it delegates to `TmateSessionManager.start()` and receives endpoints for the supervisor.
2. **Given** a managed agent run with tmate NOT available, **When** the launcher starts the run, **Then** it falls back to headless execution (same as current behavior).
3. **Given** a managed agent run completes, **When** the supervisor teardown runs, **Then** `TmateSessionManager.teardown()` is called in the finally block.

---

### User Story 3 — OAuth session activities use TmateSessionManager (Priority: P2)

The OAuth session `start_auth_runner` activity refactors its Docker-exec tmate polling loop to use `TmateSessionManager` endpoint extraction (either the manager runs inside the container entrypoint and writes endpoints to a well-known file, or the activity uses the manager's API for extraction).

**Why this priority**: This completes the strategy unification. The OAuth session is a simpler consumer, using tmate inside a Docker container rather than directly on the worker.

**Independent Test**: Can be tested by mocking Docker exec calls and verifying the activity correctly extracts tmate URLs using the shared manager abstraction.

**Acceptance Scenarios**:

1. **Given** an OAuth session auth container with tmate running, **When** the `start_auth_runner` activity polls for tmate readiness, **Then** it uses `TmateSessionManager` endpoint extraction patterns instead of hardcoded Docker-exec polling.
2. **Given** tmate fails to become ready within the timeout, **When** the activity times out, **Then** it reports the failure with appropriate diagnostics (same current behavior).

---

### User Story 4 — Self-hosted tmate server configuration (Priority: P2)

An operator configures `MOONMIND_TMATE_SERVER_HOST` and related environment variables. When `TmateSessionManager.start()` is called, the manager writes `set-option` directives into the per-session config file pointing sessions to the private relay server instead of `tmate.io`.

**Why this priority**: Self-hosted server support is a security and operational requirement for production deployments, but functional correctness works without it (falls back to public tmate.io).

**Independent Test**: Can be tested by setting environment variables and verifying the generated config file contains the expected `set-option` directives.

**Acceptance Scenarios**:

1. **Given** `MOONMIND_TMATE_SERVER_HOST` is set, **When** `TmateSessionManager.start()` is called, **Then** the per-session config file contains `set-option -g tmate-server-host` and related directives.
2. **Given** no self-hosted config is set, **When** `TmateSessionManager.start()` is called, **Then** sessions connect to the default `tmate.io` relay.

---

### Edge Cases

- What happens when `teardown()` is called on an already-ended session? → No-op behavior, no exceptions.
- What happens when the tmate binary crashes during session startup? → `start()` raises a descriptive error after the timeout, session transitions to ERROR state.
- What happens if socket directory creation fails due to permissions? → `start()` raises immediately with a clear error message.
- What happens if two TmateSessionManager instances use the same session name? → Socket path collision error; the manager should use unique socket paths per session name.
- What happens if `teardown()` fails to kill the tmate process? → Best-effort cleanup; logs warning but does not raise.

## Requirements

### Functional Requirements

- **FR-001**: System MUST provide a `TmateSessionManager` class at `moonmind/workflows/temporal/runtime/tmate_session.py` that encapsulates the full tmate session lifecycle (DOC-REQ-001)
- **FR-002**: System MUST provide a `TmateEndpoints` dataclass with fields: `session_name`, `socket_path`, `attach_ro`, `attach_rw`, `web_ro`, `web_rw` (DOC-REQ-002)
- **FR-003**: System MUST provide a `TmateServerConfig` dataclass with fields: `host`, `port`, `rsa_fingerprint`, `ed25519_fingerprint` (DOC-REQ-005)
- **FR-004**: `TmateSessionManager.is_available()` MUST return True only when the tmate binary is on PATH (DOC-REQ-003)
- **FR-005**: `TmateSessionManager.start()` MUST create a per-session socket directory and config file, launch tmate as a subprocess, wait for readiness, and extract all four endpoint types (DOC-REQ-004, DOC-REQ-012)
- **FR-006**: `TmateSessionManager.start()` MUST accept optional parameters: `command`, `env`, `cwd`, `exit_code_capture`, `timeout_seconds` (DOC-REQ-012)
- **FR-007**: `TmateSessionManager.teardown()` MUST kill the tmate session and clean up socket, config, and exit-code files (DOC-REQ-010)
- **FR-008**: When `TmateServerConfig` is provided with a host, the manager MUST write server `set-option` directives into the per-session config file (DOC-REQ-005, DOC-REQ-006)
- **FR-009**: `TmateServerConfig` values MUST be sourced from environment variables `MOONMIND_TMATE_SERVER_HOST`, `MOONMIND_TMATE_SERVER_PORT`, `MOONMIND_TMATE_SERVER_RSA_FINGERPRINT`, `MOONMIND_TMATE_SERVER_ED25519_FINGERPRINT` (DOC-REQ-013)
- **FR-010**: `ManagedRuntimeLauncher.launch()` MUST delegate tmate lifecycle management to `TmateSessionManager` instead of inline logic (DOC-REQ-007)
- **FR-011**: `oauth_session_activities.start_auth_runner()` MUST use `TmateSessionManager` patterns for endpoint extraction instead of hardcoded Docker-exec polling (DOC-REQ-008)
- **FR-012**: Both runtime wrapping and OAuth sessions MUST use the same `TmateSessionManager` abstraction — no split tmate strategy (DOC-REQ-011)
- **FR-013**: Session state transitions MUST map to the lifecycle: DISABLED → STARTING → READY → (ENDED / REVOKED / ERROR) (DOC-REQ-009)
- **FR-014**: Existing unit tests for launcher.py and oauth_session_activities.py MUST continue to pass after refactoring
- **FR-015**: New unit tests MUST be added for `TmateSessionManager` covering start/teardown/config-generation/availability-check
- **FR-016**: The `endpoints` property MUST return the last extracted `TmateEndpoints`, or `None` if the session has not been started
- **FR-017**: The `exit_code_path` property MUST return the path to the exit code capture file when `exit_code_capture=True`

### Key Entities

- **TmateSessionManager**: Manages the lifecycle of a single tmate session: start, readiness, endpoint extraction, teardown.
- **TmateEndpoints**: Value object holding all extracted tmate endpoint strings (RO/RW SSH and web URLs).
- **TmateServerConfig**: Configuration for connecting to a self-hosted tmate relay server.

## Success Criteria

### Measurable Outcomes

- **SC-001**: A single `TmateSessionManager` class handles tmate lifecycle for both runtime wrapping and OAuth sessions — zero inline tmate logic remains in `launcher.py` or `oauth_session_activities.py`
- **SC-002**: All existing managed agent run tests pass without modification (no behavioral regression)
- **SC-003**: At least 10 new unit tests covering `TmateSessionManager` start, teardown, config generation, availability check, and endpoint extraction
- **SC-004**: Self-hosted server configuration generates correct tmate config file directives when environment variables are set
- **SC-005**: The refactored launcher produces identical endpoint structures for the supervisor as the previous inline implementation

## Assumptions

- The tmate binary is already installed in the worker Docker image (confirmed present in Dockerfile)
- The existing `launcher.py` inline tmate logic is functionally correct and serves as the behavioral reference for the new abstraction
- The OAuth session container entrypoint pattern (writing endpoints to a well-known file or using docker exec for extraction) may evolve — the initial implementation preserves the existing Docker-exec approach but routes it through `TmateSessionManager` patterns
- `openssh-client` and `ca-certificates` dependencies are already present in the worker image
