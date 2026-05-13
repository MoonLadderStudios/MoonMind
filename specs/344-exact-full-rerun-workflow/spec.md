# Feature Specification: Exact Full Rerun Workflow

**Feature Branch**: `344-exact-full-rerun-workflow`
**Created**: 2026-05-13
**Status**: Draft
**Input**: User description: """
Use the Jira preset brief for MM-645 as the canonical Moon Spec orchestration input.

Additional constraints:

Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-645 MoonSpec Orchestration Input

Use the Jira preset brief for MM-645 as the canonical Moon Spec orchestration input.

Additional constraints:

Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): docs/Tasks/TaskArchitecture.md.

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

## Source

- Jira issue: MM-645
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Exact full rerun workflow
- Priority: Medium
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, `presetInstructions`, or `recommendedPresetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-645 from MM project
Summary: Exact full rerun workflow
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-645 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-645: Exact full rerun workflow

Source Reference
Source Document: docs/Tasks/TaskArchitecture.md
Source Title: Task Architecture (Control Plane)
Source Sections:
- 7.2 Exact full rerun

Coverage IDs:
- DESIGN-REQ-017

As a Mission Control user, I want to choose Rerun on a failed execution to start a new execution from the beginning that reuses the original task input snapshot unchanged so that I can re-execute the exact same task without editing anything and without importing prior progress.

Acceptance Criteria:
- Rerun never opens an authoring form.
- Submission reuses the original snapshot reference unchanged.
- Submission carries TaskRecoveryProvenance kind=exact_full_rerun with pinned source workflow/run ids.
- New execution runs the full pipeline (prepare, prompt composition, planning/plan hydration, all steps).
- No completed progress from the source run is imported.

Requirements:
- Implement exact full rerun submission path and ensure snapshot reuse without mutation.

Relevant implementation notes from docs/Tasks/TaskArchitecture.md:
- `TaskRecoveryKind` includes `exact_full_rerun`, `edited_full_retry`, and `resume_from_failed_step`.
- `TaskRecoveryProvenance` includes `kind`, `sourceWorkflowId`, `sourceRunId`, and optional requester/timestamp fields.
- `task.recovery.kind === "exact_full_rerun"` means the new execution starts from the beginning.
- The original task input snapshot is the authoritative representation of the authored draft.
- Edit, exact full rerun, edited full retry, and Resume all depend on the original snapshot for the authored task input.
- For exact full rerun, the original task input snapshot is reused as the execution input, the task starts from the beginning, prepare/prompt composition/planning or plan hydration/all steps run again, and no completed execution progress is imported from the failed source run.
"""

## User Story - Exact Failed Task Rerun

**Summary**: As a Mission Control user, I want Rerun on a failed execution to start the whole task again from the original task input so that I can repeat the exact same work without editing the task or carrying over partial progress.

**Goal**: Failed-task recovery clearly separates exact full rerun from editing or resume flows, and a user can restart the full workflow with the original authored input unchanged.

**Independent Test**: Can be fully tested by starting from a failed execution with a known original task input snapshot, choosing Rerun, and confirming the new execution uses the unchanged snapshot, records exact-rerun provenance, starts from the beginning, and imports zero completed progress from the source execution.

**Acceptance Scenarios**:

1. **Given** a failed execution with an available original task input snapshot, **When** the user chooses Rerun, **Then** the system starts a new execution without opening an authoring form.
2. **Given** the original task input snapshot contains the authored task details, **When** the exact full rerun is submitted, **Then** the new execution reuses that snapshot unchanged as its input.
3. **Given** the failed source execution has a workflow identity and run identity, **When** the exact full rerun is created, **Then** the new execution records recovery provenance identifying the rerun as `exact_full_rerun` and pins both source identifiers.
4. **Given** the source execution completed one or more stages before failing, **When** the exact full rerun starts, **Then** preparation, prompt composition, planning or plan hydration, and every task step run from the beginning.
5. **Given** completed work exists on the failed source execution, **When** the exact full rerun executes, **Then** no completed progress, preserved step output, or resume checkpoint is imported into the new execution.

### Edge Cases

