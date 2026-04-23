# Feature Specification: Claude Browser Terminal Sign-In Ceremony

**Feature Branch**: `242-claude-browser-terminal-signin`
**Created**: 2026-04-23
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-479 as the canonical Moon Spec orchestration input.

Additional constraints:


Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

Original brief reference: `docs/tmp/jira-orchestration-inputs/MM-479-moonspec-orchestration-input.md`.
Classification: single-story runtime feature request.

## Original Preset Brief

```text
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
```

## User Story - Claude Browser Terminal Sign-In Ceremony

**Summary**: As an operator, I can complete Claude OAuth in a MoonMind browser terminal by opening Claude's authentication URL externally and pasting the returned token or code back into the terminal while the session waits for me.

**Goal**: Claude OAuth enrollment keeps the operator attached to the browser terminal during the external Anthropic sign-in ceremony, forwards pasted code input only to the Claude CLI PTY, and preserves secret-safe terminal and attach-token boundaries.

**Independent Test**: Start or simulate a Claude OAuth session that reaches terminal-ready or `awaiting_user`, open the OAuth terminal page, attach with a one-time token, receive Claude login output, paste a returned authorization code, and verify the code is forwarded to the Claude PTY while only bounded secret-free metadata is persisted.

**Acceptance Scenarios**:

1. **Given** a Claude OAuth session is created for `runtime_id = claude_code`, **When** the session exposes terminal bridge identifiers, **Then** Mission Control can open the OAuth browser terminal attached to the short-lived auth runner.
2. **Given** the Claude CLI prints an authentication URL, **When** the session is waiting for external Anthropic sign-in, **Then** the terminal remains attachable while the session reports an operator-waiting state such as `awaiting_user`.
3. **Given** the operator pastes a returned token or authorization code into the browser terminal, **When** the WebSocket bridge receives the input frame, **Then** the PTY bridge forwards the exact input bytes to the Claude CLI process.
4. **Given** terminal input contains a token or code, **When** MoonMind records terminal metadata or returns API responses, **Then** it does not persist the raw input as a Managed Secret, API response field, artifact, provider profile value, workflow payload, or durable terminal metadata.
5. **Given** an OAuth terminal attach token is issued, **When** it is used for WebSocket attachment, **Then** the token is short-lived, single-use, stored only as a hash, and not exposed through terminal metadata.
6. **Given** the OAuth terminal is active, **When** the browser sends terminal frames, **Then** only the OAuth auth-runner PTY receives input and generic task execution or Docker exec frames are rejected.

### Edge Cases

- The terminal bridge is not ready when the page first opens.
- The OAuth session reaches a final state before the terminal attaches.
- A stale or reused attach token is presented to the WebSocket endpoint.
- The operator pastes a token-like string into the terminal.
- The PTY disconnects after the operator submits input.
- Non-Claude OAuth sessions continue to use the same terminal bridge safely.

## Assumptions

- MM-478 already covers Claude provider registry defaults, auth-runner startup, mounted Claude home, and `claude login`; MM-479 covers the browser terminal ceremony on top of that backend.
- The OAuth terminal page is a shared UI for OAuth runtimes. The acceptance evidence for MM-479 must include a Claude runtime/session scenario, but the page does not need a separate Claude-only route.
- Final volume verification and provider profile registration are covered by surrounding OAuth stories; this story is complete when the terminal ceremony and secret-safe input bridge are verified.

## Source Design Requirements

- **DESIGN-REQ-006** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` section 3.3): Choosing `Connect with Claude OAuth` must open an OAuth terminal session through the OAuth Session API, `MoonMind.OAuthSession`, auth-runner container, PTY/WebSocket bridge, browser terminal, `claude login`, and `claude_auth_volume`. Scope: in scope, mapped to FR-001, FR-002, and FR-008.
- **DESIGN-REQ-007** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` section 3.3): The terminal is for credential enrollment and repair only, not the normal Claude task execution surface. Scope: in scope, mapped to FR-008.
- **DESIGN-REQ-008** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` section 3.4): Claude OAuth prints or opens an authentication URL and expects the operator to paste the returned auth token or authorization code back into the terminal. Scope: in scope, mapped to FR-003 and FR-004.
- **DESIGN-REQ-009** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` section 3.4): The OAuth session should remain in an operator-waiting state such as `awaiting_user`, with the terminal attached long enough for the operator to paste the returned token or code. Scope: in scope, mapped to FR-002 and FR-003.
- **DESIGN-REQ-010** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` section 3.4): Pasted tokens or codes are transient terminal input and must not be stored as Managed Secrets, returned through API responses, written to artifacts, or persisted in provider profile rows. Scope: in scope, mapped to FR-005 and FR-006.
- **DESIGN-REQ-016** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` section 9): Only authorized operators can start, attach to, cancel, finalize, or repair Claude OAuth sessions. Scope: in scope for attach/session ownership, mapped to FR-007.
- **DESIGN-REQ-017** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` section 9): Browser terminal attach tokens are short-lived and single-use. Scope: in scope, mapped to FR-007.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST expose the OAuth terminal page for Claude OAuth sessions created for `runtime_id = claude_code`.
- **FR-002**: System MUST wait for terminal bridge readiness and attach only when the session is in `bridge_ready`, `awaiting_user`, or `verifying` with terminal bridge identifiers.
- **FR-003**: System MUST keep the terminal attachable during an operator-waiting state such as `awaiting_user` so the operator can complete external Anthropic sign-in.
- **FR-004**: System MUST forward pasted terminal input, including returned Claude auth tokens or authorization codes, to the OAuth auth-runner PTY.
- **FR-005**: System MUST keep terminal input content out of durable terminal metadata and record only bounded counters, resize values, close reasons, and other secret-free metadata.
- **FR-006**: System MUST keep token-like terminal input out of OAuth session API responses, provider profile rows, artifacts, and Managed Secrets.
- **FR-007**: System MUST issue browser terminal attach tokens that are scoped to the session owner, short-lived through the session expiration, single-use, and stored server-side only as a hash.
- **FR-008**: System MUST reject generic task terminal or Docker exec frames on the OAuth terminal bridge so the session cannot become a normal Claude task execution terminal.
- **FR-009**: System MUST preserve existing Codex OAuth terminal behavior while adding or verifying the Claude ceremony.

### Key Entities

- **OAuth Session**: Durable enrollment or repair session with runtime, profile, status, expiration, terminal bridge identifiers, owner, and metadata.
- **OAuth Terminal Attach Token**: One-time browser terminal credential generated for a session and validated by the WebSocket endpoint.
- **Terminal Bridge Connection**: Runtime boundary that validates browser terminal frames and forwards allowed input, resize, heartbeat, and close events to the auth-runner PTY.
- **Claude Auth Runner PTY**: Short-lived terminal process running Claude login against the mounted Claude home volume.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: UI tests prove the OAuth terminal page waits through non-ready states and attaches once a Claude session reports `awaiting_user` with terminal bridge identifiers.
- **SC-002**: API route tests prove attach token metadata is hash-only and single-use for an `awaiting_user` OAuth terminal session.
- **SC-003**: Terminal bridge tests prove a Claude authorization-code-like input string is forwarded to the PTY while safe metadata excludes the raw input.
- **SC-004**: Terminal bridge tests prove generic task-terminal or Docker-exec frames are rejected.
- **SC-005**: Focused validation passes for OAuth session route tests, terminal bridge tests, and the OAuth terminal UI tests.
