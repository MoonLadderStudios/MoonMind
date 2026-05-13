# Feature Specification: Editable Full Retry Workflow

**Feature Branch**: `343-editable-full-retry-workflow`
**Created**: 2026-05-12
**Status**: Draft
**Input**: User description: """
For a single-story Jira preset brief, run moonspec-specify unless an active spec.md already passes the specify gate.
For a broad technical or declarative design, run moonspec-breakdown first, then select the recommended first generated spec unless the issue brief explicitly requires processing all specs.
Preserve Jira issue MM-644 and the original preset brief in spec.md so final verification can compare against them.

Canonical Jira preset brief:

# MM-644 MoonSpec Orchestration Input

## Source

- Jira issue: MM-644
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Editable full retry workflow
- Priority: Medium
- Trusted fetch tool: `jira.get_issue`
- Trusted response artifact: `/work/agent_jobs/mm:64ab0274-00cc-4a8d-be47-54371fa92117/artifacts/moonspec-inputs/MM-644-trusted-jira-get-issue-summary.json`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira issue fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, `presetInstructions`, `recommendedPresetInstructions`, or `acceptanceCriteriaText`.
- Label: moonmind-workflow-mm-a1fb7aa8-954b-4c59-acc2-c0a2c5339282

## Canonical MoonSpec Feature Request

Jira issue: MM-644 from MM project
Summary: Editable full retry workflow
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-644 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-644: Editable full retry workflow

Source Reference
Source Document: docs/Tasks/TaskArchitecture.md
Source Title: Task Architecture (Control Plane)
Source Sections:
- 7.1 Editable full retry
Coverage IDs:
- DESIGN-REQ-016

As a Mission Control user, I want to choose Edit task on a failed execution to open the Create page in edit-for-rerun mode hydrated from the authoritative snapshot, change any authoring fields under normal validation, and submit a new execution that starts from the beginning with its own new snapshot, leaving the original failed execution and its artifacts immutable.

Acceptance Criteria
- Edit task hydrates Create page from the original snapshot.
- User can edit any authoring field subject to standard validation.
- Submission carries TaskRecoveryProvenance kind=edited_full_retry with pinned source workflow/run ids.
- New execution starts from the beginning and writes its own new snapshot.
- Original failed execution's snapshot, ledger, artifacts, and checkpoints remain immutable.
- No completed progress is imported into the edited full retry.

Requirements
- Implement edit-for-rerun page mode and edited-full-retry submission path.

## Relevant Implementation Notes

- Source design path: `docs/Tasks/TaskArchitecture.md`.
- Section 7.1 defines editable full retry as the workflow for changing overall instructions or any task input before retrying the full task.
- The Create page opens in edit-for-rerun mode from the authoritative task input snapshot.
- Users may edit instructions, steps, attachments, runtime, publish mode, branch, presets, dependencies, and other authoring fields subject to normal validation.
- Submitting the edited full retry creates a new execution from the beginning with its own authoritative task input snapshot.
- The original failed execution, its snapshot, step ledger, artifacts, and checkpoints remain immutable.
- No completed execution progress is imported into the edited full retry.
- Preserve `TaskRecoveryProvenance.kind == "edited_full_retry"` with pinned source `workflowId` and `runId` in the new submission path.

## MoonSpec Classification Input

Classify this as a single-story runtime feature request for Mission Control failed-task recovery: implement edit-for-rerun page mode and the edited-full-retry submission path while preserving MM-644 traceability and the immutable-source-run contract.

## Orchestration Constraints

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.
Preserve Jira issue key MM-644 and this canonical Jira preset brief across downstream MoonSpec artifacts and final evidence.
"""

Preserved source Jira preset brief: `MM-644` from the trusted Jira preset brief handoff, reproduced verbatim in `## Original Preset Brief` below for downstream verification.

Original brief reference: trusted `jira.get_issue` MCP response synthesized into `/work/agent_jobs/mm:64ab0274-00cc-4a8d-be47-54371fa92117/artifacts/moonspec-orchestration-input-MM-644.md`.
Classification: single-story runtime feature request.
Resume decision: no existing Moon Spec feature directory or later-stage artifacts matched `MM-644` under `specs/`, so `Specify` was the first incomplete stage.
Runtime intent: Jira Orchestrate always runs as a runtime implementation workflow. Source design references in the brief are treated as runtime source requirements.

## Original Preset Brief

````text
# MM-644 MoonSpec Orchestration Input

## Source

- Jira issue: MM-644
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Editable full retry workflow
- Priority: Medium
- Trusted fetch tool: `jira.get_issue`
- Trusted response artifact: `/work/agent_jobs/mm:64ab0274-00cc-4a8d-be47-54371fa92117/artifacts/moonspec-inputs/MM-644-trusted-jira-get-issue-summary.json`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira issue fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, `presetInstructions`, `recommendedPresetInstructions`, or `acceptanceCriteriaText`.
- Label: moonmind-workflow-mm-a1fb7aa8-954b-4c59-acc2-c0a2c5339282

## Canonical MoonSpec Feature Request

