# MM-405 MoonSpec Orchestration Input

## Source

- Jira issue: MM-405
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Agent Skill Catalog and Source Policy
- Labels: moonmind-workflow-mm-84523417-cb8e-4e09-a152-7267f5d213c6
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-405 from MM project
Summary: Agent Skill Catalog and Source Policy
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-405 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-405: Agent Skill Catalog and Source Policy

Source Reference
- Source Document: docs/Tasks/AgentSkillSystem.md
- Original Jira Source Document: docs/Tools/SkillSystem.md
- Source Title: MM-319: breakdown docs\Tools\SkillSystem.md
- Source Sections:
  - AgentSkillSystem §1-§7
  - AgentSkillSystem §9
  - AgentSkillSystem §17
  - SkillAndPlanContracts §1.1
- Coverage IDs:
  - DESIGN-REQ-001
  - DESIGN-REQ-002
  - DESIGN-REQ-003
  - DESIGN-REQ-004

User Story
As a MoonMind operator, I can rely on agent skills being modeled as deployment-scoped, versioned instruction data with explicit source precedence and policy gates, so managed runs do not confuse instruction bundles with executable tools or silently trust repo-local content.

Acceptance Criteria
- Given executable tools and agent instruction bundles share the historical word skill, when contracts are validated, then ToolDefinition, runtime command, AgentSkillDefinition, SkillSet, and ResolvedSkillSet remain typed separately.
- Given a deployment-stored skill is edited, when the change is saved, then a new immutable version is created instead of mutating the previous version.
- Given built-in, deployment, repo, and local-only candidates exist, when policy allows them, then deterministic precedence selects the winner and records source provenance.
- Given policy forbids repo or local-only skills, when those sources contain candidates, then they are excluded before precedence is applied and cannot silently affect the run.

Requirements
- Define or preserve concrete AgentSkillDefinition, AgentSkillVersion, SkillSet, and SkillSetEntry contracts with source kind and version metadata.
- Keep executable ToolDefinition and runtime-native command contracts separate from agent-skill instruction bundles in validation and docs.
- Apply deployment policy gates to repo and local-only skill sources before selection or materialization.
- Treat repo-provided and local-only skill content as potentially untrusted input.
- Preserve MM-405 in all downstream MoonSpec artifacts, verification output, commit text, and pull request metadata.

Relevant Implementation Notes
- The canonical agent-skill design lives in `docs/Tasks/AgentSkillSystem.md`; executable tool contracts live separately in `docs/Tasks/SkillAndPlanContracts.md`.
- Agent skills are reusable instruction bundles and are not executable Temporal tools.
- ToolDefinition, runtime command, AgentSkillDefinition, SkillSet, and ResolvedSkillSet should remain typed separately at validation and API boundaries.
- Built-in, deployment-stored, repo-checked-in, and local-only skill candidates should be handled as distinct sources with explicit provenance.
- Deployment policy should gate repo and local-only sources before selection, resolution, or materialization.
- Runtime-visible `.agents/skills` should represent the active resolved skill set for a run, while `.agents/skills/local` remains a local-only overlay input.
- Resolved skill sets should be immutable per run or step, and workflows should carry refs to snapshots rather than large skill bodies.
- Runtime adapters own materialization of the resolved skill snapshot for each target runtime.
- Checked-in skill folders should not be mutated in place during runtime setup.

Verification
- Verify executable tool contracts and agent-skill instruction-bundle contracts remain distinguishable in models, validation, tests, and docs touched by the change.
- Verify deployment-backed skill edits create immutable versions rather than mutating previous versions.
- Verify source precedence records provenance when built-in, deployment, repo, and local-only candidates are present.
- Verify policy-denied repo and local-only skills are excluded before precedence is applied.
- Verify trusted or untrusted skill source handling is covered at the workflow/activity or adapter boundary when runtime materialization is changed.
- Preserve MM-405 in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.

Dependencies
- Jira link metadata at fetch time indicates MM-405 is blocked by MM-406.
