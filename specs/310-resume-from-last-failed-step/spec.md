# Feature Specification: Resume from Last Failed Step

**Feature Branch**: `310-resume-from-last-failed-step`  
**Created**: 2026-05-07  
**Status**: Draft  
**Input**: User description: """
Use the Jira preset brief for MM-602 as the canonical Moon Spec orchestration input.

Additional constraints:


Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-602 MoonSpec Orchestration Input

## Source

- Jira issue: MM-602
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Implement resume from last failed step
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.
- Trusted response artifact: `/work/agent_jobs/mm:222b2e78-d472-440c-8bff-8e20c3cfd8f8/artifacts/moonspec-inputs/MM-602-trusted-jira-get-issue.json`

## Canonical MoonSpec Feature Request

Jira issue: MM-602 from MM project
Summary: Implement resume from last failed step
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-602 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-602: Implement resume from last failed step

As a MoonMind operator recovering a failed task, I want to resume from the last failed step while preserving completed prior work, so I do not have to rerun successful setup or implementation steps.

Acceptance Criteria
1. Failed task details expose Resume only when backend capability canResumeFromFailedStep is true.
2. Resume is distinct from Edit task, Rerun, and paused-task lifecycle resume.
3. Resume uses the original task input snapshot unchanged and rejects edited instructions, steps, attachments, runtime, publish mode, branch, presets, dependencies, or model settings.
4. Resume pins the source workflowId and runId, identifies the last failed step from the source step ledger, and requires durable output refs for all preserved prior steps.
5. Resume requires a resume checkpoint containing the original task snapshot ref, plan ref or digest when available, failed step identity and attempt, preserved step refs, prepared input refs, and workspace, branch, commit, or equivalent state before the failed step.
6. A resumed execution creates linked follow-up execution provenance, materializes preserved prior steps as reused from the source run, starts new work at the failed step, and leaves the original failed execution unchanged.
7. If checkpoint validation, authorization, plan matching, workspace restoration, or preserved-output injection fails, Resume fails explicitly before executing the failed step and must not silently degrade to a full rerun.
8. Task details and related-runs UI show resumed executions, preserved prior steps, disabled Resume reasons, confirmation copy, success toast copy, and relationship label Resumed from failed step.
9. Workflow/activity or adapter-boundary tests cover resume eligibility, checkpoint validation, preserved step materialization, failed restoration behavior, and UI capability rendering.

Relevant Implementation Notes
- Canonical docs already define desired-state behavior for resume semantics and should be used as the source contract.
- The implementation should preserve large checkpoint and step content by refs rather than embedding it into workflow history.
- Resume source identity must include both source workflowId and source runId to avoid drift.

Out of Scope
- Editing task input as part of Resume.
- Generic RequestRerun behavior changes beyond what is required to keep Resume distinct.
- A full historical per-run product surface.

Source Documents
- docs/Tasks/TaskArchitecture.md
- docs/UI/TaskDetailsPage.md
- docs/Temporal/RunHistoryAndRerunSemantics.md
- docs/Temporal/StepLedgerAndProgressModel.md

## MoonSpec Classification Input

Classify this as a single-story runtime feature request for failed task recovery: implement resume from the last failed step while preserving completed prior work, original task inputs, durable output refs, source workflow/run identity, checkpoint validation, explicit failure behavior, follow-up execution provenance, and task-details UI affordances, with workflow/activity or adapter-boundary coverage.

## Canonical Jira Orchestrate Preset Invocation

```json
{
  "type": "preset",
  "preset_id": "jira-orchestrate",
  "title": "Jira Orchestrate",
  "inputs": {
    "jira_issue_key": "MM-602",
    "source_design_path": "",
    "constraints": "",
    "jira_issue": {
      "key": "MM-602",
      "summary": "Implement resume from last failed step",
      "description": "User Story\nAs a MoonMind operator recovering a failed task, I want to resume from the last failed step while preserving completed prior work, so I do not have to rerun successful setup or implementation steps.\n\n\nAcceptance Criteria\n1. Failed task details expose Resume only when backend capability canResumeFromFailedStep is true.\n2. Resume is distinct from Edit task, Rerun, and paused-task lifecycle resume.\n3. Resume uses the original task input snapshot unchanged and rejects edited instructions, steps, attachments, runtime, publish mode, branch, presets, dependencies, or model settings.\n4. Resume pins the source workflowId and runId, identifies the last failed step from the source step ledger, and requires durable output refs for all preserved prior steps.\n5. Resume requires a resume checkpoint containing the original task snapshot ref, plan ref or digest when available, failed step identity and attempt, preserved step refs, prepared input refs, and workspace, branch, commit, or equivalent state before the failed step.\n6. A resumed execution creates linked follow-up execution provenance, materializes preserved prior steps as reused from the source run, starts new work at the failed step, and leaves the original failed execution unchanged.\n7. If checkpoint validation, authorization, plan matching, workspace restoration, or preserved-output injection fails, Resume fails explicitly before executing the failed step and must not silently degrade to a full rerun.\n8. Task details and related-runs UI show resumed executions, preserved prior steps, disabled Resume reasons, confirmation copy, success toast copy, and relationship label Resumed from failed step.\n9. Workflow/activity or adapter-boundary tests cover resume eligibility, checkpoint validation, preserved step materialization, failed restoration behavior, and UI capability rendering.\n\n\nNotes\n- Canonical docs already define desired-state behavior for resume semantics and should be used as the source contract.\n- The implementation should preserve large checkpoint and step content by refs rather than embedding it into workflow history.\n- Resume source identity must include both source workflowId and source runId to avoid drift.\n\n\nOut of Scope\n- Editing task input as part of Resume.\n- Generic RequestRerun behavior changes beyond what is required to keep Resume distinct.\n- A full historical per-run product surface.\n\n\nSource Documents\n- docs/Tasks/TaskArchitecture.md\n- docs/UI/TaskDetailsPage.md\n- docs/Temporal/RunHistoryAndRerunSemantics.md\n- docs/Temporal/StepLedgerAndProgressModel.md",
      "status": "In Progress"
    },
    "canonical_moonspec_input_ref": "/work/agent_jobs/mm:222b2e78-d472-440c-8bff-8e20c3cfd8f8/artifacts/moonspec-inputs/MM-602-moonspec-orchestration-input.md"
  },
  "expansion_state": "not_expanded"
}
```

## Orchestration Constraints

- Jira Orchestrate always runs as a runtime implementation workflow.
- If the brief points at an implementation document, treat it as runtime source requirements.
- Source design path is unavailable in the trusted Jira response; use the source documents listed above as reference docs during later MoonSpec stages.
"""

