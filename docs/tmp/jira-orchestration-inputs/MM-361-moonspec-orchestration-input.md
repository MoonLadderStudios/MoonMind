# MM-361 MoonSpec Orchestration Input

## Source

- Jira issue: MM-361
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: [OAuthTerminal] Replace placeholder auth runner with provider bootstrap PTY lifecycle
- Labels: `MM-318`, `managed-sessions`, `oauth-terminal`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-361 from MM project
Summary: [OAuthTerminal] Replace placeholder auth runner with provider bootstrap PTY lifecycle
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-361 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-361: [OAuthTerminal] Replace placeholder auth runner with provider bootstrap PTY lifecycle

Short Name
oauth-runner-bootstrap-pty

User Story
As a MoonMind operator, I can start a Codex OAuth enrollment session that launches a short-lived auth runner container running the provider bootstrap command in a PTY, so credential enrollment is first-party and does not depend on placeholder container behavior.

Acceptance Criteria
- Given an authorized Codex OAuth session starts, the auth runner container mounts the selected auth volume at the provider enrollment path.
- Given the provider registry defines a bootstrap command, the runner executes that command in a PTY owned by the OAuth enrollment session.
- Given the session succeeds, fails, expires, or is cancelled, the runner stops and cleanup is idempotent.
- Given the runner is active, it exposes no ordinary managed task terminal and no generic Docker exec capability.
- Given runner startup fails because Docker or the provider CLI is unavailable, the OAuth session fails with an actionable, redacted reason.

Current Evidence
- `specs/183-oauth-terminal-flow/verification.md` has verdict `ADDITIONAL_WORK_NEEDED`.
- `moonmind/workflows/temporal/runtime/terminal_bridge.py` currently starts a runner image with `sleep` and comments that the real specialized PTY container is not implemented.

Requirements
- Replace the placeholder auth runner lifecycle with a short-lived runner container that executes the selected provider bootstrap command in a PTY.
- Mount the selected Codex OAuth auth volume at the provider enrollment path during enrollment.
- Scope the runner and PTY ownership to the OAuth enrollment session.
- Route terminal behavior through MoonMind's authenticated PTY/WebSocket bridge only.
- Stop the runner after success, failure, expiry, or cancellation, and make cleanup idempotent.
- Fail with actionable, redacted diagnostics when Docker, runner startup, or provider CLI execution is unavailable.
- Preserve the boundary that OAuth terminal code is for enrollment only, not managed task execution or generic Docker exec.

Independent Test
Start a Codex OAuth session with a fake provider bootstrap command, assert the auth runner mounts the selected auth volume, executes the bootstrap command inside the session-owned PTY, exposes only authenticated terminal bridge access, and performs idempotent cleanup for success, failure, expiry, and cancellation paths with redacted failure reasons.

Out of Scope
- Managed Codex task execution changes.
- Claude/Gemini task-scoped session parity.
- Generic Docker exec exposure.
- Ordinary managed task terminal attachment.

Source Document
docs/ManagedAgents/OAuthTerminal.md

Source Sections
- 5. OAuth Terminal Contract
- 5.1 Auth runner container
- 10. Operator Behavior

Coverage IDs
- DESIGN-REQ-011
- DESIGN-REQ-012
- DESIGN-REQ-014
- DESIGN-REQ-020

Source Design Coverage
- DESIGN-REQ-011: Provide a first-party OAuth terminal architecture using Mission Control, OAuth Session API, MoonMind.OAuthSession, short-lived auth runner, PTY/WebSocket bridge, and xterm.js.
- DESIGN-REQ-012: Run a short-lived auth runner container that mounts the auth volume at the provider enrollment path and tears down on success, cancellation, expiry, or failure.
- DESIGN-REQ-014: Do not expose generic Docker exec access or ordinary task-run terminal attachment through the OAuth terminal bridge.
- DESIGN-REQ-020: Preserve ownership boundaries among OAuth terminal code, Provider Profile code, managed-session controller code, Codex session runtime code, and Docker workload orchestration.

Relevant Implementation Notes
- The auth runner container is short-lived and scoped to one OAuth session.
- For Codex, the auth runner targets `codex_auth_volume` at `/home/app/.codex` while enrollment is happening.
- The OAuth terminal is only for credential enrollment or repair and must not become the runtime surface for managed Codex task execution.
- Later task-scoped Codex managed sessions target the registered provider profile and mount the auth volume separately when needed.

Needs Clarification
- None
