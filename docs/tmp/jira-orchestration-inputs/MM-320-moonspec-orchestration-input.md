# MM-320 MoonSpec Orchestration Input

## Source

- Jira issue: MM-320
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Better gh secret handling
- Priority: Medium
- Labels: `secrets-system`, `managed-sessions`, `github-auth`, `temporal`, `security`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: Synthesized from the trusted `jira.get_issue` MCP response because the response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-320 from MM project
Summary: Better gh secret handling
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-320 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-320: Better gh secret handling

Issue Brief
- Preset issue type from Jira description: Task
- Preset summary from Jira description: Align managed-session GitHub auth with Secrets System launch materialization model

Description
MoonMind currently supports managed-session GitHub clone auth by resolving `GITHUB_TOKEN` before `agent_runtime.launch_session` and materializing it into host-side git subprocesses through an environment-scoped credential helper.

This fixes the immediate clone failure, but the long-term implementation should align more directly with `docs/Security/SecretsSystem.md`: durable contracts should carry secret references or materialization descriptors, not raw secret values, and runtime launch should produce auditable, scoped materialization events.

Current Context
- Immediate fix landed in commit `edbe2708`.
- The current launch path consumes an already-materialized `GITHUB_TOKEN` from `LaunchCodexManagedSessionRequest.environment`.
- The desired-state system calls for `SecretRef`-based durable contracts, late resolution, narrow runtime materialization, redaction-safe diagnostics, and observability for when credentials are materialized.

Objective
Refactor managed-session GitHub authentication so Codex session launch receives a reference-based GitHub credential contract and resolves/materializes the credential only at the controlled runtime launch boundary.

Requirements
- Add or reuse a durable `SecretRef` or materialization descriptor for managed-session GitHub credentials instead of relying on raw `GITHUB_TOKEN` in launch request environment.
- Preserve local-first behavior: UI-managed `GITHUB_TOKEN` and `GITHUB_PAT` secrets must continue to support private repo clone and push.
- Resolve the GitHub credential inside the activity/service boundary immediately before git workspace preparation.
- Materialize credentials only for the host-side git subprocesses that need them.
- Do not persist raw GitHub credentials in workflow history, task payloads, run metadata, artifacts, logs, or generated durable config.
- Emit redaction-safe diagnostics for missing, revoked, or unresolvable GitHub credentials.
- Add operator-visible audit or metadata indicating that GitHub credentials were materialized for runtime launch, without exposing the value.
- Keep the existing username-free repo input model: owner/repo, URL, or local path only. Do not require operators to specify GitHub usernames in workflow input.

Relevant Implementation Notes
- Relevant current paths:
  - `moonmind/workflows/temporal/activity_runtime.py`
  - `moonmind/workflows/temporal/runtime/managed_api_key_resolve.py`
  - `moonmind/workflows/temporal/runtime/managed_session_controller.py`
  - `moonmind/schemas/managed_session_models.py`
- Consider extending `LaunchCodexManagedSessionRequest` with a compact credential/materialization reference rather than adding more raw environment payload fields.
- Keep compatibility-sensitive Temporal payload handling in mind. If the launch contract changes, preserve worker-bound invocation compatibility or add an explicit cutover plan.
- The current environment-scoped credential helper can remain as the final subprocess materialization mechanism if driven by a resolved launch-scoped secret value.

Acceptance Criteria
- Managed-session clone of a private GitHub repo succeeds without prior `gh auth setup-git` for the worker Unix user.
- Temporal workflow payloads and histories contain no raw GitHub token values.
- Launch request durable data contains only a secret reference or non-sensitive materialization descriptor.
- Missing or revoked GitHub credential fails before clone with a clear, actionable, redaction-safe error.
- Git clone/fetch/push failure messages redact secret-like values.
- Audit/diagnostic metadata records that GitHub credential materialization was required for the run without exposing the credential.
- Existing local-first managed secret behavior continues to work for `GITHUB_TOKEN` and `GITHUB_PAT`.
- Unit tests cover the activity/controller boundary and redaction.
- At least one workflow/activity boundary test covers the real managed-session launch invocation shape.

Verification
- Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`.
- Run targeted managed-session controller tests.
- Add or update Temporal activity boundary tests for launch credential references.
- Perform a manual or hermetic integration check with a private GitHub repo credential where feasible.

Source Traceability
- The issue references the desired-state security model in `docs/Security/SecretsSystem.md`.
- Preserve MM-320 in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