## User Story - Resume Failed Task Progress

**Summary**: As a MoonMind operator recovering a failed task, I want to resume from the last failed step while preserving completed prior work so that I do not have to rerun successful setup or implementation steps.

**Goal**: Let an operator start a linked follow-up execution from the last failed step only when MoonMind can prove that the original inputs, completed-step outputs, prepared inputs, and workspace or branch state needed for a truthful resume are durable and authorized.

**Independent Test**: Can be fully tested by opening a failed task with checkpointed completed progress, choosing **Resume**, confirming that a linked follow-up execution starts at the failed step with prior steps shown as preserved, and verifying that failed or incomplete checkpoints prevent any new step execution.

**Acceptance Scenarios**:

1. **Given** a failed task whose backend capability says failed-step Resume is available, **When** the operator views task details, **Then** the page shows a distinct **Resume** action with accessible intent "Resume from failed step" alongside other valid failed-task actions.
2. **Given** the operator chooses Resume for an eligible failed task, **When** the resume request is accepted, **Then** the system creates a linked follow-up execution, preserves the source execution unchanged, pins both source workflow identity and run identity, and labels the relationship `Resumed from failed step`.
3. **Given** a resumed execution is created, **When** it starts work, **Then** completed prior steps are materialized as preserved from the source run, the first newly executed step is the last failed step, and later steps proceed only after that failed step succeeds.
4. **Given** the source task has original instructions, steps, attachments, runtime, publish mode, branch, presets, dependencies, and model settings, **When** Resume is requested, **Then** the resumed execution uses the original task input snapshot unchanged and rejects any edited task payload values.
5. **Given** checkpoint validation, authorization, plan matching, workspace restoration, or preserved-output injection fails, **When** Resume is requested or initialized, **Then** the failure is explicit before the failed step executes and the system does not silently degrade into a full rerun.
6. **Given** a failed task lacks recoverable output refs, prepared input refs, or workspace, branch, commit, or equivalent state needed to preserve prior work, **When** the operator views task details, **Then** Resume is unavailable and the page exposes a clear disabled reason or omits the action according to the capability contract.
7. **Given** a source or resumed task detail page has related runs, **When** the operator inspects those runs, **Then** source and resumed executions cross-reference each other with the `Resumed from failed step` relationship and preserved prior steps are not presented as newly executed by the resumed run.

### Edge Cases

- Source execution is not terminal failed or otherwise explicitly resume-eligible.
- Source run identity is missing, ambiguous, stale, or no longer matches the checkpoint evidence.
- The last failed step cannot be identified from the source step ledger.
- The source task input snapshot is missing, inaccessible, or inconsistent with the checkpoint.
- A source plan ref or digest exists but does not match the checkpoint or resumed execution context.
- Completed prior steps exist but one or more required output refs are missing, unauthorized, corrupted, or insufficient for downstream steps.
- The task mutates workspace or branch state but no checkpoint exists immediately before the failed step.
- The operator attempts to change instructions, steps, attachments, runtime, publish mode, branch, presets, dependencies, or model settings while using Resume.
- Resume is confused with paused-task lifecycle resume, exact full rerun, or edited full retry.
- A duplicate Resume request is submitted for the same source run and failed-step checkpoint.

