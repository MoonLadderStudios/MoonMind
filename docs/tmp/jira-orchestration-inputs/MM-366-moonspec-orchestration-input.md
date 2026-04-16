# MM-366 MoonSpec Orchestration Input

## Source

- Jira issue: MM-366
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Simplify Orchestrate Summary
- Labels: None
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-366 from MM project
Summary: Simplify Orchestrate Summary
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-366 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-366: Simplify Orchestrate Summary

Title
Replace preset-specific orchestration report steps with a workflow-owned finish summary contract

User Story
As a MoonMind operator, I want every workflow to end with the same system-owned finish summary, so that I can rely on one canonical summary surface instead of preset-specific agent-authored report steps.

Problem
Some seeded orchestration presets currently add their own final report step, such as:

- `jira-orchestrate` returning a Jira-specific orchestration report.
- `moonspec-orchestrate` returning an orchestration report.

At the same time, MoonMind already has a workflow finalization path that produces the canonical finish summary artifact. This splits summary ownership between preset steps and the workflow finalizer.

This causes:

- Inconsistent summary behavior across presets.
- Duplicated summary concepts.
- Risk of drift between preset-authored reports and the canonical finish summary.
- Weaker behavior on failure/cancel paths, where a preset step may not run but the finalizer still can.

Goal
Make the workflow finalizer the canonical owner of end-of-run summaries for all workflows.

Desired State
- Generic workflow summaries are produced by workflow finalization, not by a preset-authored final prompt step.
- Presets no longer add final report steps just to narrate workflow completion.
- If a preset needs domain-specific outputs, those are preserved as structured outputs or artifacts rather than as the canonical workflow summary.
- The finalizer may consume structured workflow outputs so the generic finish summary can still show important preset-specific facts.

Acceptance Criteria
- MoonMind.Run / workflow finalization is the canonical owner of end-of-run summaries.
- Normal workflow summaries no longer depend on preset-authored final report steps.
- `jira-orchestrate` no longer ends with a Jira-specific narrative summary step for generic completion reporting.
- `moonspec-orchestrate` no longer ends with a separate orchestration report step for generic completion reporting.
- Any preset-specific facts still needed after execution are preserved through structured outputs or artifacts.
- Terminal success, failure, cancellation, and no-change paths continue to produce one consistent finish summary contract.
- Docs clearly distinguish between the canonical workflow finish summary and optional preset-specific structured outputs.

Validation
- Verify canonical finish summary artifacts still appear for successful orchestration runs after preset report-step removal.
- Verify failure/cancel paths still produce useful summary output without relying on preset-authored report steps.
- Verify Jira Orchestrate still preserves Jira-specific data needed for operator visibility and downstream reasoning.
- Verify MoonSpec Orchestrate still preserves publish handoff and outcome data without a separate final report step.

Relevant Implementation Notes
- Likely affected areas include `api_service/data/task_step_templates/jira-orchestrate.yaml`, `api_service/data/task_step_templates/moonspec-orchestrate.yaml`, `moonmind/workflows/temporal/workflows/run.py`, finish summary / run summary contract surfaces, and related docs describing orchestration summaries.
- Remove final preset report steps whose only purpose is generic completion reporting.
- Preserve preset-specific structured outputs where needed.
- Allow the finalizer to incorporate structured workflow outputs into the canonical finish summary.

Non-Goals
- Removing structured preset-specific outputs that are still needed for workflow logic or handoff.
- Removing Jira-specific data such as issue key, final status, or PR URL from workflow outputs.
- Redesigning the broader finish-summary system from scratch.

Needs Clarification
- None
