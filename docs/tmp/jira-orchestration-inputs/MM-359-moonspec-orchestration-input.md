# MM-359 MoonSpec Orchestration Input

## Source

- Jira issue: MM-359
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: OAuth Session State and Verification Boundaries
- Labels: `mm-318`, `moonspec-breakdown`, `oauth-terminal`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-359 from MM project
Summary: OAuth Session State and Verification Boundaries
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-359 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-359: OAuth Session State and Verification Boundaries

MoonSpec Story ID: STORY-005

Short Name
oauth-state-verify

User Story
As an operator, I can understand OAuth credential readiness through transport-neutral statuses and secret-free verification results at profile and launch boundaries.

Acceptance Criteria
- OAuth sessions progress through transport-neutral states including pending, starting, bridge_ready, awaiting_user, verifying, registering_profile, and terminal states.
- session_transport = none is valid while PTY bridge is disabled and does not imply tmate semantics.
- OAuth verification failure blocks profile registration and exposes a secret-free failure reason.
- Managed-session launch verifies selected profile materialization before marking the session ready.
- Persisted or returned verification output contains compact status/failure metadata only.

Requirements
- Use transport-neutral OAuth statuses.
- Allow session_transport = none while the interactive bridge is disabled.
- Verify durable auth volume credentials before Provider Profile registration.
- Verify selected profile materialization at managed-session launch.
- Keep verification outputs compact and secret-free.

Independent Test
Exercise OAuth session success, cancel, expire, and disabled-bridge paths with mocked volume verification and assert status transitions plus redacted verification outputs.

Dependencies
- STORY-001
- STORY-002

Risks
- In-flight workflow compatibility may require a versioned cutover if status payload shapes change.

Out of Scope
- Full terminal UI implementation.
- Codex App Server turn execution.
- Provider-specific auth UX copy.

Source Document
docs/ManagedAgents/OAuthTerminal.md

Source Sections
- 5.3 Session transport state
- 6. Provider Profile Registration
- 8. Verification
- 9. Security Model
- 11. Required Boundaries

Coverage IDs
- DESIGN-REQ-010
- DESIGN-REQ-015
- DESIGN-REQ-016
- DESIGN-REQ-018
- DESIGN-REQ-020

Source Design Coverage
- DESIGN-REQ-010: Never place raw credential contents in workflow history, logs, artifacts, or UI responses.
- DESIGN-REQ-015: Use transport-neutral OAuth statuses and allow session_transport = none while the interactive bridge is disabled.
- DESIGN-REQ-016: Register or update Provider Profiles after OAuth verification, preserving Codex OAuth fields and slot policy.
- DESIGN-REQ-018: Verify credentials at both the OAuth/profile boundary and the managed-session launch boundary without leaking credential contents.
- DESIGN-REQ-020: Preserve ownership boundaries among OAuth terminal code, Provider Profile code, managed-session controller code, Codex session runtime code, and Docker workload orchestration.

Needs Clarification
- None