## Assumptions

- Resume confirmation and success feedback may use the existing task action patterns as long as the user-visible intent remains distinct from Rerun, Edit task, and paused-task lifecycle Resume.
- The source documents listed in the MM-602 Jira brief are the canonical runtime source requirements for this single story; no separate source design path was provided.
- A future broader run-history product surface is out of scope; only the relationship and detail behavior required to make failed-step Resume understandable is included.

## Source Design Requirements

| ID | Source | Requirement Summary | Scope | Mapped Requirement |
|----|--------|---------------------|-------|--------------------|
| DESIGN-REQ-001 | `docs/Tasks/TaskArchitecture.md` lines 338-341 | Failed-step Resume must be a distinct task intent that restores completed progress from a resume checkpoint, starts at the failed step, pins source workflow and run identity, and treats checkpoint refs as execution state rather than editable authoring fields. | In scope | FR-001, FR-002, FR-003, FR-004, FR-006 |
| DESIGN-REQ-002 | `docs/Tasks/TaskArchitecture.md` lines 397-411 | Resume must not allow task input edits, must identify the last failed step, preserve completed prior work, retry the failed step as new work, continue later steps normally, and fail explicitly when restoration is invalid. | In scope | FR-002, FR-003, FR-004, FR-005, FR-006, FR-007 |
| DESIGN-REQ-003 | `docs/Tasks/TaskArchitecture.md` lines 510-531 | Resume checkpoints and execution must preserve prepared refs, output refs, workspace or branch state, and source provenance, and must not silently re-execute preserved steps or fall back to full rerun. | In scope | FR-003, FR-005, FR-006, FR-007 |
| DESIGN-REQ-004 | `docs/Tasks/TaskArchitecture.md` lines 639-643 | The canonical task workflow produces step ledger state and resume checkpoints and may start at a failed step only with a validated resume checkpoint. | In scope | FR-002, FR-005, FR-006 |
| DESIGN-REQ-005 | `docs/UI/TaskDetailsPage.md` lines 53-61 and 150-160 | Task details capability data must include `canResumeFromFailedStep`, and failed task actions expose Resume only when checkpointed progress is restorable without mutating the original failed execution. | In scope | FR-001, FR-009 |
| DESIGN-REQ-006 | `docs/UI/TaskDetailsPage.md` lines 1059-1071 | Clicking Resume must create a linked follow-up execution from the original snapshot, pin source identity, restore prior work, start at the last failed step, preserve the original execution, provide completion feedback, and label the relationship. | In scope | FR-002, FR-003, FR-004, FR-008, FR-009 |
| DESIGN-REQ-007 | `docs/UI/TaskDetailsPage.md` lines 1073-1079 | Task details must not mutate terminal executions, hide valid failed-task actions because remediation exists, or reconstruct edit state from display-only labels when a canonical draft exists. | In scope | FR-001, FR-004, FR-009 |
| DESIGN-REQ-008 | `docs/Temporal/RunHistoryAndRerunSemantics.md` lines 248-257 | Resume must require source identity, explicit resume eligibility, last failed step identity, authoritative task snapshot, plan evidence when present, restorable checkpoint evidence, a linked follow-up execution, unchanged source execution, and a resume relationship. | In scope | FR-002, FR-003, FR-005, FR-006, FR-008 |
| DESIGN-REQ-009 | `docs/Temporal/RunHistoryAndRerunSemantics.md` lines 261-291 | Resume must reject edited task payloads, pin source run identity, require checkpoint evidence with minimum required source, step, output, prepared-input, and workspace state fields, and fail before new execution when checkpoint validation fails. | In scope | FR-004, FR-005, FR-006, FR-007 |
| DESIGN-REQ-010 | `docs/Temporal/RunHistoryAndRerunSemantics.md` lines 293-302 | Source and resumed details must cross-link related runs and display preserved prior steps as reused from the source run. | In scope | FR-008, FR-009 |
| DESIGN-REQ-011 | `docs/Temporal/RunHistoryAndRerunSemantics.md` lines 410-415 | Resume checkpoint evidence must be preserved through durable artifacts or a dedicated durable read model keyed by source workflow, source run, logical step, and attempt. | In scope | FR-005, FR-006, FR-007 |
| DESIGN-REQ-012 | `docs/Temporal/StepLedgerAndProgressModel.md` lines 351-405 | Resume checkpoints are durable evidence and must identify source workflow and run, failed step, preserved prior steps, sufficient output refs, prepared input refs, and workspace or branch state before the failed step. | In scope | FR-005, FR-006 |
| DESIGN-REQ-013 | `docs/Temporal/StepLedgerAndProgressModel.md` lines 424-452 | Step truth and resume checkpoint projections must remain downstream of workflow state and artifact linkage, and user-visible updates should occur when checkpoints are created or validated and preserved step rows are materialized. | In scope | FR-007, FR-010 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST expose failed-step Resume as a distinct failed-task action only when backend capability data confirms checkpointed progress is restorable.
- **FR-002**: The system MUST create a linked follow-up execution for Resume, leave the original failed execution unchanged, and preserve a relationship from the resumed execution back to the source execution.
- **FR-003**: The system MUST pin the Resume source with both source workflow identity and source run identity and use those values when validating checkpoint evidence and rendering provenance.
- **FR-004**: The system MUST use the original task input snapshot unchanged for Resume and MUST reject edited instructions, steps, attachments, runtime, publish mode, branch, presets, dependencies, model settings, or other task payload changes submitted through Resume.
- **FR-005**: The system MUST require a resume checkpoint or equivalent durable evidence that identifies the source execution, task snapshot, plan evidence when available, failed step identity and attempt, completed prior steps, preserved output refs, prepared input refs, and workspace, branch, commit, or equivalent state before the failed step.
- **FR-006**: The system MUST validate resume eligibility, authorization, source identity, task snapshot consistency, plan matching when applicable, preserved output availability, prepared input availability, and workspace or branch restoration before executing the failed step.
- **FR-007**: The system MUST fail Resume explicitly before new step execution when checkpoint evidence is missing, unauthorized, corrupted, stale, plan-mismatched, insufficient for downstream contracts, or cannot restore required workspace or branch state.
- **FR-008**: A resumed execution MUST materialize completed prior steps as preserved from the source run, start newly executed work at the source run's last failed step, and run later steps only after that failed step succeeds.
- **FR-009**: Task details MUST show Resume affordances, disabled or unavailable Resume reasons, confirmation and success feedback, related-run links, and the relationship label `Resumed from failed step` in a way that distinguishes failed-step Resume from Edit task, Rerun, remediation, and paused-task lifecycle Resume.
- **FR-010**: Operator-visible task progress and related-run data MUST make checkpoint creation or validation and preserved-step materialization diagnosable without requiring operators to inspect low-level worker internals.
- **FR-011**: The feature MUST include workflow/activity or adapter-boundary validation covering resume eligibility, checkpoint validation, preserved step materialization, failed restoration behavior, and UI capability rendering.
- **FR-012**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `MM-602` and the original Jira preset brief for traceability.