- The source execution has no reconstructible original task input snapshot when the user attempts Rerun.
- The source execution contains attachments, preset-derived steps, or dependency metadata that must remain unchanged in the reused snapshot.
- The source execution has completed stages or step outputs that could be mistaken for resume progress.
- The user needs to change the task before retrying, which belongs to an editable retry flow rather than exact full rerun.
- The same failed execution offers both Rerun and Resume, but each action must preserve its distinct recovery intent.

## Assumptions

- Mission Control already has a failed execution detail surface where Rerun is presented as a recovery action.
- The original task input snapshot is the authoritative source for the authored task details when exact full rerun is available.
- If the original task input snapshot cannot be recovered or authorized, the system should fail closed with an operator-visible degraded outcome instead of silently creating a partial rerun.

## Source Design Requirements

- **DESIGN-REQ-001** (`docs/Tasks/TaskArchitecture.md` lines 296-304): Recovery provenance must distinguish `exact_full_rerun` from other recovery intents and include pinned source workflow and run identifiers. Scope: in scope. Maps to FR-003 and FR-004.
- **DESIGN-REQ-002** (`docs/Tasks/TaskArchitecture.md` lines 338-341): Full-rerun recovery starts from the beginning, while resume is the only recovery path that restores completed progress. Scope: in scope. Maps to FR-005 and FR-007.
- **DESIGN-REQ-003** (`docs/Tasks/TaskArchitecture.md` lines 347-371): The original task input snapshot is authoritative for recovery actions and must preserve authored task content, attachment refs, step ordering, runtime and publish selections, repository and branch choice, preset metadata, provenance, detachment state, final order, and dependencies. Scope: in scope. Maps to FR-002 and FR-008.
- **DESIGN-REQ-004** (`docs/Tasks/TaskArchitecture.md` lines 386-395): Exact full rerun reuses the original task input snapshot, starts from the beginning, reruns the normal execution path, and imports no completed progress from the failed source run. Scope: in scope. Maps to FR-001, FR-002, FR-005, FR-006, and FR-007.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST allow a user to choose Rerun on an eligible failed execution to start a new execution from the beginning without opening an authoring form.
- **FR-002**: The system MUST reuse the source execution's original task input snapshot unchanged as the exact full rerun input.
- **FR-003**: The system MUST record the new execution's recovery intent as `exact_full_rerun`.
- **FR-004**: The system MUST preserve the source workflow identifier and source run identifier in the exact full rerun's recovery provenance.
- **FR-005**: The system MUST run the full execution path for an exact full rerun, including preparation, prompt composition, planning or plan hydration, and all task steps.
- **FR-006**: The system MUST NOT open or depend on editable task authoring state for exact full rerun submission.
- **FR-007**: The system MUST NOT import completed progress, preserved step outputs, or resume checkpoints from the source execution into an exact full rerun.
- **FR-008**: The system MUST preserve all authored input details already present in the original task input snapshot, including attachments, step order, runtime and publish selections, repository and branch choice, preset metadata, provenance, detachment state, final order, and dependencies.
- **FR-009**: The system MUST present an explicit degraded or blocked outcome when exact full rerun cannot safely reuse the original task input snapshot.
- **FR-010**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key `MM-645` and the original Jira preset brief for traceability.

### Key Entities

- **Failed Execution**: The source task run selected by the user for recovery, including its workflow identity, run identity, terminal failure state, prior progress, and original task input snapshot reference.
- **Original Task Input Snapshot**: The authoritative authored task input that exact full rerun reuses unchanged, including task text, attachments, step structure, selections, preset metadata, and dependencies.
- **Exact Full Rerun Execution**: The new execution created from the failed source execution, with recovery intent `exact_full_rerun` and pinned source identifiers.
- **Recovery Provenance**: The traceability record that distinguishes exact full rerun from editable retry and resume and links the new execution back to the exact source workflow/run.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In validation scenarios with an eligible failed execution, 100% of Rerun actions create a new execution without opening an authoring form.
- **SC-002**: 100% of exact full rerun validation cases reuse the original task input snapshot unchanged.
- **SC-003**: 100% of exact full rerun validation cases record `exact_full_rerun` provenance with non-empty source workflow and run identifiers.
- **SC-004**: 100% of exact full rerun validation cases execute from the beginning with no imported completed progress or resume checkpoint state.
- **SC-005**: Traceability review confirms `MM-645` and the original Jira preset brief are preserved in the active MoonSpec artifacts and downstream delivery metadata.
