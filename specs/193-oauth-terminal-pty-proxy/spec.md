# Feature Specification: OAuth Terminal PTY Proxy

**Feature Branch**: `193-oauth-terminal-pty-proxy`  
**Created**: 2026-04-16  
**Status**: Draft  
**Input**:

```text
# MM-362 MoonSpec Orchestration Input

## Source

- Jira issue: MM-362
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: [OAuthTerminal] Proxy Mission Control OAuth terminal to the real auth-runner PTY
- Labels: `MM-318`, `managed-sessions`, `oauth-terminal`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-362 from MM project
Summary: [OAuthTerminal] Proxy Mission Control OAuth terminal to the real auth-runner PTY
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-362 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-362: [OAuthTerminal] Proxy Mission Control OAuth terminal to the real auth-runner PTY

User Story
As a MoonMind operator, I can attach Mission Control's xterm.js OAuth terminal to the real auth-runner PTY, so input, output, resize, heartbeat, and close behavior reflect the provider login session instead of local frame acknowledgements only.

Acceptance Criteria
- Given an OAuth session reaches bridge readiness, Mission Control attaches through a one-time token, owner-scoped, TTL-enforced WebSocket.
- Given the browser sends input frames, bytes reach only the session's auth-runner PTY.
- Given the PTY emits output, Mission Control receives terminal output without exposing credential files, environment dumps, or raw auth-volume listings.
- Given resize and heartbeat frames occur, the PTY size and liveness state are handled and connection metadata is persisted.
- Given unsupported frames such as generic Docker exec or task-terminal attach are sent, the bridge rejects them and records a safe close reason.
- Given ordinary managed task execution runs later, it still uses Codex App Server and not the OAuth terminal transport.

Current Evidence
- specs/183-oauth-terminal-flow/verification.md marks resize/input/output/heartbeat routing as PARTIAL because real PTY forwarding is represented by bridge metadata in local tests.
- api_service/api/routers/oauth_sessions.py exposes the attach WebSocket used by the OAuth terminal page, but that path currently validates frames and returns acknowledgements rather than forwarding PTY bytes.
- api_service/api/websockets.py contains a separate Docker exec terminal path that is not the OAuth terminal page's attach endpoint.

Requirements
- Proxy Mission Control OAuth terminal input frames to the real auth-runner PTY for the owning OAuth session only.
- Stream auth-runner PTY output back to Mission Control through the OAuth terminal attach WebSocket.
- Route resize, heartbeat, close, and transport-state handling through the OAuth terminal bridge and persist safe connection metadata.
- Enforce one-time token, owner scope, and TTL checks before attaching to a terminal bridge.
- Reject unsupported terminal frame types and record safe close reasons.
- Keep ordinary managed task execution on Codex App Server rather than the OAuth terminal transport.
- Prevent credential file contents, environment dumps, raw auth-volume listings, or other secrets from appearing in terminal output handling, logs, artifacts, or UI responses.

Independent Test
Exercise the OAuth terminal attach path against a session that has reached bridge readiness and verify owner-scoped one-time token enforcement, PTY input forwarding, PTY output streaming, resize handling, heartbeat/liveness behavior, safe rejection of unsupported frames, and continued separation from ordinary managed task execution transport.

Dependencies
- MM-318

Risks
- Real PTY forwarding can expose sensitive provider-login output if sanitization and transport boundaries are incomplete.
- The OAuth attach endpoint and existing Docker exec terminal path must stay separated so generic task-terminal semantics cannot bypass OAuth session policy.
- One-time attach tokens and bridge lifecycle state need deterministic cleanup to avoid stale terminal access.

Out of Scope
- Provider Profile registration semantics already covered by MM-355/MM-359.
- Workload container credential inheritance already covered by MM-335/MM-360.

Source Document
docs/ManagedAgents/OAuthTerminal.md

Source Sections
- 5. OAuth Terminal Contract
- 5.2 Terminal bridge
- 5.3 Session transport state
- 9. Security Model

Coverage IDs
- DESIGN-REQ-011
- DESIGN-REQ-013
- DESIGN-REQ-014
- DESIGN-REQ-015
- DESIGN-REQ-020

Needs Clarification
- None
```

## User Story - OAuth Terminal PTY Proxy

### Summary

As a MoonMind operator, I can attach Mission Control's OAuth terminal to the real auth-runner terminal so provider login input, output, resize, heartbeat, and close behavior reflect the active enrollment session.

### Goal

Mission Control OAuth terminal interactions reach only the intended auth-runner session and return terminal output to the operator without exposing credential material or generic task terminal access.

### Independent Test

Start or simulate an OAuth session that has reached bridge readiness, attach with a valid one-time owner-scoped token, exchange terminal input/output, resize, heartbeat, and close frames, and verify the session records safe connection metadata while unsupported terminal frames are rejected.

### Acceptance Scenarios

