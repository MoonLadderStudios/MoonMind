# Feature Specification: Execute Resume From the Failed Step Only

**Feature Branch**: `328-execute-resume-failed-step`
**Created**: 2026-05-08
**Status**: Draft
**Input**: User description: """
Use the Jira preset brief for MM-634 as the canonical Moon Spec orchestration input.

Additional constraints:


Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-634 MoonSpec Orchestration Input

## Source

- Jira issue: MM-634
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Execute Resume from the failed step only
- Priority: Medium
- Labels: `moonmind-workflow-mm-86f66178-893d-469b-ba39-7bf1a3a19bb6`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira issue fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, `presetInstructions`, or `recommendedPresetInstructions`; potentially related custom fields `Implementation plan`, `Backout plan`, `Test plan`, and `Source` were present but empty.

## Canonical MoonSpec Feature Request

Jira issue: MM-634 from MM project
Summary: Execute Resume from the failed step only
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-634 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-634: Execute Resume from the failed step only

Source Reference
Source Document: docs/Tasks/TaskArchitecture.md
Source Title: Task Architecture (Control Plane)
Source Sections:
- 3.6 Failed-step resume is not full rerun
- 7.3 Resume from failed step
- 8.6 Resume execution responsibilities
- 11 Invariants
- 12.1 MoonMind.Run

Coverage IDs:
- DESIGN-REQ-014
- DESIGN-REQ-015
- DESIGN-REQ-016
- DESIGN-REQ-017

As a user pressing Resume, I want MoonMind to restore completed work, retry the last failed step, and continue later steps without silently rerunning preserved steps or changing original input.

Acceptance Criteria:
- Resume uses the original task input snapshot unchanged and exposes no editable authoring form in v1.
- The resumed execution validates checkpoint source, snapshot, and plan identity before executing any step.
- The runtime restores workspace/branch/commit state immediately before the failed step.
- Completed prior steps are preserved with source workflowId, runId, logical step ID, and attempt provenance.
- Preserved outputs are injected so failed and downstream steps observe continuous-run contracts.
- The failed step is retried as the first newly executed step and later steps execute normally.
- Restoration failure does not fall back to full rerun or re-execute preserved steps.

Requirements:
- Resume cannot edit or silently mutate instructions, steps, attachments, runtime, publish mode, branch, dependencies, or preset metadata.
- A resumed execution imports completed prior steps, retries the failed step first, and continues later steps normally.
- Missing, stale, unauthorized, corrupted, or inconsistent resume evidence must block Resume or fail before execution, never full-rerun silently.
- MoonMind.Run owns progression, prepare orchestration, context generation, target-aware context delivery, ledgers, checkpoints, and full/resumed starts.

## Orchestration Constraints

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.
"""

<!-- Moon Spec specs contain exactly one independently testable user story. Use /speckit.breakdown for technical designs that contain multiple stories. -->

## Classification

- Input type: Single-story runtime feature request.
- Runtime decision: Jira Orchestrate always runs as a runtime implementation workflow, and `docs/Tasks/TaskArchitecture.md` is treated as runtime source requirements.
- Breakdown decision: `moonspec-breakdown` was not run because the MM-634 Jira preset brief defines one user story with one recovery execution goal, bounded acceptance criteria, and explicit failure behavior.
- Resume decision: No existing Moon Spec artifact set for `MM-634` was found under `specs/`; specification was the first incomplete stage.

## User Story - Failed-Step Resume Execution

**Summary**: As a user pressing Resume on a failed task, I want MoonMind to restore completed work, retry only the last failed step, and then continue later steps normally so that preserved prior work is not silently rerun and the original task input remains unchanged.

**Goal**: A resumed execution validates its pinned checkpoint, restores the source run state immediately before the failed step, marks prior completed steps as preserved from the source run, retries the failed step as the first newly executed step, and proceeds through downstream steps without falling back to full rerun behavior.

