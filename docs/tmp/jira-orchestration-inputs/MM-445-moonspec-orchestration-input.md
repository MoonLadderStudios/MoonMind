# MM-445 MoonSpec Orchestration Input

## Source

- Jira issue: MM-445
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Route claude_anthropic Settings auth actions to a Claude enrollment flow
- Labels: `moonmind-workflow-mm-ee47cb53-4263-419f-aecd-fe52ac23f51c`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-445 from MM project
Summary: Route claude_anthropic Settings auth actions to a Claude enrollment flow
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-445 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-445: Route claude_anthropic Settings auth actions to a Claude enrollment flow

Source Reference
- Source Document: docs/ManagedAgents/ClaudeAnthropicOAuth.md
- Source Title: MoonMind Design: claude_anthropic Settings Authentication (Repo-Backed)
- Source Sections:
  - 2.1 Settings surface
  - 2.3 Current Settings UI implementation
  - 5.1 Placement
  - 5.2 Row-level action model
  - 10.1 Frontend
- Coverage IDs:
  - DESIGN-REQ-001
  - DESIGN-REQ-003
  - DESIGN-REQ-007

User Story
As an operator configuring provider profiles, I can start Claude Anthropic authentication from the existing Providers & Secrets table and see Claude-specific actions instead of a Codex-shaped Auth control.

Acceptance Criteria
- `claude_anthropic` exposes a Connect Claude action when not connected.
- Connected `claude_anthropic` rows expose Replace token, Validate, and Disconnect actions where supported by returned capability/readiness metadata.
- Codex OAuth behavior remains available for `codex_default` without reusing Codex labels for Claude.
- No new standalone Claude auth page or specs directory is created by this story.

Requirements
- Auth capability is derived from profile metadata or explicit strategy, not hardcoded to `profile.runtime_id === codex_cli`.
- The provider profile row remains the entry point for Claude enrollment.
- Action labels distinguish manual Claude enrollment from terminal OAuth.

Implementation Notes
- Preserve MM-445 in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/ManagedAgents/ClaudeAnthropicOAuth.md` as the source design reference for the Settings provider row placement, action model, and frontend behavior.
- Scope implementation to routing `claude_anthropic` Settings authentication actions from the existing Providers & Secrets table into the Claude enrollment flow.
- Keep Codex OAuth behavior available for `codex_default` and avoid reusing Codex OAuth labels for Claude-specific manual token enrollment.
- Do not create a new standalone Claude auth page or a separate specs directory for this story.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-445 is blocked by MM-446, whose embedded status is Backlog.
