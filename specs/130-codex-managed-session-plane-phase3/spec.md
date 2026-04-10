# Spec: Codex Managed Session Plane Phase 3

## Problem

MoonMind has the Phase 1 Codex managed-session contract, but `mm.activity.agent_runtime` does not yet expose a typed session-oriented activity surface for launching and controlling a remote task-scoped session container. Without that surface, later workflow and adapter work risks falling back to worker-local Codex execution semantics.

## Requirements

### Functional Requirements

- **FR-001**: `mm.activity.agent_runtime` MUST expose typed session activity contracts for `launch_session`, `session_status`, `send_turn`, `steer_turn`, `interrupt_turn`, `clear_session`, `terminate_session`, `fetch_session_summary`, and `publish_session_artifacts`.
- **FR-002**: The session activity request and response models MUST be Codex-only, Docker-only, and explicit about remote-container control semantics.
- **FR-003**: Session activities MUST delegate through a dedicated remote session-control boundary rather than using the existing worker-local managed runtime launcher/supervisor path.
- **FR-004**: Temporal activity bindings and the activity catalog MUST register the new `agent_runtime.*` session activity types on the `mm.activity.agent_runtime` fleet.

### Non-Functional Requirements

- **NF-001**: Session activity contracts MUST be encoded as executable Pydantic models.
- **NF-002**: New activity signatures MUST include workflow-boundary tests proving Temporal can serialize the typed request/response contracts.
- **NF-003**: Existing managed runtime activity tests MUST continue to pass.

## Acceptance Criteria

1. **Given** a session launch payload using the Codex managed-session contract, **When** `agent_runtime.launch_session` is invoked, **Then** the request validates as a remote-container launch request and returns a typed managed-session handle.
2. **Given** a `TemporalAgentRuntimeActivities` instance without a session controller, **When** any session activity is invoked, **Then** it fails fast instead of attempting a local managed-run fallback.
3. **Given** the `agent_runtime` fleet bindings are built, **When** the catalog is inspected, **Then** the session activity types are present alongside the existing managed-runtime activities.

## Scope

- **In scope**: typed managed-session schema models, Temporal activity catalog entries, `TemporalAgentRuntimeActivities` session methods, and boundary/unit tests.
- **Out of scope**: Docker launcher implementation, `MoonMind.AgentSession`, session reconciliation, session adapter implementation, session projection API, and UI work.
