# MM-356 MoonSpec Orchestration Input

## Source

- Jira issue: MM-356
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Codex Managed Session Volume Targeting
- Labels: `mm-318`, `moonspec-breakdown`, `oauth-terminal`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, or `presetBrief`.

## Canonical MoonSpec Feature Request

Jira issue: MM-356 from MM project
Summary: Codex Managed Session Volume Targeting
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-356 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-356: Codex Managed Session Volume Targeting

Story ID: STORY-002

Short Name
codex-volume-targeting

User Story
As a task operator, I can launch a managed Codex session that receives the workspace volume and only receives the durable auth volume at an explicit, separate auth target.

Acceptance Criteria
- The Docker command mounts agent_workspaces at /work/agent_jobs.
- Absent MANAGED_AUTH_VOLUME_PATH, codex_auth_volume is not mounted.
- Present MANAGED_AUTH_VOLUME_PATH, codex_auth_volume is mounted at that path and not at codexHomePath.
- MANAGED_AUTH_VOLUME_PATH equal to codexHomePath fails before container creation.
- Managed-session containers receive reserved MOONMIND_SESSION_* environment values.

Requirements
- Mount agent_workspaces into every managed Codex session container.
- Conditionally mount codex_auth_volume only when explicitly set by selected profile or launcher policy.
- Reject auth-volume targets that equal codexHomePath.
- Pass reserved session environment values into the container.

Independent Test
Launch a managed Codex session with and without MANAGED_AUTH_VOLUME_PATH and inspect the generated Docker command plus validation failures for invalid mount targets.

Dependencies
- STORY-001

Risks
- Dynamic future auth mount paths must remain fail-fast and path-normalized.

Out of Scope
- OAuth terminal enrollment flow.
- Credential verification implementation.
- Workload container launches.

Source Document
docs/ManagedAgents/OAuthTerminal.md

Source Sections
- 3.2 Shared task workspace volume
- 3.3 Explicit auth-volume target
- 4. Volume Targeting Rules
- 7. Managed Codex Session Launch
- 11. Required Boundaries

Coverage IDs
- DESIGN-REQ-004
- DESIGN-REQ-005
- DESIGN-REQ-006
- DESIGN-REQ-017
- DESIGN-REQ-020

Source Design Coverage
- DESIGN-REQ-004: Treat agent_workspaces as the required managed-session workspace volume mounted at /work/agent_jobs.
- DESIGN-REQ-005: Materialize per-task paths under agent_workspaces, including repo, session state, artifact spool, and .moonmind/codex-home.
- DESIGN-REQ-006: Mount auth volumes into managed Codex sessions only through an explicit MANAGED_AUTH_VOLUME_PATH, separate from codexHomePath.
- DESIGN-REQ-017: Launch managed Codex session containers with required workspace mount, conditional auth-volume mount, and reserved session environment values.
- DESIGN-REQ-020: Preserve ownership boundaries among OAuth terminal code, Provider Profile code, managed-session controller code, Codex session runtime code, and Docker workload orchestration.

Needs Clarification
- None