### Key Entities *(include if feature involves data)*

- **Resume Capability**: The task-detail capability state that determines whether failed-step Resume is available and why it may be unavailable.
- **Source Execution Identity**: The source workflow identity and run identity for the failed execution being resumed.
- **Resume Checkpoint**: Durable evidence that records source identity, source task snapshot, plan evidence when available, failed step identity, preserved prior steps, prepared inputs, output refs, and workspace or branch state before the failed step.
- **Preserved Step**: A completed prior step represented in the resumed execution as reused from the source run, including source provenance and output refs.
- **Resumed Execution Relationship**: The relationship linking the resumed follow-up execution to the original failed execution with the label `Resumed from failed step`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In 100% of eligible failed-task detail views with restorable checkpointed progress, operators can identify and initiate failed-step Resume as a distinct action without using Edit task or Rerun.
- **SC-002**: In 100% of successful Resume requests, the source execution remains unchanged and the resumed execution records both source workflow identity and source run identity.
- **SC-003**: In 100% of resumed executions, completed prior steps before the failed step are shown as preserved from the source run and are not counted as newly executed by the resumed run.
- **SC-004**: In 100% of invalid checkpoint, authorization, plan-mismatch, missing-output, or restoration-failure cases, Resume fails before any new failed-step work begins and does not fall back to a full rerun.
- **SC-005**: In 100% of Resume attempts that include edited task payload values, the request is rejected and the operator is directed to edited full retry for changes.
- **SC-006**: In 100% of source and resumed task-detail views after Resume succeeds, related runs include the `Resumed from failed step` relationship and allow the operator to navigate between the source and resumed executions.
- **SC-007**: Boundary-level validation covers at least one successful Resume path, one ineligible capability path, one invalid checkpoint path, one edited-input rejection path, and one task-detail rendering path.
- **SC-008**: Final verification evidence preserves `MM-602`, the original Jira preset brief, and mappings for DESIGN-REQ-001 through DESIGN-REQ-013.
