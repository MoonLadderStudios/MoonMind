# Feature Specification: Simplify Orchestrate Summary

**Feature Branch**: `193-simplify-orchestrate-summary`  
**Created**: 2026-04-16  
**Status**: Draft  
**Input**:

```text
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
```

**Implementation Intent**: Runtime implementation. Required deliverables include production behavior changes plus validation tests.

## User Story - Workflow-Owned Finish Summary

**Summary**: As a MoonMind operator, I want orchestration workflows to finish through one system-owned summary contract so that final run output is consistent across presets and terminal states.

**Goal**: Normal orchestration completion no longer depends on preset-authored final narrative report steps; the workflow finalizer remains the canonical surface for finish summaries while preset-specific facts continue to be available as structured outputs or artifacts.

**Independent Test**: Run or inspect the seeded Jira and MoonSpec orchestration preset definitions and workflow finalization behavior, then verify the presets no longer include generic final narrative report steps and terminal success, failure, cancellation, and no-change paths still produce one consistent finish summary contract.

**Acceptance Scenarios**:

1. **Given** a Jira orchestration preset reaches normal completion, **when** the workflow finishes, **then** the canonical finish summary is produced by workflow finalization rather than a Jira-specific final narrative step.
2. **Given** a MoonSpec orchestration preset reaches normal completion, **when** the workflow finishes, **then** the canonical finish summary is produced by workflow finalization rather than a separate orchestration report step.
3. **Given** a preset produces domain-specific facts such as Jira issue key, final status, PR URL, publish handoff, or outcome data, **when** the finalizer builds the finish summary, **then** those facts remain available through structured outputs or artifacts instead of being lost with report-step removal.
4. **Given** a workflow ends in success, failure, cancellation, or no-change, **when** the operator reviews the run outcome, **then** the same finish summary contract is available without depending on preset-authored report steps.
5. **Given** documentation describes orchestration summaries, **when** an operator reads it, **then** it distinguishes canonical workflow finish summaries from optional preset-specific structured outputs.

### Edge Cases

- A preset-specific step may currently be the only place where issue keys, PR URLs, publish references, or outcome classifications are narrated.
- A workflow may terminate before late preset steps run, but finalization still executes.
- A no-change result may skip implementation steps while still requiring a useful finish summary.
- Failure or cancellation may include partial structured output that should be summarized without implying successful completion.

## Assumptions

- Existing workflow finalization already produces the canonical finish summary artifact and remains the owner of generic end-of-run narration.
- Jira and MoonSpec orchestration presets can remove final report-only steps without removing structured outputs needed for workflow logic, operator visibility, or downstream handoff.
- Runtime behavior is the target; documentation updates are supporting evidence only.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST treat workflow finalization as the canonical owner of normal end-of-run summaries for orchestration workflows.
- **FR-002**: Jira orchestration presets MUST NOT include a final Jira-specific narrative report step whose only purpose is generic completion reporting.
- **FR-003**: MoonSpec orchestration presets MUST NOT include a separate final orchestration report step whose only purpose is generic completion reporting.
- **FR-004**: Preset-specific facts needed after execution, including Jira issue key, final status, PR URL, publish handoff, and outcome data, MUST remain available as structured outputs or artifacts.
- **FR-005**: Success, failure, cancellation, and no-change terminal paths MUST continue to produce one consistent finish summary contract.
- **FR-006**: Documentation MUST distinguish canonical workflow finish summaries from optional preset-specific structured outputs.
- **FR-007**: Moon Spec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key MM-366.

### Key Entities

- **Workflow Finish Summary**: The system-owned terminal summary contract produced during workflow finalization.
- **Orchestration Preset**: A seeded multi-step task template, such as Jira Orchestrate or MoonSpec Orchestrate, that defines agent execution steps and structured handoff behavior.
- **Preset Structured Output**: Domain-specific data emitted by preset steps for workflow logic, operator visibility, or downstream handoff without owning the generic run summary.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Tests prove `jira-orchestrate` contains no generic final Jira narrative report step while preserving required Jira-specific structured output references.
- **SC-002**: Tests prove `moonspec-orchestrate` contains no generic final orchestration report step while preserving required MoonSpec publish or outcome handoff references.
- **SC-003**: Tests prove workflow finalization summary generation remains available for success, failure, cancellation, and no-change outcomes.
- **SC-004**: Documentation or contract tests demonstrate the distinction between canonical finish summaries and optional preset-specific structured outputs.
- **SC-005**: Verification evidence confirms MM-366 and the original preset brief are preserved in the active Moon Spec artifacts and delivery metadata.
