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
