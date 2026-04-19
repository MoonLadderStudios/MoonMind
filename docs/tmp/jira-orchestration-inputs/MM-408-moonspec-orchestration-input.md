# MM-408 MoonSpec Orchestration Input

## Source

- Jira issue: MM-408
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Skill Runtime Observability and Verification
- Labels: `moonmind-workflow-mm-84523417-cb8e-4e09-a152-7267f5d213c6`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-408 from MM project
Summary: Skill Runtime Observability and Verification
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-408 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-408: Skill Runtime Observability and Verification

Source Reference
- Source Document: docs/Tools/SkillSystem.md
- Source Title: MM-319: breakdown docs\Tools\SkillSystem.md
- Source Sections:
  - AgentSkillSystem §18-§23
  - SkillInjection §15, §18-§19
  - docs/tmp/004-AgentSkillSystemPlan.md §2-§5
- Coverage IDs:
  - DESIGN-REQ-010
  - DESIGN-REQ-018
  - DESIGN-REQ-019
  - DESIGN-REQ-020

User Story
As an operator or maintainer, I can inspect which skills were selected, where they came from, how they were materialized, and whether projection succeeded, with boundary tests proving the real adapter and activity behavior rather than isolated helper behavior only.

Acceptance Criteria
- Given a task detail view or API response for a run with skills, then it exposes resolved snapshot ID, selected skill versions, source provenance, materialization mode, visible path summary, and manifest or prompt-index artifact refs where appropriate.
- Given a projection collision occurs, then operator-visible diagnostics include the path, object kind, attempted projection action, and remediation without dumping full skill bodies.
- Given proposal, schedule, or rerun metadata is inspected, then skill intent or resolved snapshot reuse is explicit and re-resolution is never silent.
- Given the skill injection implementation changes, then real adapter or activity boundary tests cover single-skill and multi-skill projections, read-only materialization, activation summary injection, collision failure, replay reuse, and repo-skill input without in-place mutation.

Requirements
- Surface resolved skill metadata in submit, detail, and debug contexts appropriate to operator permissions.
- Record active backing path, visible path, projected skills and versions, read-only state, collision failures, and activation summary evidence.
- Preserve artifact and payload discipline by linking manifests or prompt indexes instead of logging full bodies by default.
- Add or maintain boundary-level tests for adapter and activity behavior when skill injection or resolution behavior changes.

Relevant Implementation Notes
- Keep skill observability focused on metadata and artifact refs rather than full skill body content.
- Include selected skill versions, source provenance, materialization mode, runtime-visible path summary, active backing path, read-only state, and collision diagnostics where the existing submit, detail, debug, proposal, schedule, or rerun surfaces expose skill runtime state.
- Treat projection failures as operator-visible diagnostics with path, object kind, attempted projection action, and remediation guidance.
- Make skill intent or resolved snapshot reuse explicit for proposal, schedule, and rerun flows so re-resolution is not silent.
- Cover real adapter or activity boundaries when skill injection or resolution behavior changes, including single-skill projection, multi-skill projection, read-only materialization, activation summary injection, collision failure, replay reuse, and repo-skill input without in-place mutation.

Verification
- Confirm task detail or API responses for runs with skills expose resolved snapshot ID, selected skill versions, source provenance, materialization mode, visible path summary, and manifest or prompt-index artifact refs where appropriate.
- Confirm projection collision diagnostics include path, object kind, attempted projection action, and remediation without dumping full skill bodies.
- Confirm proposal, schedule, and rerun metadata make skill intent or resolved snapshot reuse explicit and do not silently re-resolve.
- Confirm boundary-level tests cover single-skill and multi-skill projections, read-only materialization, activation summary injection, collision failure, replay reuse, and repo-skill input without in-place mutation.
- Preserve MM-408 in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.

Dependencies
- Jira link metadata at fetch time indicates MM-408 blocks MM-407.
