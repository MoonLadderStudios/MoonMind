# MM-407 MoonSpec Orchestration Input

## Source

- Jira issue: MM-407
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Managed Runtime Skill Projection
- Labels: `moonmind-workflow-mm-84523417-cb8e-4e09-a152-7267f5d213c6`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-407 from MM project
Summary: Managed Runtime Skill Projection
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-407 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-407: Managed Runtime Skill Projection

Source Reference
- Source Document: docs/Tools/SkillSystem.md
- Source Title: MM-319: breakdown docs\Tools\SkillSystem.md
- Source Sections:
  - AgentSkillSystem §8
  - AgentSkillSystem §13-§16
  - SkillInjection §2-§16
  - ManagedAndExternalAgentExecutionModel §1-§2.5
- Coverage IDs:
  - DESIGN-REQ-005
  - DESIGN-REQ-011
  - DESIGN-REQ-012
  - DESIGN-REQ-013
  - DESIGN-REQ-014
  - DESIGN-REQ-015
  - DESIGN-REQ-016
  - DESIGN-REQ-017
  - DESIGN-REQ-021

User Story
As a managed runtime adapter, I can materialize a pinned skill snapshot into a run-scoped active backing store and expose exactly that selected set at .agents/skills with a compact activation summary, so agents see the expected path without MoonMind rewriting checked-in skill folders.

Acceptance Criteria
- Given a resolved snapshot with one skill, when the adapter prepares the runtime, then .agents/skills contains a full active root with _manifest.json and that skill's SKILL.md.
- Given a resolved snapshot with multiple skills, then only selected skills appear in the active projection and unselected repo skills are absent.
- Given a checked-in .agents/skills directory exists, then MoonMind may use it as a resolution input but does not rewrite it in place during runtime setup.
- Given .agents or .agents/skills is an incompatible file or unprojectable path, then preparation fails before runtime launch with path, object kind, attempted action, and remediation guidance.
- Given the runtime starts, then the instruction payload includes a compact activation summary and full skill bodies are available on disk, not duplicated inline.

Requirements
- Materialize the active skill bundle into a MoonMind-owned run-scoped backing directory exactly once per snapshot.
- Project the active backing store at .agents/skills for managed runtimes using adapter-compatible mechanics.
- Include only selected skills and a MoonMind-owned active manifest in the runtime-visible tree.
- Inject a compact activation summary naming active skills, visible path, hard rules, and first-read hints.
- Do not use retrieval-first loading, custom visible paths, or per-skill leaf mounting as the canonical managed-runtime path.

Relevant Implementation Notes
- Keep `.agents/skills` as the canonical runtime-visible path for the resolved active snapshot.
- Materialize the selected skill set into a MoonMind-owned run-scoped backing store before managed runtime launch.
- Expose exactly the selected active skill set at `.agents/skills`; unselected repo skills must be absent from the runtime-visible projection.
- Treat checked-in `.agents/skills` folders as resolution inputs only and do not rewrite them in place during runtime setup.
- Include a MoonMind-owned active manifest in the runtime-visible projection.
- Keep full skill bodies on disk and avoid duplicating large skill content inline in the instruction payload.
- Inject only a compact activation summary that names active skills, visible path, hard rules, and first-read hints.
- Fail before runtime launch when `.agents` or `.agents/skills` is an incompatible file or unprojectable path, with path, object kind, attempted action, and remediation guidance.

Verification
- Confirm a resolved snapshot with one skill materializes a full `.agents/skills` active root containing `_manifest.json` and that skill's `SKILL.md`.
- Confirm a resolved snapshot with multiple skills projects only selected skills and omits unselected repo skills.
- Confirm checked-in `.agents/skills` can be used as a resolution input without being rewritten in place during runtime setup.
- Confirm incompatible `.agents` or `.agents/skills` paths fail before runtime launch with actionable diagnostics.
- Confirm runtime instructions include a compact activation summary while full skill bodies are available on disk and not duplicated inline.
- Preserve MM-407 in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.

Dependencies
- MM-408 blocks this issue.
- MM-407 blocks MM-406.
