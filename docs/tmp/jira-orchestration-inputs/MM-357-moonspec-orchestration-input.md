# MM-357 MoonSpec Orchestration Input

## Source

- Jira issue: MM-357
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Per-Run Codex Home Seeding
- Labels: `mm-318`, `moonspec-breakdown`, `oauth-terminal`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-357 from MM project
Summary: Per-Run Codex Home Seeding
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-357 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-357: Per-Run Codex Home Seeding

MoonSpec Story ID: STORY-003

Short Name
codex-home-seeding

User Story
As a task operator, I can start Codex App Server from a per-run CODEX_HOME seeded one way from durable credentials without making the auth volume live runtime state.

Acceptance Criteria
- Eligible auth entries copy into codexHomePath before Codex App Server starts.
- Missing or invalid auth-volume paths fail with actionable errors.
- Excluded entries are not copied into the per-run home.
- Codex App Server uses per-run CODEX_HOME, not the durable auth-volume mount.
- Operator execution evidence is artifact-backed, not runtime-home-backed.

Requirements
- Create the per-run codexHomePath under the task workspace.
- Copy only eligible auth entries from MANAGED_AUTH_VOLUME_PATH into codexHomePath.
- Start Codex App Server with CODEX_HOME = codexHomePath.
- Keep runtime home directories out of operator/audit presentation.

Independent Test
Run the session runtime with a fake auth-volume directory and assert eligible files are copied, excluded files are not copied, and Codex App Server receives the per-run CODEX_HOME.

Dependencies
- STORY-002

Risks
- Eligible/excluded file policy must stay aligned with Codex CLI auth layout changes.

Out of Scope
- Deciding which provider profile is selected.
- OAuth enrollment and profile registration.
- Live Logs UI implementation.

Source Document
docs/ManagedAgents/OAuthTerminal.md

Source Sections
- 3.2 Shared task workspace volume
- 4. Volume Targeting Rules
- 7. Managed Codex Session Launch
- 10. Operator Behavior
- 11. Required Boundaries

Coverage IDs
- DESIGN-REQ-005
- DESIGN-REQ-007
- DESIGN-REQ-008
- DESIGN-REQ-010
- DESIGN-REQ-019
- DESIGN-REQ-020

Source Design Coverage
- DESIGN-REQ-005: Materialize per-task paths under agent_workspaces, including repo, session state, artifact spool, and .moonmind/codex-home.
- DESIGN-REQ-007: Seed eligible auth entries one way from the durable auth volume into the per-run Codex home before starting Codex App Server.
- DESIGN-REQ-008: Keep managed task execution on Codex App Server, not PTY attach or terminal scrollback.
- DESIGN-REQ-010: Never place raw credential contents in workflow history, logs, artifacts, or UI responses.
- DESIGN-REQ-019: Present Live Logs, artifacts, session summaries, diagnostics, and reset/control-boundary artifacts as execution evidence instead of runtime homes or auth volumes.
- DESIGN-REQ-020: Preserve ownership boundaries among OAuth terminal code, Provider Profile code, managed-session controller code, Codex session runtime code, and Docker workload orchestration.

Needs Clarification
- None