**Independent Test**: Start a Resume attempt from a failed task with complete checkpoint evidence, then verify the resumed run preserves prior completed steps with source provenance, restores the workspace or branch state before the failed step, injects preserved outputs into the retried step, executes the failed step first, continues later steps normally, and fails before execution rather than full-rerunning when restoration evidence is invalid.

**Acceptance Scenarios**:

1. **Given** a failed task has a valid resume checkpoint for completed prior work and the last failed step, **When** the user presses Resume, **Then** the resumed execution starts from the original task input snapshot without presenting an editable authoring form.
2. **Given** the resume checkpoint references a source workflow, source run, original snapshot, and plan identity, **When** the resumed execution starts, **Then** those identities are validated before any step executes.
3. **Given** workspace, branch, commit, or equivalent runtime state was checkpointed immediately before the failed step, **When** the resumed execution starts, **Then** the runtime restores that state before retrying the failed step.
4. **Given** completed steps exist before the failed step, **When** the resumed execution records progress, **Then** those steps are marked as preserved from the source run with source workflow ID, run ID, logical step ID, and attempt provenance.
5. **Given** preserved completed steps produced outputs needed by later steps, **When** the failed step and downstream steps execute, **Then** preserved outputs are injected so those steps observe the same contracts as a continuous run.
6. **Given** the failed step is identified by the source run's ledger, **When** the resumed execution begins new work, **Then** the failed step is retried as the first newly executed step and later steps execute normally after it succeeds.
7. **Given** checkpoint validation or restoration fails because evidence is missing, stale, unauthorized, corrupted, or inconsistent, **When** Resume is attempted, **Then** execution fails explicitly before the failed step and does not fall back to full rerun or re-execute preserved prior steps.

### Edge Cases

- The resume request references a source workflow ID without the matching source run ID.
- The source run's original task input snapshot does not match the checkpoint's snapshot reference.
- The plan identity or digest in the checkpoint differs from the plan used to identify the failed step.
- Workspace, branch, commit, or equivalent runtime state cannot be restored before the failed step.
- A preserved prior step lacks output refs needed by the failed or downstream steps.
- The failed step succeeds on retry, but a later downstream step fails and must be recorded as fresh resumed-run work rather than source-run preserved work.
- A restoration failure occurs after checkpoint validation but before the first newly executed step starts.

## Assumptions

- Backend eligibility and durable checkpoint creation are handled by the adjacent evidence-gating story; this story covers what the resumed execution does after a checkpoint is selected.
- Existing task, artifact, and execution authorization rules apply to resume checkpoint reads and preserved output injection.
- Resume v1 is intentionally not an edit flow; any task input change requires an edited full retry instead.

## Source Design Requirements

