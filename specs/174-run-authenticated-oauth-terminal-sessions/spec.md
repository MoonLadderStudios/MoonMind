# Feature Specification: Run Authenticated OAuth Terminal Sessions

**Feature Branch**: `174-run-authenticated-oauth-terminal-sessions`  
**Created**: 2026-04-15  
**Status**: Draft  
**Input**: MM-333: Run authenticated OAuth terminal sessions. As an authorized operator, I can start a browser-based OAuth terminal session that runs a provider login command inside a short-lived auth runner, streams PTY I/O through MoonMind, verifies the resulting credentials, and closes deterministically on success, cancellation, expiry, or failure. Source: `docs/ManagedAgents/OAuthTerminal.md` sections 5, 5.1, 5.2, 5.3, and 9. Coverage IDs: DESIGN-REQ-001, DESIGN-REQ-010, DESIGN-REQ-011, DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-018, DESIGN-REQ-019, DESIGN-REQ-022. Breakdown Story ID: STORY-002. Breakdown JSON: `docs/tmp/story-breakdowns/mm-318-breakdown-docs-managedagents-oauthtermina-74125184/stories.json`.

## User Story 1 - Browser OAuth Terminal Session

As an authorized operator, I can open an OAuth terminal session for a provider profile, interact with the provider login command through MoonMind, and have the session close after a terminal outcome.

### Acceptance Scenarios

1. **Given** an authorized operator starts a Codex OAuth session, **when** the workflow starts the auth runner, **then** MoonMind persists terminal session metadata, exposes a MoonMind-owned terminal transport, and leaves credential material only in the durable auth volume.
2. **Given** the same operator attaches to the terminal WebSocket, **when** terminal input, resize, or heartbeat frames arrive, **then** MoonMind proxies only to the OAuth auth runner for that session and records connection timestamps without exposing generic Docker exec.
3. **Given** the operator finalizes, cancels, the session expires, or verification fails, **when** the terminal outcome is reached, **then** the auth runner is stopped and the session reaches exactly one terminal state: `succeeded`, `cancelled`, `expired`, or `failed`.

## Requirements

- **FR-001**: OAuth session creation MUST return transport-neutral terminal refs required by Mission Control to attach to the MoonMind terminal bridge. Maps to DESIGN-REQ-011 and DESIGN-REQ-013.
- **FR-002**: The auth runner start activity MUST persist the runner container, terminal session ID, bridge ID, transport value, and expiry metadata on the OAuth session row. Maps to DESIGN-REQ-012 and DESIGN-REQ-013.
- **FR-003**: Terminal WebSocket attachment MUST require an authenticated owner, an active unexpired session, and a known auth runner container. Maps to DESIGN-REQ-018 and DESIGN-REQ-019.
- **FR-004**: Terminal WebSocket execution MUST run the provider registry bootstrap command inside the session auth runner PTY rather than exposing arbitrary Docker exec. Maps to DESIGN-REQ-011, DESIGN-REQ-012, and DESIGN-REQ-022.
- **FR-005**: The bridge MUST handle terminal data, resize frames, heartbeat frames, disconnects, and close metadata without returning credential files, token values, environment dumps, or auth-volume listings. Maps to DESIGN-REQ-010, DESIGN-REQ-013, and DESIGN-REQ-019.
- **FR-006**: Finalize, cancel, expiry, and verification failure paths MUST stop the short-lived auth runner deterministically. Maps to DESIGN-REQ-012 and DESIGN-REQ-022.

## Key Entities

- **OAuth Session**: Existing `managed_agent_oauth_sessions` row that stores owner, transport-neutral lifecycle state, terminal refs, runner container metadata, expiry, and profile registration metadata.
- **Auth Runner**: Short-lived container mounted to the provider enrollment auth volume and scoped to one OAuth session.
- **Terminal Bridge**: Authenticated WebSocket endpoint that proxies PTY I/O to the OAuth auth runner only.

## Success Criteria

- **SC-001**: Unit tests verify OAuth session creation exposes terminal refs and stores runner metadata without credential contents.
- **SC-002**: Unit tests verify the terminal bridge rejects unauthorized, inactive, and expired attaches and resolves provider bootstrap commands for authorized active attaches.
- **SC-003**: Unit tests verify API terminal outcomes stop the auth runner on successful finalize and failure finalize paths.
