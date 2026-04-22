# MM-448 MoonSpec Orchestration Input

## Source

- Jira issue: MM-448
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Launch Claude Code from the secret_ref provider profile
- Labels: `moonmind-workflow-mm-ee47cb53-4263-419f-aecd-fe52ac23f51c`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-448 from MM project
Summary: Launch Claude Code from the secret_ref provider profile
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-448 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-448: Launch Claude Code from the secret_ref provider profile

Source Reference
Source Document: docs/ManagedAgents/ClaudeAnthropicOAuth.md
Source Title: MoonMind Design: claude_anthropic Settings Authentication (Repo-Backed)
Source Sections:
- 4. Desired profile shape
- 8. Runtime launch behavior
- 10.2 Backend
- 11. Final recommendation
Coverage IDs:
- DESIGN-REQ-006
- DESIGN-REQ-013

User Story
As MoonMind launching a Claude Code managed runtime, I can resolve the claude_anthropic provider profile, clear conflicting environment keys, resolve the managed secret, inject ANTHROPIC_API_KEY, and start claude_code without adding a new runtime-selection model.

Acceptance Criteria
- claude_anthropic launches through the existing profile-driven materialization path.
- ANTHROPIC_API_KEY is injected from the managed secret referenced by anthropic_api_key.
- ANTHROPIC_AUTH_TOKEN, ANTHROPIC_BASE_URL, and OPENAI_API_KEY are cleared before launch when configured.
- No new runtime-selection concept is introduced.
- Missing or unreadable secret bindings produce actionable failure output without exposing secret values.

Requirements
- Runtime launch must resolve provider profile, apply clear_env_keys, resolve secret_refs, inject ANTHROPIC_API_KEY, and launch claude_code.
- Workflow or activity payloads must carry compact refs/metadata rather than raw token values.

Implementation Notes
- Preserve MM-448 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/ManagedAgents/ClaudeAnthropicOAuth.md` as the source design reference for provider-profile-backed Claude Anthropic runtime launch behavior.
- Scope the implementation to launching Claude Code from the existing `claude_anthropic` profile-driven materialization path.
- Resolve the `claude_anthropic` provider profile and use its `credential_source=secret_ref` and `runtime_materialization_mode=api_key_env` shape.
- Resolve the managed secret referenced by `anthropic_api_key` and inject only `ANTHROPIC_API_KEY` into the Claude Code runtime environment.
- Apply `clear_env_keys` before launch so conflicting `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, and `OPENAI_API_KEY` values are removed when configured.
- Keep raw secret values out of workflow/activity payloads, logs, diagnostics, artifacts, and generated MoonSpec context; carry only compact refs and secret-free metadata.
- Missing, unreadable, unauthorized, or malformed secret bindings must fail with actionable, secret-free diagnostics.
- Do not introduce a new runtime-selection model or fork launch behavior away from existing provider profile materialization.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-448 blocks MM-447, whose embedded status is Code Review.
- Trusted Jira link metadata at fetch time shows MM-448 is blocked by MM-449, whose embedded status is Selected for Development.

Needs Clarification
- None