- **DESIGN-REQ-001** (Source: `docs/Tasks/TaskArchitecture.md` section 3.6, lines 101-109): Resume is a distinct failed-task recovery workflow that does not open an authoring form, retries the last failed step using the original task input and durable completed work, never silently edits task input metadata, and must be unavailable or fail explicitly when prior work cannot be restored faithfully. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004, FR-010, FR-011, FR-012.
- **DESIGN-REQ-002** (Source: `docs/Tasks/TaskArchitecture.md` section 7.3, lines 397-415): Resume must pin source workflow and run identity, identify the last failed step, use a checkpoint containing completed-step output refs, prepared input refs, and workspace or branch state, import prior steps as preserved progress, retry the failed step as a new attempt, continue later steps normally, and fail before execution when evidence is incomplete, corrupted, unauthorized, or inconsistent. Scope: in scope. Maps to FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, FR-012.
- **DESIGN-REQ-003** (Source: `docs/Tasks/TaskArchitecture.md` section 8.6, lines 515-534): `MoonMind.Run` owns checkpoint validation, source identity verification, workspace restoration, preserved-step marking, preserved-output injection, failed-step retry, fresh ledgers for retried and later steps, no fallback to full rerun, and provenance for preserved rows. Scope: in scope. Maps to FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, FR-012.
- **DESIGN-REQ-004** (Source: `docs/Tasks/TaskArchitecture.md` section 11, lines 574-624): System invariants require explicit recovery intent, unchanged original inputs for Resume, checkpointed progress, no silent re-execution of preserved steps, and pinned source workflow/run identity. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004, FR-009, FR-010, FR-011, FR-012.
- **DESIGN-REQ-005** (Source: `docs/Tasks/TaskArchitecture.md` section 12.1, lines 630-637): `MoonMind.Run` is the canonical workflow that produces step ledger state and resume checkpoints, may start at a failed step when given a validated resume checkpoint, and owns checkpoint durability even when step execution delegates to child workflows. Scope: in scope. Maps to FR-002, FR-004, FR-005, FR-008, FR-009, FR-010.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Resume MUST use the original task input snapshot unchanged and MUST NOT expose an editable authoring form in v1.
- **FR-002**: A resumed execution MUST validate the resume checkpoint source workflow ID, source run ID, original snapshot identity, and plan identity before executing any step.
- **FR-003**: A resumed execution MUST fail explicitly before executing the failed step when checkpoint validation finds missing, stale, unauthorized, corrupted, or inconsistent evidence.
- **FR-004**: A resumed execution MUST restore workspace, branch, commit, or equivalent runtime state immediately before the failed step before newly executing work.
- **FR-005**: Completed steps before the failed step MUST be imported as preserved progress rather than re-executed by the resumed run.
- **FR-006**: Preserved completed steps MUST carry provenance for source workflow ID, source run ID, logical step ID, and source attempt.
- **FR-007**: Preserved outputs from completed prior steps MUST be injected so the retried failed step and downstream steps observe the same contracts as a continuous run.
- **FR-008**: The failed step MUST be retried as the first newly executed step in the resumed execution.
- **FR-009**: Steps after the retried failed step MUST execute normally after the failed step succeeds, producing fresh resumed-run ledger rows, artifacts, and checkpoints.
- **FR-010**: Resume restoration failure MUST NOT fall back to full rerun behavior.
- **FR-011**: Resume MUST NOT re-execute preserved prior steps unless a future explicit user action requests that behavior.
- **FR-012**: Task detail or equivalent operator progress surfaces MUST distinguish preserved prior steps from newly executed resumed-run steps.
- **FR-013**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key `MM-634` and the canonical Jira preset brief.

### Key Entities

- **Resumed Execution**: The new execution created when a user chooses Resume for a failed task.
- **Source Execution Identity**: The pinned source workflow ID and run ID of the failed task being resumed.
- **Resume Checkpoint**: The durable evidence binding source execution, original snapshot, plan identity, completed-step refs, prepared inputs, and workspace or branch state.
- **Preserved Step**: A completed source-run step represented in the resumed execution as reused progress rather than newly executed work.
- **Retried Failed Step**: The last failed source-run step executed as the first new attempt in the resumed execution.
- **Downstream Step**: A later planned step that executes normally after the retried failed step succeeds.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of valid resumed execution tests preserve the original task input snapshot unchanged and expose no editable authoring form.
- **SC-002**: 100% of resumed execution starts validate source workflow ID, source run ID, original snapshot identity, and plan identity before the first newly executed step.
- **SC-003**: 100% of valid resume cases restore workspace, branch, commit, or equivalent runtime state before retrying the failed step.
- **SC-004**: 100% of completed source steps before the failed step are represented as preserved progress with source workflow ID, run ID, logical step ID, and attempt provenance.
- **SC-005**: 100% of retried failed-step and downstream-step cases receive preserved outputs needed to satisfy continuous-run contracts.
- **SC-006**: 100% of valid resume cases execute the failed step as the first newly executed step and later steps only after that step succeeds.
- **SC-007**: 0 invalid restoration cases fall back to full rerun behavior or re-execute preserved prior steps.
- **SC-008**: Traceability review confirms `MM-634`, the canonical Jira preset brief, and source coverage IDs DESIGN-REQ-014 through DESIGN-REQ-017 remain preserved across MoonSpec artifacts and final verification evidence.
