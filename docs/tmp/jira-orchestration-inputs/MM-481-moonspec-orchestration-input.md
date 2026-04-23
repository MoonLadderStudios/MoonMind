# MM-481 MoonSpec Orchestration Input

## Source

- Jira issue: MM-481
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Claude OAuth Runtime Launch Materialization
- Labels: `moonmind-workflow-mm-8f0966f3-d711-4289-9669-3a8e435353fb`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-481 from MM project
Summary: Claude OAuth Runtime Launch Materialization
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-481 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-481: Claude OAuth Runtime Launch Materialization

Source Reference
- Source Document: docs/ManagedAgents/ClaudeAnthropicOAuth.md
- Source Title: Claude Anthropic OAuth in Settings
- Source Sections:
  - 2. OAuth Profile Shape
  - 7. Runtime Launch Behavior
  - 9. Security Requirements
- Coverage IDs:
  - DESIGN-REQ-003
  - DESIGN-REQ-004
  - DESIGN-REQ-015
  - DESIGN-REQ-017
  - DESIGN-REQ-018

User Story
As a task operator, when a Claude run uses the OAuth-backed profile, MoonMind launches `claude_code` with the Claude auth volume materialized as the runtime home and competing API-key variables cleared.

Acceptance Criteria
- Given a Claude task selects `claude_anthropic`, then launch resolves that provider profile before container or runtime startup.
- Given the selected profile contains `clear_env_keys`, then `ANTHROPIC_API_KEY`, `CLAUDE_API_KEY`, and `OPENAI_API_KEY` are removed from the launch environment before `claude_code` starts.
- Given `oauth_home` materialization is selected, then `claude_auth_volume` is mounted or projected at `/home/app/.claude` according to the provider-profile materialization contract.
- Given the runtime environment is built, then Claude home environment variables are set consistently for the runtime.
- Given workflow history, logs, or artifacts are inspected after launch, then raw credential file contents are absent.
- Given a workload or audit artifact path is requested, then the auth volume is not treated as a task workspace or audit artifact.

Requirements
- Resolve `claude_anthropic` at Claude task launch.
- Apply `clear_env_keys` exactly as defined by the selected profile.
- Materialize `claude_auth_volume` at the configured Claude home path for `oauth_home` profiles.
- Set Claude home environment variables consistently before launching `claude_code`.
- Keep raw credential file contents out of workflow history, logs, and artifacts.

Relevant Implementation Notes
- Preserve MM-481 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/ManagedAgents/ClaudeAnthropicOAuth.md` as the source design reference for OAuth profile shape, runtime launch behavior, and security requirements.
- Launch behavior must resolve the `claude_anthropic` provider profile before runtime startup rather than inferring settings ad hoc during command execution.
- Apply the selected profile's `clear_env_keys` exactly and remove competing API-key variables before `claude_code` starts.
- For `oauth_home` profiles, materialize `claude_auth_volume` at `/home/app/.claude` and set Claude home environment variables consistently for the runtime.
- Keep raw credential file contents out of workflow history, logs, artifacts, and audit paths.
- Do not treat the auth volume as a task workspace or as an artifact-backed path.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-481 blocks MM-480, whose embedded status is Code Review.
- Trusted Jira link metadata at fetch time shows MM-481 is blocked by MM-482, whose embedded status is Selected for Development.

Needs Clarification
- None