Jira issue: MM-644 from MM project
Summary: Editable full retry workflow
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-644 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-644: Editable full retry workflow

Source Reference
Source Document: docs/Tasks/TaskArchitecture.md
Source Title: Task Architecture (Control Plane)
Source Sections:
- 7.1 Editable full retry
Coverage IDs:
- DESIGN-REQ-016

As a Mission Control user, I want to choose Edit task on a failed execution to open the Create page in edit-for-rerun mode hydrated from the authoritative snapshot, change any authoring fields under normal validation, and submit a new execution that starts from the beginning with its own new snapshot, leaving the original failed execution and its artifacts immutable.

Acceptance Criteria
- Edit task hydrates Create page from the original snapshot.
- User can edit any authoring field subject to standard validation.
- Submission carries TaskRecoveryProvenance kind=edited_full_retry with pinned source workflow/run ids.
- New execution starts from the beginning and writes its own new snapshot.
- Original failed execution's snapshot, ledger, artifacts, and checkpoints remain immutable.
- No completed progress is imported into the edited full retry.

Requirements
- Implement edit-for-rerun page mode and edited-full-retry submission path.

## Relevant Implementation Notes

- Source design path: `docs/Tasks/TaskArchitecture.md`.
- Section 7.1 defines editable full retry as the workflow for changing overall instructions or any task input before retrying the full task.
- The Create page opens in edit-for-rerun mode from the authoritative task input snapshot.
- Users may edit instructions, steps, attachments, runtime, publish mode, branch, presets, dependencies, and other authoring fields subject to normal validation.
- Submitting the edited full retry creates a new execution from the beginning with its own authoritative task input snapshot.
- The original failed execution, its snapshot, step ledger, artifacts, and checkpoints remain immutable.
- No completed execution progress is imported into the edited full retry.
- Preserve `TaskRecoveryProvenance.kind == "edited_full_retry"` with pinned source `workflowId` and `runId` in the new submission path.

## MoonSpec Classification Input

Classify this as a single-story runtime feature request for Mission Control failed-task recovery: implement edit-for-rerun page mode and the edited-full-retry submission path while preserving MM-644 traceability and the immutable-source-run contract.

## Orchestration Constraints

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.
Preserve Jira issue key MM-644 and this canonical Jira preset brief across downstream MoonSpec artifacts and final evidence.
````

<!-- Moon Spec specs contain exactly one independently testable user story. Use /speckit.breakdown for technical designs that contain multiple stories. -->

## User Story - Editable Full Retry From Snapshot

**Summary**: As a Mission Control user recovering from a failed execution, I want Edit task to open an editable retry from the original task snapshot so that I can change the authored task and start a new full execution without mutating the failed run or importing partial progress.

**Goal**: Failed-task recovery supports an editable full retry path that reconstructs the authored task from the authoritative snapshot, permits normal task edits, and submits a new from-beginning execution with clear provenance and immutable source evidence.

**Independent Test**: Can be fully tested by starting from a failed execution with an authoritative task snapshot, choosing Edit task, confirming the edit form is hydrated from the original snapshot, changing representative authoring fields, submitting the edited retry, and verifying that the new execution starts from the beginning with its own snapshot while the failed execution evidence remains unchanged.

**Acceptance Scenarios**:

1. **Given** a failed execution has an authoritative task input snapshot, **When** the user chooses Edit task, **Then** the task authoring surface opens in edit-for-rerun mode populated from that original snapshot.
2. **Given** the edit-for-rerun surface is loaded, **When** the user changes instructions, steps, attachments, runtime choices, publish mode, branch, presets, dependencies, or other supported authoring fields, **Then** normal authoring validation is applied before submission.
3. **Given** the user submits a valid edited full retry, **When** the new execution is created, **Then** it starts from the beginning and records recovery provenance identifying an edited full retry from the source execution.
4. **Given** an edited full retry is accepted, **When** the new execution input is recorded, **Then** the new execution has its own authoritative task input snapshot reflecting the edited authoring state.
5. **Given** an edited full retry is submitted from a failed execution, **When** the system records the new execution, **Then** the original failed execution's snapshot, ledger, artifacts, and checkpoints remain unchanged.
6. **Given** the source execution contains completed progress before failure, **When** the edited full retry starts, **Then** no completed execution progress is imported into the edited full retry.
7. **Given** the original task snapshot is missing, unreadable, unauthorized, or insufficient to hydrate the authored task, **When** the user attempts Edit task, **Then** the system blocks or marks the action unavailable with an operator-readable reason before starting a retry.

### Edge Cases

- The source execution has an original snapshot but some artifact references are unavailable or unauthorized.
- The user changes only metadata-like authoring choices, such as runtime, publish mode, branch, presets, or dependencies.
- The user changes attachment-backed authoring fields while unchanged attachment-backed fields still need to retain their original references.
- The source execution offers multiple recovery actions; edited full retry must remain distinct from exact rerun and Resume.
- A stale or partial source snapshot cannot reconstruct every authored field required for a safe edit experience.
- The edited retry submission is attempted without source execution identifiers needed to establish provenance.

## Assumptions

