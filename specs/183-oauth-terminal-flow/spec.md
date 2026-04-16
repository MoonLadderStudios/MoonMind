# Feature Specification: OAuth Terminal Enrollment Flow

**Feature Branch**: `183-oauth-terminal-flow`  
**Created**: 2026-04-16  
**Status**: Draft  
**Input**:

```text
Jira issue: MM-318 from MM board
Summary: breakdown docs\ManagedAgents\OAuthTerminal.md
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-318 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-318: breakdown docs\ManagedAgents\OAuthTerminal.md

Selected generated story: STORY-004 OAuth Terminal Enrollment Flow
Dependencies: STORY-001, STORY-005
Breakdown JSON: docs/tmp/story-breakdowns/mm-318-breakdown-docs-managedagents-oauthterminal-md/stories.json
Source design: docs/ManagedAgents/OAuthTerminal.md
```

## User Story - OAuth Terminal Enrollment Flow

### Summary

As an authorized operator, I can enroll or repair OAuth credentials through a first-party browser terminal backed by a short-lived auth runner and authenticated PTY/WebSocket bridge.

### Goal

Create OAuth sessions through the API.

### Independent Test

Start an OAuth session with a fake provider bootstrap command, attach through the WebSocket protocol, complete/finalize the session, and assert terminal metadata, auth runner cleanup, and status transitions.

### Acceptance Scenarios

1. **Given an authorized operator starts Codex OAuth enrollment, when the request is accepted, then MoonMind starts an OAuth session and auth runner scoped to the selected volume.**
2. **Given the OAuth session reaches bridge readiness, when Mission Control attaches, then it uses an authenticated terminal WebSocket.**
3. **Given terminal resize, input, output, or heartbeat frames occur, when the bridge handles them, then they are routed only to the session PTY and connection metadata is recorded.**
4. **Given the session succeeds, fails, expires, or is cancelled, when cleanup runs, then the bridge and auth runner are closed and no generic Docker exec endpoint is exposed.**
5. **Given managed task execution runs later, when the task session starts, then it uses Codex App Server instead of OAuth terminal transport.**

### Edge Cases

- Attach token is reused or belongs to another user.
- Browser disconnects during provider login.
- Auth runner exits before bridge readiness.
- Provider bootstrap command emits credential-like terminal output.
- Session cancellation races with finalization.

## Requirements

- **FR-001**: The system MUST create OAuth sessions through the API.
- **FR-002**: The system MUST start short-lived auth runner containers with target auth volume mounted at provider enrollment path.
- **FR-003**: The system MUST attach Mission Control through authenticated PTY/WebSocket bridge rendered with xterm.js.
- **FR-004**: The system MUST enforce TTL, ownership, resize/heartbeat handling, close metadata, and cleanup.
- **FR-005**: The spec artifacts MUST retain Jira issue key MM-318 and the original preset brief so final verification can compare against the originating Jira request.

## Source Design Requirements

- **DESIGN-REQ-001**: Provide a first-party way to enroll OAuth credentials and target resulting credential volumes into managed runtime containers. Source: `docs/ManagedAgents/OAuthTerminal.md` 1. Purpose. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004.
- **DESIGN-REQ-008**: Keep managed task execution on Codex App Server, not PTY attach or terminal scrollback. Source: `docs/ManagedAgents/OAuthTerminal.md` 4. Volume Targeting Rules; 10. Operator Behavior. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004.
- **DESIGN-REQ-011**: Provide a first-party OAuth terminal architecture using Mission Control, OAuth Session API, MoonMind.OAuthSession, short-lived auth runner, PTY/WebSocket bridge, and xterm.js. Source: `docs/ManagedAgents/OAuthTerminal.md` 5. OAuth Terminal Contract. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004.
- **DESIGN-REQ-012**: Run a short-lived auth runner container that mounts the auth volume at the provider enrollment path and tears down on success, cancellation, expiry, or failure. Source: `docs/ManagedAgents/OAuthTerminal.md` 5.1 Auth runner container. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004.
- **DESIGN-REQ-013**: Provide authenticated PTY/WebSocket terminal I/O with resize, heartbeat, TTL, ownership enforcement, and close metadata. Source: `docs/ManagedAgents/OAuthTerminal.md` 5.2 Terminal bridge. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004.
- **DESIGN-REQ-014**: Do not expose generic Docker exec access or ordinary task-run terminal attachment through the OAuth terminal bridge. Source: `docs/ManagedAgents/OAuthTerminal.md` 5.2 Terminal bridge. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004.
- **DESIGN-REQ-020**: Preserve ownership boundaries among OAuth terminal code, Provider Profile code, managed-session controller code, Codex session runtime code, and Docker workload orchestration. Source: `docs/ManagedAgents/OAuthTerminal.md` 11. Required Boundaries. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004.
- **DESIGN-REQ-002**: Codex-focused managed-session scope. Scope: out of scope for this isolated story; covered by STORY-001.
- **DESIGN-REQ-003**: Durable Codex auth volume. Scope: out of scope for this isolated story; covered by STORY-001.
- **DESIGN-REQ-004**: Shared task workspace volume. Scope: out of scope for this isolated story; covered by STORY-002.
- **DESIGN-REQ-005**: Per-task workspace layout. Scope: out of scope for this isolated story; covered by STORY-002, STORY-003.
- **DESIGN-REQ-006**: Explicit auth-volume target. Scope: out of scope for this isolated story; covered by STORY-002.
- **DESIGN-REQ-007**: One-way auth seeding. Scope: out of scope for this isolated story; covered by STORY-003.
- **DESIGN-REQ-009**: No workload auth inheritance. Scope: out of scope for this isolated story; covered by STORY-006.
- **DESIGN-REQ-010**: No credential leakage. Scope: out of scope for this isolated story; covered by STORY-001, STORY-003, STORY-005, STORY-006.
- **DESIGN-REQ-015**: Transport-neutral OAuth state. Scope: out of scope for this isolated story; covered by STORY-005.
- **DESIGN-REQ-016**: Provider Profile registration. Scope: out of scope for this isolated story; covered by STORY-001, STORY-005.
- **DESIGN-REQ-017**: Managed Codex session launch. Scope: out of scope for this isolated story; covered by STORY-002.
- **DESIGN-REQ-018**: Credential verification boundaries. Scope: out of scope for this isolated story; covered by STORY-005.
- **DESIGN-REQ-019**: Artifact-backed operator evidence. Scope: out of scope for this isolated story; covered by STORY-003.

## Dependencies

- STORY-001
- STORY-005

## Out Of Scope

- Ordinary managed task-run terminal attach.
- Generic Docker exec.
- Codex App Server managed task execution.

## Key Entities

- **OAuth Session**: Credential enrollment or repair flow owned by MoonMind and scoped to an operator and target auth volume.
- **Auth Runner**: Short-lived container that runs provider login commands against the target auth volume.
- **Terminal Bridge**: Authenticated PTY/WebSocket transport between Mission Control and the auth runner PTY.
- **Attach Session**: Browser terminal connection with ownership, TTL, resize, heartbeat, and close metadata.

## Success Criteria

- **SC-001**: An API or workflow test starts a Codex OAuth session and auth runner for a selected volume.
- **SC-002**: A WebSocket test attaches Mission Control through authenticated bridge metadata.
- **SC-003**: Bridge tests cover resize, input, output, heartbeat, ownership, and close metadata.
- **SC-004**: Cleanup tests verify success, failure, expiry, and cancellation stop bridge and runner resources.
- **SC-005**: Negative tests verify no generic Docker exec or ordinary task terminal is exposed.
