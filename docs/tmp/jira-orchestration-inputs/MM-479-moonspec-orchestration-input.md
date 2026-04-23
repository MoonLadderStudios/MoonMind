# MM-479 MoonSpec Orchestration Input

## Source

- Jira issue: MM-479
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Claude Browser Terminal Sign-In Ceremony
- Labels: `moonmind-workflow-mm-8f0966f3-d711-4289-9669-3a8e435353fb`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-479 from MM project
Summary: Claude Browser Terminal Sign-In Ceremony
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-479 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-479: Claude Browser Terminal Sign-In Ceremony

Source Reference
- Source Document: docs/ManagedAgents/ClaudeAnthropicOAuth.md
- Source Title: Claude Anthropic OAuth in Settings
- Source Sections:
  - 3.3 OAuth Terminal Flow
  - 3.4 Claude Sign-In Ceremony
  - 4. OAuth Session Backend
  - 9. Security Requirements
- Coverage IDs:
  - DESIGN-REQ-006
  - DESIGN-REQ-007
  - DESIGN-REQ-008
  - DESIGN-REQ-009
  - DESIGN-REQ-010
  - DESIGN-REQ-016
  - DESIGN-REQ-017

User Story
As an operator, I can complete Claude OAuth in a MoonMind browser terminal by opening Claude's authentication URL externally and pasting the returned token or code back into the terminal while the session waits for me.

Acceptance Criteria
- Given a Claude OAuth session is created, then Mission Control opens the in-browser terminal view attached to the short-lived auth runner.
- Given Claude prints an authentication URL, then the terminal remains attached and the session moves to or remains in an operator-waiting state such as awaiting_user.
- Given the operator pastes a returned token or authorization code, then the PTY bridge forwards it only to the Claude CLI process.
- Given terminal input contains a token or code, then MoonMind does not store it as a Managed Secret, return it through an API response, write it to an artifact, or persist it in a provider profile row.
- Given attach tokens are issued for the browser terminal, then they are short-lived and single-use.
- Given the OAuth terminal is active, then it cannot be used as a generic Claude task execution terminal.

Requirements
- Support Claude's URL plus pasted token or authorization-code ceremony instead of Codex device-code semantics.
- Keep the terminal attached while the operator completes external Anthropic sign-in.
- Represent operator wait time with an explicit awaiting_user-style state.
- Treat pasted tokens or codes as transient terminal input only.
- Enforce terminal attach token lifetime, single use, and session ownership.

Relevant Implementation Notes
- Preserve MM-479 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/ManagedAgents/ClaudeAnthropicOAuth.md` as the source design reference for Claude OAuth terminal flow, Claude sign-in ceremony, OAuth session backend behavior, and security requirements.
- The browser terminal is for the short-lived Claude OAuth auth runner only, not generic Claude task execution.
- Keep pasted tokens or authorization codes as transient PTY input only; do not persist them as Managed Secrets, provider profile fields, API responses, artifacts, logs, or durable workflow payloads.
- Keep terminal attach credentials short-lived, single-use, and scoped to the owning session/operator.
- Surface the external Anthropic sign-in wait as an explicit operator-waiting state such as `awaiting_user`.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-479 blocks MM-478, whose embedded status is Code Review.
- Trusted Jira link metadata at fetch time shows MM-479 is blocked by MM-480, whose embedded status is Backlog.

Needs Clarification
- None
