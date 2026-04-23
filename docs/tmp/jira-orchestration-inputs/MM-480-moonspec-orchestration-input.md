# MM-480 MoonSpec Orchestration Input

## Source

- Jira issue: MM-480
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Claude OAuth Verification and Profile Registration
- Labels: `moonmind-workflow-mm-8f0966f3-d711-4289-9669-3a8e435353fb`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-480 from MM project
Summary: Claude OAuth Verification and Profile Registration
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-480 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-480: Claude OAuth Verification and Profile Registration

Source Reference
- Source Document: docs/ManagedAgents/ClaudeAnthropicOAuth.md
- Source Title: Claude Anthropic OAuth in Settings
- Source Sections:
  - 5. Verification
  - 6. Profile Registration
  - 9. Security Requirements
- Coverage IDs:
  - DESIGN-REQ-003
  - DESIGN-REQ-004
  - DESIGN-REQ-013
  - DESIGN-REQ-014
  - DESIGN-REQ-016
  - DESIGN-REQ-018

User Story
As an operator, I can finalize a Claude OAuth session and have MoonMind verify the auth volume, register or update the OAuth-backed provider profile, and expose only secret-free verification metadata.

Acceptance Criteria
- Given finalization runs after Claude login, then MoonMind verifies account-auth material under the mounted Claude home before profile registration.
- Given known artifacts such as credentials.json or qualifying settings.json are present, then verification can return verified status using only metadata.
- Given verification output is returned or persisted, then it includes only secret-free fields such as verified, status, reason, artifact counts, and timestamps.
- Given verification succeeds, then claude_anthropic is registered or updated with credential_source oauth_volume, runtime_materialization_mode oauth_home, volume_ref claude_auth_volume, and volume_mount_path /home/app/.claude.
- Given profile registration succeeds, then Provider Profile Manager is synced for runtime_id claude_code.
- Given an unauthorized operator attempts finalize or repair, then the operation is rejected.

Requirements
- Verify Claude account-auth material before registering or updating the provider profile.
- Accept only explicitly documented Claude credential artifacts as proof of account setup.
- Return and persist secret-free verification metadata only.
- Register or update the OAuth-backed claude_anthropic provider profile after successful verification.
- Sync Provider Profile Manager for claude_code after successful registration.

Relevant Implementation Notes
- Preserve MM-480 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/ManagedAgents/ClaudeAnthropicOAuth.md` as the source design reference for Claude OAuth verification, profile registration, and security requirements.
- Verification must inspect account-auth material under the mounted Claude home before profile registration and must not expose raw credential contents.
- Treat `credentials.json` and qualifying `settings.json` data as proof only through secret-free metadata such as verified state, status, reason, artifact counts, and timestamps.
- On successful verification, register or update `claude_anthropic` with `credential_source` `oauth_volume`, `runtime_materialization_mode` `oauth_home`, `volume_ref` `claude_auth_volume`, and `volume_mount_path` `/home/app/.claude`.
- Sync Provider Profile Manager for `runtime_id` `claude_code` after profile registration succeeds.
- Reject unauthorized finalize or repair attempts.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-480 blocks MM-479, whose embedded status is Code Review.
- Trusted Jira link metadata at fetch time shows MM-480 is blocked by MM-481, whose embedded status is Backlog.

Needs Clarification
- None
