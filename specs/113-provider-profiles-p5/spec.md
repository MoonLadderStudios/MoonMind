# Feature Specification: Provider Profiles Phase 5 - OAuth Terminal

**Feature Branch**: `113-provider-profiles-p5`  
**Created**: 2026-03-28  
**Status**: Draft  
**Input**: User description: "Fully implement Phase 5 of docs/tmp/005-ProviderProfilesPlan.md: OAuth Terminal and OAuth-Backed Provider Profile Completion"

## Source Document Requirements

- **DOC-REQ-001**: Replace the dead OAuth launch path with the PTY/WebSocket bridge startup activity path. Implement short-lived auth container startup compatible with the mounted auth volume model, and teardown activity.
- **DOC-REQ-002**: Update the OAuth session workflow: replace `oauth_runner_ready` with `bridge_ready`, update status transitions to transport-neutral lifecycle, add signals for terminal connected/disconnected/finalize/cancel.
- **DOC-REQ-003**: Update the OAuth session persistence model to remove `oauth_web_url`, `oauth_ssh_url` and add `terminal_session_id`, `terminal_bridge_id`, `connected_at`, `disconnected_at`. Create `oauth_terminal_sessions` table if necessary.
- **DOC-REQ-004**: Update OAuth session finalization to create/update `ManagedAgentProviderProfile` with `credential_source=oauth_volume` and `runtime_materialization_mode=oauth_home`.
- **DOC-REQ-005**: Implement session-scoped terminal attach authorization, TTL enforcement, idle expiry, audit logging, and ensure clients never receive credentials directly.
- **DOC-REQ-006**: Finish provider-specific volume verification for Gemini, Claude, and Codex, ensuring registration only happens after verification succeeds.

## User Scenarios & Testing

### User Story 1 - Secure OAuth Terminal Login (Priority: P1)

Operators must be able to authenticate to provider CLI tools (like Anthropic, Gemini, Codex) using a fully generic browser-based PTY terminal, without leaking credentials.

**Why this priority**: Restoring the OAuth flow is the primary user-facing requirement of Phase 5. Without this, no profile using "OAuth Provider Profile" flow can be generated in the UI.

**Independent Test**: Execute an OAuth flow registration in the local test suite. Verify the PTY container starts, the DB marks it active, and an operator can type commands using pseudo-socket messages to log in securely. Upon completion, the OAuth session validates the volume and creates the Provider Profile record.

**Acceptance Scenarios**:

1. **Given** an operator requests OAuth for Claude, **When** the session starts, **Then** a PTY bridge container spins up and exposes a secure WebSocket transport.
2. **Given** a connected operator finishes logging in, **When** they click Finalize, **Then** the workflow verifies the auth volume and registers the permanent `ManagedAgentProviderProfile`.

## Requirements

### Functional Requirements

- **FR-001**: System MUST provide a PTY browser terminal. (Addresses DOC-REQ-001)
- **FR-002**: System MUST transition OAuth workflow states to terminal-connected/disconnected. (Addresses DOC-REQ-002)
- **FR-003**: System MUST persist terminal details in `oauth_terminal_sessions` removing legacy URL fields. (Addresses DOC-REQ-003)
- **FR-004**: System MUST emit `ManagedAgentProviderProfile` entries automatically upon completion. (Addresses DOC-REQ-004)
- **FR-005**: System MUST enforce attach authentication and idle session timeouts. (Addresses DOC-REQ-005)
- **FR-006**: System MUST verify the mounted container volume for valid CLI fingerprints before declaring success. (Addresses DOC-REQ-006)

### Key Entities

- **OAuthSession**: Transformed model avoiding hardcoded TMATE urls in favor of WebSocket details.
- **TerminalPTYBridge**: Transitory runner environment for doing browser CLI tasks securely.

## Success Criteria

### Measurable Outcomes

- **SC-001**: E2E browser test can successfully log into `claude` CLI and result in an active profile.
- **SC-002**: OAuth sessions idle-timeout correctly without hanging the Temporal server or worker.
