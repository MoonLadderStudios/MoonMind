# MM-358 MoonSpec Orchestration Input

## Source

- Jira issue: MM-358
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: OAuth Terminal Enrollment Flow
- Labels: `mm-318`, `moonspec-breakdown`, `oauth-terminal`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-358 from MM project
Summary: OAuth Terminal Enrollment Flow
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-358 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-358: OAuth Terminal Enrollment Flow

MoonSpec Story ID: STORY-004

Short Name
oauth-terminal-flow

User Story
As an authorized operator, I can enroll or repair OAuth credentials through a first-party browser terminal backed by a short-lived auth runner and authenticated PTY/WebSocket bridge.

Acceptance Criteria
- Authorized Codex OAuth enrollment starts an OAuth session and auth runner scoped to the selected volume.
- Mission Control can attach through an authenticated terminal WebSocket after bridge readiness.
- Resize/input/output/heartbeat frames route only to the session PTY and record connection metadata.
- Success, failure, expiry, or cancellation closes the bridge and auth runner without generic Docker exec exposure.
- Managed task execution later uses Codex App Server, not OAuth terminal transport.

Requirements
- Create OAuth sessions through the API.
- Start short-lived auth runner containers with target auth volume mounted at provider enrollment path.
- Attach Mission Control through authenticated PTY/WebSocket bridge rendered with xterm.js.
- Enforce TTL, ownership, resize/heartbeat handling, close metadata, and cleanup.

Independent Test
Start an OAuth session with a fake provider bootstrap command, attach through the WebSocket protocol, complete/finalize the session, and assert terminal metadata, auth runner cleanup, and status transitions.

Dependencies
- STORY-001
- STORY-005

Risks
- Browser terminal security and one-time attach-token behavior need explicit negative tests.

Out of Scope
- Ordinary managed task-run terminal attach.
- Generic Docker exec.
- Codex App Server managed task execution.

Source Document
docs/ManagedAgents/OAuthTerminal.md

Source Sections
- 5. OAuth Terminal Contract
- 5.1 Auth runner container
- 5.2 Terminal bridge
- 9. Security Model
- 11. Required Boundaries

Coverage IDs
- DESIGN-REQ-001
- DESIGN-REQ-008
- DESIGN-REQ-011
- DESIGN-REQ-012
- DESIGN-REQ-013
- DESIGN-REQ-014
- DESIGN-REQ-020

Source Design Coverage
- DESIGN-REQ-001: Provide a first-party way to enroll OAuth credentials and target resulting credential volumes into managed runtime containers.
- DESIGN-REQ-008: Keep managed task execution on Codex App Server, not PTY attach or terminal scrollback.
- DESIGN-REQ-011: Provide a first-party OAuth terminal architecture using Mission Control, OAuth Session API, MoonMind.OAuthSession, short-lived auth runner, PTY/WebSocket bridge, and xterm.js.
- DESIGN-REQ-012: Run a short-lived auth runner container that mounts the auth volume at the provider enrollment path and tears down on success, cancellation, expiry, or failure.
- DESIGN-REQ-013: Provide authenticated PTY/WebSocket terminal I/O with resize, heartbeat, TTL, ownership enforcement, and close metadata.
- DESIGN-REQ-014: Do not expose generic Docker exec access or ordinary task-run terminal attachment through the OAuth terminal bridge.
- DESIGN-REQ-020: Preserve ownership boundaries among OAuth terminal code, Provider Profile code, managed-session controller code, Codex session runtime code, and Docker workload orchestration.

Needs Clarification
- None
