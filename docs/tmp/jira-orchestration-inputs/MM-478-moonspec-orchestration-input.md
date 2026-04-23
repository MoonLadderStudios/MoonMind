# MM-478 MoonSpec Orchestration Input

## Source

- Jira issue: MM-478
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Claude OAuth Provider Registry and Session Backend
- Labels: `moonmind-workflow-mm-8f0966f3-d711-4289-9669-3a8e435353fb`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-478 from MM project
Summary: Claude OAuth Provider Registry and Session Backend
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-478 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-478: Claude OAuth Provider Registry and Session Backend

Source Reference
- Source Document: docs/ManagedAgents/ClaudeAnthropicOAuth.md
- Source Title: Claude Anthropic OAuth in Settings
- Source Sections:
  - 2. OAuth Profile Shape
  - 3.3 OAuth Terminal Flow
  - 4. OAuth Session Backend
- Coverage IDs:
  - DESIGN-REQ-003
  - DESIGN-REQ-006
  - DESIGN-REQ-010
  - DESIGN-REQ-011
  - DESIGN-REQ-012
  - DESIGN-REQ-017
  - DESIGN-REQ-018

User Story
As an operator, when I choose Connect with Claude OAuth, MoonMind creates a Claude-specific OAuth session that starts a short-lived auth runner with the correct mounted Claude home and bootstrap command.

Acceptance Criteria
- Given Connect with Claude OAuth is invoked for claude_anthropic, then POST /api/v1/oauth-sessions creates a session anchored to that provider profile row.
- Given runtime_id = claude_code, then the provider registry resolves auth_mode oauth, session_transport moonmind_pty_ws, default volume claude_auth_volume, mount path /home/app/.claude, provider_id anthropic, provider_label Anthropic, bootstrap command claude login, and success_check claude_config_exists.
- Given the auth runner starts, then it mounts claude_auth_volume as the Claude home used for enrollment.
- Given the auth runner environment is built, then HOME=/home/app, CLAUDE_HOME=/home/app/.claude, and CLAUDE_VOLUME_PATH=/home/app/.claude are set.
- Given ambient API-key variables are present, then ANTHROPIC_API_KEY and CLAUDE_API_KEY are cleared for the OAuth enrollment runner.
- Given the session is for OAuth enrollment or repair, then it is not treated as the normal task execution surface for Claude runs.

Requirements
- Reuse the OAuth Session API and MoonMind.OAuthSession workflow family for Claude OAuth.
- Define a Claude provider registry entry for runtime_id claude_code with the documented transport, volume, provider metadata, bootstrap command, and success check.
- Start a short-lived auth runner with Claude-specific home environment variables and clear competing API-key variables.
- Keep OAuth terminal sessions scoped to credential enrollment and repair.

Relevant Implementation Notes
- Preserve MM-478 in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/ManagedAgents/ClaudeAnthropicOAuth.md` as the source design reference for Claude OAuth profile shape, terminal flow, and OAuth session backend behavior.
- Scope implementation to Claude-specific OAuth provider registry and session backend behavior for `runtime_id = claude_code` / `claude_anthropic`.
- The provider registry should resolve `auth_mode` `oauth`, `session_transport` `moonmind_pty_ws`, default volume `claude_auth_volume`, mount path `/home/app/.claude`, provider ID `anthropic`, provider label `Anthropic`, bootstrap command `claude login`, and success check `claude_config_exists`.
- The auth runner should mount `claude_auth_volume` as the Claude home for enrollment and set `HOME=/home/app`, `CLAUDE_HOME=/home/app/.claude`, and `CLAUDE_VOLUME_PATH=/home/app/.claude`.
- OAuth enrollment runners should clear ambient `ANTHROPIC_API_KEY` and `CLAUDE_API_KEY` values so API-key auth does not mask OAuth setup.
- Claude OAuth sessions are for credential enrollment and repair, not normal Claude task execution.

Validation
- Verify POST `/api/v1/oauth-sessions` for `claude_anthropic` creates a session anchored to the Claude provider profile row.
- Verify the provider registry entry for `runtime_id = claude_code` exposes the expected OAuth auth mode, transport, volume, mount path, provider metadata, bootstrap command, and success check.
- Verify the auth runner mounts `claude_auth_volume` as `/home/app/.claude` and sets `HOME`, `CLAUDE_HOME`, and `CLAUDE_VOLUME_PATH` to the expected values.
- Verify ambient `ANTHROPIC_API_KEY` and `CLAUDE_API_KEY` are cleared for the OAuth enrollment runner.
- Verify Claude OAuth enrollment or repair sessions are not treated as the normal task execution surface for Claude runs.

Needs Clarification
- None

Dependencies
- Trusted Jira link metadata at fetch time shows MM-478 blocks MM-477, whose embedded status is Code Review.
- Trusted Jira link metadata at fetch time shows MM-478 is blocked by MM-479, whose embedded status is Backlog.
