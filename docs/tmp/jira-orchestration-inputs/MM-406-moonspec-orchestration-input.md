# MM-406 MoonSpec Orchestration Input

## Source

- Jira issue: MM-406
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Skill Selection and Snapshot Resolution
- Labels: `moonmind-workflow-mm-84523417-cb8e-4e09-a152-7267f5d213c6`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-406 from MM project
Summary: Skill Selection and Snapshot Resolution
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-406 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-406: Skill Selection and Snapshot Resolution

Source Reference
- Source Document: docs/Tools/SkillSystem.md
- Source Title: MM-319: breakdown docs\Tools\SkillSystem.md
- Source Sections:
  - AgentSkillSystem §10-§12
  - AgentSkillSystem §15
  - AgentSkillSystem §19
  - ManagedAndExternalAgentExecutionModel §2.5
- Coverage IDs:
  - DESIGN-REQ-006
  - DESIGN-REQ-007
  - DESIGN-REQ-008
  - DESIGN-REQ-009
  - DESIGN-REQ-010
  - DESIGN-REQ-019

User Story
As a task author, I can express task-wide and step-specific skill intent and have MoonMind resolve it into an immutable, artifact-backed snapshot before runtime launch, so retries, reruns, and audits reproduce the same skill set unless re-resolution is explicit.

Acceptance Criteria
- Given `task.skills` defines a baseline and `step.skills` excludes one inherited skill, when the step is prepared, then the resolved snapshot reflects the override without mutating task-level intent.
- Given a pinned skill version cannot be resolved, when resolution runs, then the workflow fails before runtime launch with an actionable validation error.
- Given a `ResolvedSkillSet` is produced, then workflow history contains only compact refs and metadata while large manifests, bodies, bundles, and source traces are artifact-backed.
- Given a retry, continue-as-new, or ordinary rerun occurs, then the same resolved snapshot is reused unless the caller explicitly requests re-resolution.

Requirements
- Collect task and step skill selectors before runtime launch.
- Resolve allowed source kinds through activity or service boundaries, not deterministic workflow code.
- Pin exact skill versions and write resolved manifest artifacts.
- Fail fast for missing required skills, unsatisfied pins, nondeterministic collisions, policy blocks, or runtime incompatibility.
- Preserve proposal, schedule, retry, rerun, and replay semantics for skill intent and resolved snapshots.

Relevant Implementation Notes
- Keep `.agents/skills` as the canonical active runtime-visible path for the resolved active snapshot.
- Treat checked-in repo skills and local-only skills as inputs to resolution, not as mutable runtime source-of-truth folders.
- Keep `.agents/skills/local` as a local-only overlay source.
- Do not embed large skill content in workflow history; workflows should carry compact refs and metadata while activities or services write artifact-backed manifests, bodies, bundles, and source traces.
- Skill source loading, resolution, manifest generation, and materialization belong at activity or service boundaries, not deterministic workflow code.
- Unsupported source kinds, missing required skills, unsatisfied pins, nondeterministic collisions, policy blocks, or runtime incompatibility should fail before runtime launch with actionable validation errors.

Verification
- Confirm task-level and step-level skill selector inputs are collected before runtime launch.
- Confirm step-level exclusions can override inherited task-level skill intent without mutating the task-level intent.
- Confirm pinned skill resolution failure stops before runtime launch with an actionable validation error.
- Confirm `ResolvedSkillSet` output stores large skill manifests, bodies, bundles, and source traces as artifact-backed data while workflow history retains only compact refs and metadata.
- Confirm retries, continue-as-new, and ordinary reruns reuse the same resolved snapshot unless explicit re-resolution is requested.
- Preserve MM-406 in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.

Dependencies
- MM-407 blocks this issue.
- MM-406 blocks MM-405.
