# MM-446 MoonSpec Orchestration Input

## Source

- Jira issue: MM-446
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Provide a Claude manual token enrollment drawer with explicit lifecycle states
- Labels: `moonmind-workflow-mm-ee47cb53-4263-419f-aecd-fe52ac23f51c`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

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