- Existing authentication, authorization, and ownership rules apply to viewing the source execution, reading its task snapshot, and creating a retry.
- Exact full rerun and Resume behavior are covered by adjacent specs; this story only covers the editable full retry workflow and the boundaries needed to keep it distinct.
- The task authoring surface already defines standard validation for fields it supports; this story requires the edit-for-rerun path to use that validation rather than defining separate rules.

## Source Design Requirements

- **DESIGN-REQ-001** (Source: `docs/Tasks/TaskArchitecture.md` section 7.1, lines 373-379; original coverage ID DESIGN-REQ-016): Editable full retry must open the task authoring surface in edit-for-rerun mode from the authoritative task input snapshot. Scope: in scope. Maps to FR-001, FR-002, FR-010.
- **DESIGN-REQ-002** (Source: `docs/Tasks/TaskArchitecture.md` section 7.1, line 380; original coverage ID DESIGN-REQ-016): The user must be able to edit instructions, steps, attachments, runtime choices, publish mode, branch, presets, dependencies, and other authoring fields subject to normal validation. Scope: in scope. Maps to FR-003, FR-004.
- **DESIGN-REQ-003** (Source: `docs/Tasks/TaskArchitecture.md` section 7.1, line 381; original coverage ID DESIGN-REQ-016): Submitting an edited full retry must create a new execution that starts from the beginning. Scope: in scope. Maps to FR-005, FR-007.
- **DESIGN-REQ-004** (Source: `docs/Tasks/TaskArchitecture.md` section 7.1, line 382; original coverage ID DESIGN-REQ-016): The edited execution must receive its own authoritative task input snapshot. Scope: in scope. Maps to FR-006.
- **DESIGN-REQ-005** (Source: `docs/Tasks/TaskArchitecture.md` section 7.1, line 383; original coverage ID DESIGN-REQ-016): The original failed execution, its snapshot, step ledger, artifacts, and checkpoints must remain immutable after an edited full retry is created. Scope: in scope. Maps to FR-008.
- **DESIGN-REQ-006** (Source: `docs/Tasks/TaskArchitecture.md` section 7.1, line 384; original coverage ID DESIGN-REQ-016): Edited full retry must not import completed execution progress from the failed source run. Scope: in scope. Maps to FR-009.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Failed execution details MUST offer the Edit task action only when the source execution has an authoritative task input snapshot that the current user is allowed to read.
- **FR-002**: Edit task MUST open the task authoring surface in edit-for-rerun mode hydrated from the authoritative source task snapshot.
- **FR-003**: Edit-for-rerun mode MUST allow the user to edit supported authoring fields, including instructions, steps, attachments, runtime choices, publish mode, branch, presets, dependencies, and other task input fields supported by normal authoring.
- **FR-004**: Edited full retry submission MUST apply the same validation rules as normal task authoring before a new execution can be created.
- **FR-005**: A valid edited full retry submission MUST create a new execution rather than modifying or replacing the failed source execution.
- **FR-006**: The new edited full retry execution MUST record its own authoritative task input snapshot reflecting the edited authoring state.
- **FR-007**: The new edited full retry execution MUST start from the beginning of the task.
- **FR-008**: The failed source execution's snapshot, ledger, artifacts, checkpoints, and recorded state MUST remain unchanged after the edited full retry is created.
- **FR-009**: Edited full retry MUST NOT import completed progress, step outputs, workspace checkpoints, or Resume references from the failed source execution.
- **FR-010**: Edited full retry provenance MUST preserve the recovery kind and pinned source execution identity so the new execution can be audited against the failed source run.
- **FR-011**: If the source snapshot is missing, unreadable, unauthorized, or insufficient to hydrate the task authoring surface, the system MUST prevent edited full retry from starting and provide an operator-readable reason.
- **FR-012**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key `MM-644` and the original Jira preset brief for traceability.

### Key Entities

- **Source Execution**: The failed task execution selected for edited full retry; it owns the original immutable task evidence and source identity.
- **Authoritative Task Input Snapshot**: The complete authored task input used to hydrate edit-for-rerun mode and to prove what source state was preserved.
- **Edited Full Retry Execution**: The newly created execution that starts from the beginning using edited authoring input and its own snapshot.
- **Recovery Provenance**: The audit data connecting the edited full retry execution to its recovery intent and pinned source execution identity.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In 100% of eligible failed-execution cases, choosing Edit task opens an edit-for-rerun authoring view populated from the authoritative source snapshot.
- **SC-002**: In 100% of valid edited full retry submissions, the system creates a new execution with its own task input snapshot and leaves the source execution record unchanged.
- **SC-003**: In 100% of edited full retry executions, completed progress and checkpoint evidence from the failed source execution are not imported into the new run.
- **SC-004**: In 100% of ineligible cases where the source snapshot is missing, unreadable, unauthorized, or insufficient, edited full retry is blocked before a new execution starts and an operator-readable reason is available.
- **SC-005**: Traceability review confirms `MM-644`, the original Jira preset brief, and DESIGN-REQ-001 through DESIGN-REQ-006 remain present across MoonSpec artifacts and final evidence.