1. **Given an OAuth session has reached bridge readiness, when its owner attaches with a valid one-time token before expiry, then Mission Control is connected to that session's auth-runner terminal and the token cannot be reused.**
2. **Given the browser sends terminal input through the OAuth terminal, when the frame is accepted, then the bytes reach only the auth-runner terminal for that OAuth session.**
3. **Given the auth-runner terminal emits output, when Mission Control is attached, then the operator receives terminal output and the response path does not disclose credential files, token values, environment dumps, or raw auth-volume listings.**
4. **Given resize and heartbeat frames occur during attachment, when the bridge handles them, then terminal dimensions and liveness metadata are updated for that OAuth session.**
5. **Given unsupported generic terminal or task-run attach frames are sent, when the OAuth terminal bridge receives them, then it rejects the frames and records a safe close reason.**
6. **Given ordinary managed task execution starts after OAuth enrollment, when the task runtime is selected, then the task continues to use the managed runtime transport instead of the OAuth terminal transport.**

### Edge Cases

- An attach token is expired, reused, malformed, or belongs to another owner.
- The auth runner exits before or during terminal attachment.
- The browser disconnects while login is waiting for user input.
- Unsupported terminal frames attempt to request generic Docker exec behavior.
- Terminal output resembles secret material or includes references to credential storage locations.
- Session cancellation or expiry races with browser input, resize, heartbeat, or close frames.

## Requirements

- **FR-001**: The system MUST allow Mission Control to attach to a bridge-ready OAuth session only through a one-time, owner-scoped, TTL-enforced terminal attachment.
- **FR-002**: The system MUST forward accepted browser input frames only to the auth-runner terminal associated with the attached OAuth session.
- **FR-003**: The system MUST stream auth-runner terminal output back to the attached Mission Control terminal without exposing credential files, token values, environment dumps, or raw auth-volume listings.
- **FR-004**: The system MUST handle resize and heartbeat frames for the attached OAuth terminal and persist connection metadata including liveness, dimensions, disconnections, and close reasons.
- **FR-005**: The system MUST reject unsupported generic Docker exec, task-terminal attachment, or unrelated terminal frames through the OAuth terminal bridge and record a safe close reason.
- **FR-006**: The system MUST keep ordinary managed task execution on the managed runtime transport rather than the OAuth terminal transport.
- **FR-007**: The spec artifacts MUST retain Jira issue key MM-362 and the original preset brief so final verification can compare against the originating Jira request.

## Source Design Requirements

- **DESIGN-REQ-011**: Provide a first-party OAuth terminal architecture using Mission Control, OAuth Session API, MoonMind.OAuthSession, short-lived auth runner, PTY/WebSocket bridge, and xterm.js. Source: `docs/ManagedAgents/OAuthTerminal.md` 5. OAuth Terminal Contract. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004.
- **DESIGN-REQ-013**: Provide authenticated terminal input/output with resize, heartbeat, TTL, ownership enforcement, and close metadata. Source: `docs/ManagedAgents/OAuthTerminal.md` 5.2 Terminal bridge. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004.
- **DESIGN-REQ-014**: Do not expose generic Docker exec access or ordinary task-run terminal attachment through the OAuth terminal bridge. Source: `docs/ManagedAgents/OAuthTerminal.md` 5.2 Terminal bridge. Scope: in scope. Maps to FR-005, FR-006.
- **DESIGN-REQ-015**: Keep OAuth session state transport-neutral while using a MoonMind-owned transport identifier when the bridge is enabled. Source: `docs/ManagedAgents/OAuthTerminal.md` 5.3 Session transport state. Scope: in scope. Maps to FR-001, FR-004, FR-006.
- **DESIGN-REQ-020**: Preserve ownership boundaries among OAuth terminal code, Provider Profile code, managed-session controller code, Codex session runtime code, and Docker workload orchestration. Source: `docs/ManagedAgents/OAuthTerminal.md` 11. Required Boundaries. Scope: in scope. Maps to FR-005, FR-006.

## Dependencies

- MM-318 established the broader OAuth terminal design and decomposition.
- Existing provider profile registration and workload credential inheritance stories remain separate from this feature.

## Out Of Scope

- Provider Profile registration semantics covered by MM-355/MM-359.
- Workload container credential inheritance covered by MM-335/MM-360.
- Generic Docker exec terminal behavior.
- Ordinary managed task-run terminal attachment.
- Replacing managed task execution transport with OAuth terminal scrollback.

## Key Entities

- **OAuth Session**: Credential enrollment or repair flow owned by an operator and scoped to one auth runner.
- **Auth Runner Terminal**: The interactive terminal attached to the provider login process for one OAuth session.
- **OAuth Terminal Bridge**: The authenticated terminal transport that connects Mission Control to the auth-runner terminal.
- **Attach Token**: One-time, owner-scoped, TTL-enforced authorization material for opening the terminal bridge.
- **Connection Metadata**: Safe session records such as attachment time, dimensions, heartbeat state, disconnection, and close reason.

## Success Criteria

- **SC-001**: A bridge-ready OAuth session can be attached by its owner with a valid one-time token, and token reuse is rejected.
- **SC-002**: Terminal input sent from Mission Control reaches the attached auth-runner terminal in an end-to-end validation.
- **SC-003**: Terminal output from the auth-runner terminal is visible to Mission Control without exposing credential files, token values, environment dumps, or raw auth-volume listings.
- **SC-004**: Resize, heartbeat, disconnection, and close behavior produce persisted safe connection metadata.
- **SC-005**: Unsupported generic Docker exec or task-terminal frames are rejected by negative tests.
- **SC-006**: Managed task execution remains on the managed runtime transport in regression coverage.
