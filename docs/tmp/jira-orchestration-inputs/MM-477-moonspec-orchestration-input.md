# MM-477 MoonSpec Orchestration Input

## Source

- Jira issue: MM-477
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Claude Settings Credential Method Actions
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-477 from MM project
Summary: Claude Settings Credential Method Actions
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-477 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-477: Claude Settings Credential Method Actions

Source Reference
- Source document: `docs/ManagedAgents/ClaudeAnthropicOAuth.md`
- Source title: Claude Anthropic OAuth in Settings
- Source sections:
  - 1. Product Intent
  - 3. Settings UX
  - 3.1 Placement
  - 3.2 Row Actions
  - 8. API-Key Auth Is Separate
- Coverage IDs:
  - DESIGN-REQ-001
  - DESIGN-REQ-002
  - DESIGN-REQ-005

User Story
As an operator, I can manage Claude Anthropic credentials from the existing Provider Profiles table and choose either OAuth enrollment or API-key enrollment without confusing the two credential methods.

Acceptance Criteria
- Given the operator opens Settings -> Providers & Secrets -> Provider Profiles, then the `claude_anthropic` row is available from that table rather than a separate Claude auth page.
- Given the Claude Anthropic row is rendered, then it exposes Connect with Claude OAuth and Use Anthropic API key as distinct first-class actions.
- Given an OAuth volume is present and provider policy permits checks, then Validate OAuth is available for the row.
- Given disconnect is supported by the provider-profile lifecycle policy, then Disconnect OAuth is available for the row.
- Given the operator chooses Use Anthropic API key, then the flow stores an Anthropic API key in Managed Secrets and does not create an OAuth terminal session.
- Given Claude-specific behavior is shown, then Codex-specific labels are not reused for the Claude Anthropic row.

Requirements
- Keep Claude Anthropic credential setup inside the existing Provider Profiles table.
- Expose distinct OAuth and API-key enrollment actions for Claude Anthropic.
- Route API-key enrollment to Managed Secrets with `ANTHROPIC_API_KEY` materialization, not through the browser terminal.
- Use Claude-specific labels for Claude actions while preserving Codex OAuth behavior for `codex_default`.

Relevant Implementation Notes
- Preserve MM-477 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/ManagedAgents/ClaudeAnthropicOAuth.md` as the source design reference for Claude Anthropic provider-profile placement, row actions, OAuth enrollment, API-key enrollment, and API-key/OAuth separation.
- Implement credential actions in the existing Settings -> Providers & Secrets -> Provider Profiles table rather than introducing or relying on a separate Claude auth page.
- Present Claude OAuth and Anthropic API-key enrollment as distinct row actions for `claude_anthropic`.
- Ensure the API-key path stores an Anthropic API key in Managed Secrets and materializes it as `ANTHROPIC_API_KEY`.
- Ensure choosing the API-key path does not create an OAuth terminal session.
- Keep Codex OAuth behavior for `codex_default` intact while using Claude-specific labels for Claude Anthropic actions.

Validation
- Verify the `claude_anthropic` provider profile appears in Settings -> Providers & Secrets -> Provider Profiles.
- Verify the Claude Anthropic row exposes distinct Connect with Claude OAuth and Use Anthropic API key actions.
- Verify Validate OAuth appears when an OAuth volume is present and provider policy permits validation checks.
- Verify Disconnect OAuth appears when disconnect is supported by provider-profile lifecycle policy.
- Verify Use Anthropic API key stores the key in Managed Secrets for `ANTHROPIC_API_KEY` materialization.
- Verify Use Anthropic API key does not create an OAuth terminal session.
- Verify Claude Anthropic UI copy does not reuse Codex-specific labels.
- Verify existing `codex_default` OAuth behavior is preserved.

Non-Goals
- Creating a separate Claude auth page outside the existing Provider Profiles table.
- Routing Anthropic API-key enrollment through the OAuth browser terminal flow.
- Replacing or regressing existing Codex OAuth behavior for `codex_default`.

Needs Clarification
- None
