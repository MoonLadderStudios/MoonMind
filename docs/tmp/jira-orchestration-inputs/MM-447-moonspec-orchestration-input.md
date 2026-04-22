# MM-447 MoonSpec Orchestration Input

## Source

- Jira issue: MM-447
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Add secret-safe Claude manual auth API and service behavior
- Labels: `moonmind-workflow-mm-ee47cb53-4263-419f-aecd-fe52ac23f51c`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-447 from MM project
Summary: Add secret-safe Claude manual auth API and service behavior
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-447 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-447: Add secret-safe Claude manual auth API and service behavior

Source Reference
Source Document: docs/ManagedAgents/ClaudeAnthropicOAuth.md
Source Title: MoonMind Design: claude_anthropic Settings Authentication (Repo-Backed)
Source Sections:
- 3. Design decision
- 4. Desired profile shape
- 6.1 Do not reuse /api/v1/oauth-sessions as-is
- 6.2 Add a separate manual-auth path
- 7. Secrets handling
- 10.2 Backend
Coverage IDs:
- DESIGN-REQ-002
- DESIGN-REQ-004
- DESIGN-REQ-006
- DESIGN-REQ-010
- DESIGN-REQ-012
- DESIGN-REQ-014

User Story
As Mission Control, I can submit a Claude Anthropic token to a dedicated manual-auth backend path that validates it, stores it as a Managed Secret, binds claude_anthropic to that secret, syncs provider profile state, and returns only secret-free readiness metadata.

Acceptance Criteria
- The Claude manual auth path does not require volume_ref, volume_mount_path, mounted Docker files, oauth_volume, or oauth_home finalization.
- Successful commit stores the token only in Managed Secrets and stores only secret references in the provider profile row.
- The returned response contains readiness, validation timestamp/status, secret existence, and profile readiness without returning the token.
- Invalid tokens, failed upstream validation, and unauthorized callers fail without leaking submitted token material.
- Tests prove no raw token appears in profile rows, workflow-shaped payloads, route responses, logs captured by the test, or validation failure messages.

Requirements
- Dedicated backend behavior must be separate from the existing volume-first oauth-sessions finalize path.
- The resulting profile must target credential_source=secret_ref and runtime_materialization_mode=api_key_env.
- Secret refs should include anthropic_api_key bound to a managed secret such as db://claude_anthropic_token.
- clear_env_keys must remove conflicting Anthropic/OpenAI keys before launch materialization.

Implementation Notes
- Preserve MM-447 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Scope the implementation to the backend manual-auth commit behavior for Claude Anthropic provider profiles.
- Use `docs/ManagedAgents/ClaudeAnthropicOAuth.md` as the source design reference for the provider-profile-backed manual token enrollment flow.
- Keep this path separate from `/api/v1/oauth-sessions` and its volume-first OAuth terminal finalization semantics.
- Store submitted token material only in Managed Secrets; provider profiles, workflow-shaped payloads, route responses, validation failures, notices, logs, and artifacts must remain secret-free.
- Bind `claude_anthropic` through a `secret_ref` profile shape that launches with `api_key_env` materialization and clears conflicting Anthropic/OpenAI environment variables before runtime launch.
- Sync provider profile state after a successful commit so runtime-visible provider profile data reflects the new secret reference and readiness metadata.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-447 is blocked by MM-448, whose embedded status is Selected for Development.
- Trusted Jira link metadata also shows MM-447 blocks MM-446, which is not a blocker for MM-447 and is ignored for dependency gating.

Needs Clarification
- None
