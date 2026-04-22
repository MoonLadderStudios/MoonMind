# Feature Specification: Claude Token Enrollment Drawer

**Feature Branch**: `227-claude-token-enrollment`
**Created**: 2026-04-22
**Status**: Draft
**Input**:

```text
Jira issue: MM-446 from MM project
Summary: Provide a Claude manual token enrollment drawer with explicit lifecycle states
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-446 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-446: Provide a Claude manual token enrollment drawer with explicit lifecycle states

Source Reference
- Source Document: docs/ManagedAgents/ClaudeAnthropicOAuth.md
- Source Title: MoonMind Design: claude_anthropic Settings Authentication (Repo-Backed)
- Source Sections:
  - 3. Design decision
  - 5.3 Modal / drawer flow
  - 5.4 Validation feedback
  - 10.1 Frontend
- Coverage IDs:
  - DESIGN-REQ-005
  - DESIGN-REQ-008
  - DESIGN-REQ-009

User Story
As an operator connecting Claude Anthropic, I can follow an external enrollment instruction, paste the returned token into a secure field, and watch the flow progress through validation, save, profile update, ready, or failed states.

Acceptance Criteria
- The modal or drawer includes states equivalent to not_connected, awaiting_external_step, awaiting_token_paste, validating_token, saving_secret, updating_profile, ready, and failed.
- The UI does not describe Claude manual enrollment as a terminal OAuth session.
- Validation failures show a redacted failure reason without echoing the submitted token.
- The status column can display connected/not connected, last validated timestamp, failure reason, backing secret existence, and launch readiness when provided by the backend.

Requirements
- Operators can paste a returned token into a secure input.
- The pasted token is cleared from local UI state after successful commit or cancellation.
- Readiness and validation metadata are surfaced in the same Settings subsection as provider profiles.

Implementation Notes
- Preserve MM-446 in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/ManagedAgents/ClaudeAnthropicOAuth.md` as the source design reference for the manual Claude Anthropic enrollment decision, drawer flow, validation feedback, and frontend behavior.
- Scope implementation to a Claude manual token enrollment modal or drawer with explicit lifecycle states.
- Do not describe Claude manual enrollment as a terminal OAuth session.
- Keep submitted token values out of persisted UI state, logs, errors, and user-visible failure text.
- Surface readiness and validation metadata in the existing Settings provider-profile subsection.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-446 blocks MM-445, whose embedded status is Code Review.
- Trusted Jira link metadata at fetch time shows MM-446 is blocked by MM-447, whose embedded status is Backlog.
```

## Classification

Single-story runtime feature request. The brief contains one independently testable Settings behavior change: a `claude_anthropic` provider profile action opens a manual token enrollment drawer or modal with explicit lifecycle states, secret-safe token handling, redacted validation failure feedback, and status/readiness metadata in the existing Provider Profiles subsection.

## User Story - Claude Manual Token Enrollment

**Summary**: As an operator connecting Claude Anthropic, I want a focused manual token enrollment drawer so I can follow the external Claude enrollment ceremony, paste the returned token securely, and understand whether validation, saving, profile update, readiness, or failure has occurred.

**Goal**: Operators can complete and monitor Claude Anthropic manual token enrollment from the existing Settings provider profile flow without mistaking it for a terminal OAuth session and without exposing the pasted token after submission or cancellation.

**Independent Test**: Render the Settings Providers & Secrets table with a supported `claude_anthropic` profile, open the Claude enrollment control, move through the external-step, token-paste, validation, save/profile-update, ready, and failed UI states, then verify the token value is cleared on success and cancellation, validation errors are redacted, status metadata renders in the profile row, and no terminal OAuth wording appears for Claude.

**Acceptance Scenarios**:

1. **Given** a supported `claude_anthropic` provider row is not connected, **When** the operator activates `Connect Claude`, **Then** a focused drawer or modal opens in the Settings provider-profile flow with the manual enrollment lifecycle starting from not connected and external-step instructions.
2. **Given** the enrollment drawer is open, **When** the operator proceeds past the external instruction step, **Then** the UI shows a secure returned-token paste field and an awaiting-token-paste state.
3. **Given** the operator submits a returned token, **When** validation and persistence proceed, **Then** the UI shows progress states equivalent to validating-token, saving-secret, updating-profile, and ready without showing terminal OAuth language.
4. **Given** validation fails, **When** the failure is displayed, **Then** the UI shows a redacted failure reason and never echoes the submitted token.
5. **Given** enrollment succeeds or the operator cancels the drawer, **When** the drawer closes or reaches ready, **Then** the pasted token is cleared from local UI state.
6. **Given** backend or trusted provider metadata includes validation/readiness information, **When** the provider row renders, **Then** the Status column can show connected or not connected, last validated timestamp, redacted failure reason, backing secret existence, and launch readiness.

### Edge Cases

- The operator opens the drawer and closes it without pasting a token.
- The operator attempts to submit an empty token.
- Validation fails with a provider error that contains a token-like string.
- Trusted readiness metadata is partial, missing, or indicates no backing secret.
- The profile is connected but launch readiness is false.
- The operator reopens the drawer after a failure and replaces the token.

## Assumptions

- The MM-445 story already provides the row-level Claude action entrypoint; this story owns the drawer or modal body and lifecycle-state behavior.
- If backend manual-auth endpoints are not yet available, this story may introduce or use a narrow service boundary that returns secret-free readiness metadata, but visible UI behavior and secret safety remain mandatory.
- The external enrollment instruction can be represented as operator-facing instructions or a launch affordance; the story does not require MoonMind to complete Anthropic authentication inside a terminal session.

## Source Design Requirements

- **DESIGN-REQ-005** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` section 3): Claude Anthropic Settings auth MUST use a provider-profile-backed manual token enrollment flow instead of forcing paste-back token auth through the volume-first OAuth session flow. Scope: in scope, mapped to FR-001, FR-002, FR-003, FR-007, and FR-011.
- **DESIGN-REQ-008** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` section 5.3): Starting Claude enrollment MUST open a focused drawer or modal that guides the operator through external enrollment, secure token paste, validation, secret save, provider-profile update, and readiness or failure states. Scope: in scope, mapped to FR-001 through FR-006 and FR-012.
- **DESIGN-REQ-009** (`docs/ManagedAgents/ClaudeAnthropicOAuth.md` section 5.4): The provider row SHOULD surface connected state, last validation time, failure reason, backing secret existence, and launch readiness when provided, while keeping validation failure output secret-safe. Scope: in scope, mapped to FR-008, FR-009, FR-010, and FR-013.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `claude_anthropic` manual token enrollment MUST open inside the existing Settings > Providers & Secrets > Provider Profiles flow as a focused drawer or modal.
- **FR-002**: The drawer or modal MUST present lifecycle states equivalent to `not_connected`, `awaiting_external_step`, `awaiting_token_paste`, `validating_token`, `saving_secret`, `updating_profile`, `ready`, and `failed`.
- **FR-003**: The Claude manual enrollment flow MUST NOT describe itself as terminal OAuth or reuse Codex terminal OAuth session wording.
- **FR-004**: Operators MUST be able to paste the returned Claude token into a secure input field.
- **FR-005**: The pasted token MUST be cleared from local UI state after successful commit.
- **FR-006**: The pasted token MUST be cleared from local UI state after cancellation or drawer close.
- **FR-007**: Token validation and persistence progress MUST be represented as separate visible states so operators can distinguish validation, secret save, profile update, and ready outcomes.
- **FR-008**: Validation failure messages MUST be redacted and MUST NOT echo submitted token values or token-like substrings.
- **FR-009**: The provider row Status column MUST be able to show connected or not connected state when trusted readiness metadata is provided.
- **FR-010**: The provider row Status column MUST be able to show last validated timestamp, redacted failure reason, backing secret existence, and launch readiness when trusted readiness metadata provides those values.
- **FR-011**: The flow MUST rely on provider-profile-backed manual token enrollment semantics and MUST NOT invoke the existing volume-first terminal OAuth session behavior for Claude token paste.
- **FR-012**: Empty token submission MUST be blocked with a non-secret validation message.
- **FR-013**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve MM-446 and the source design mappings.

### Key Entities

- **Claude Enrollment Drawer State**: The current user-visible lifecycle step for manual Claude enrollment.
- **Returned Claude Token Input**: A secret input value that exists only in transient UI state until submission, success, failure handling, or cancellation clears it.
- **Claude Readiness Metadata**: Trusted provider-profile metadata describing connection state, validation timestamp, failure reason, backing secret existence, and launch readiness.
- **Manual Token Enrollment Result**: Secret-free validation and persistence outcome used to update the drawer and provider row status.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: UI tests confirm opening `Connect Claude` displays a drawer or modal with manual enrollment states and no terminal OAuth wording.
- **SC-002**: UI tests confirm operators can reach the token paste state, submit a token, and see validation, saving, updating, and ready progress states.
- **SC-003**: UI tests confirm cancellation and successful completion clear the pasted token from local UI state.
- **SC-004**: UI tests confirm validation failures display redacted failure text and never echo the submitted token.
- **SC-005**: UI tests confirm trusted readiness metadata renders connected/not connected, last validated, failure, backing secret, and launch-readiness status details in the provider row.
- **SC-006**: Regression tests confirm Claude manual token enrollment does not invoke Codex terminal OAuth session behavior.
- **SC-007**: Traceability verification confirms MM-446 and DESIGN-REQ-005, DESIGN-REQ-008, and DESIGN-REQ-009 remain present in MoonSpec artifacts and final verification evidence.
