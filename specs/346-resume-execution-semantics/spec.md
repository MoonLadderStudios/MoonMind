# Feature Specification: Resume Execution Semantics

**Feature Branch**: `346-resume-execution-semantics`
**Created**: 2026-05-13
**Status**: Draft
**Input**: User description: """
Use the Jira preset brief for MM-647 as the canonical Moon Spec orchestration input.

Additional constraints:

Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-647 MoonSpec Orchestration Input

## Source

- Jira issue: MM-647
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Resume execution semantics in MoonMind.Run
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, `presetInstructions`, or `recommendedPresetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-647 from MM project
Summary: Resume execution semantics in MoonMind.Run
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Source Reference
Source Document: docs/Tasks/TaskArchitecture.md
Source Title: Task Architecture (Control Plane)
Source Sections:
- 7.3 Resume from failed step
- 8.6 Resume execution responsibilities
- Invariant 14
- Invariant 16
- Invariant 17
Coverage IDs:
- DESIGN-REQ-018
- DESIGN-REQ-024

As an execution-plane engineer, I want MoonMind.Run, when started with task.resume.kind=resume_from_failed_step, to load and validate the resume checkpoint (source workflow/run ids, snapshot, plan identity), materialize the workspace state before the failed step, mark prior completed steps as preserved (with source provenance) without re-executing them, inject preserved outputs into downstream steps, retry the failed step as the first newly executed step, and fail explicitly when restoration is incomplete instead of silently falling back to full rerun.

Acceptance Criteria
- Resume validates source workflowId/runId, snapshot ref, and plan identity/digest before executing the failed step.
- Workspace state is materialized to the pre-failed-step checkpoint before retry.
- Preserved prior steps are marked preserved with provenance (source workflowId/runId/logicalStepId/attempt) and not re-executed.
- Preserved outputs are injected so failed and downstream steps see the same contracts as a continuous run.
- Failed step is retried as a new attempt and produces fresh ledger rows/artifacts/checkpoints.
- Resume never silently falls back to full rerun on restoration failure; failures are explicit and operator-readable.
- Resume rejects user-edited task input in v1; only edited full retry permits edits.

Requirements
Implement Resume entry path in MoonMind.Run, checkpoint validator, preserved-step marker, output injector, and explicit failure behaviors.

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-647 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.
"""

<!-- Moon Spec specs contain exactly one independently testable user story. Use /speckit.breakdown for technical designs that contain multiple stories. -->

## Classification

- Input type: Single-story runtime feature request.
- Runtime decision: Jira Orchestrate always runs as a runtime implementation workflow, and `docs/Tasks/TaskArchitecture.md` is treated as runtime source requirements.
- Breakdown decision: `moonspec-breakdown` was not run because the MM-647 Jira preset brief defines one execution-plane story with one resume execution goal, bounded acceptance criteria, and no competing independent stories.
- Resume decision: No existing Moon Spec artifact set for `MM-647` was found under `specs/`; specification was the first incomplete stage.

## User Story - Failed-Step Resume Execution

**Summary**: As an execution-plane engineer, I want Resume to restore a validated failed-step checkpoint, preserve completed prior work, and retry the failed step as the first new execution action so that users can continue a failed task without editing inputs or losing trusted progress.

**Goal**: A resumed task run behaves like a continuation from a verified pre-failed-step state: prior completed steps remain preserved with source provenance, failed and downstream steps receive the same preserved inputs they would have seen in a continuous run, and restoration failures stop explicitly before any misleading full rerun occurs.

**Independent Test**: Start representative Resume executions from valid and invalid failed-step checkpoints, then verify that valid resumes validate source identity and plan identity, restore workspace state, preserve prior steps without re-executing them, inject preserved outputs, retry the failed step first, continue downstream after success, and fail explicitly before execution when restoration is incomplete or inconsistent.

**Acceptance Scenarios**:

1. **Given** a failed source run has a resume checkpoint with source workflow ID, source run ID, original task snapshot, failed step identity, and plan identity, **When** a new run starts with failed-step Resume intent, **Then** the checkpoint is loaded and validated against those identities before the failed step can execute.
2. **Given** the resume checkpoint identifies workspace, branch, commit, or equivalent state immediately before the failed step, **When** Resume begins execution, **Then** that state is materialized before the failed step is retried.
3. **Given** completed prior steps have recoverable output refs and provenance in the checkpoint, **When** the resumed run initializes its step state, **Then** those steps are marked as preserved from the source run with source workflow ID, source run ID, logical step ID, and attempt, and they are not re-executed.
4. **Given** preserved prior steps produced outputs needed by the failed step or later steps, **When** the resumed run composes inputs for the failed and downstream steps, **Then** preserved outputs are injected so those steps observe the same contracts as a continuous run.
5. **Given** checkpoint restoration succeeds, **When** the resumed run starts new work, **Then** the failed step is retried as a new attempt before any later step executes.
6. **Given** the retried failed step succeeds, **When** downstream steps continue, **Then** retried and later steps produce fresh ledger rows, artifacts, and checkpoints for the resumed run.
7. **Given** checkpoint restoration is missing, unauthorized, corrupted, or inconsistent with the original task input or plan identity, **When** Resume is requested, **Then** the run fails with an explicit operator-readable outcome before executing the failed step and does not silently fall back to full rerun behavior.
8. **Given** a user attempts to edit task instructions, steps, attachments, runtime, publish mode, branch, presets, or dependencies for failed-step Resume in v1, **When** the request is evaluated, **Then** it is rejected as Resume-ineligible and directed to the edited full retry path instead.

### Edge Cases

- The source workflow ID is valid but the source run ID does not match the checkpoint.
- The checkpoint references a stale or mismatched original task input snapshot.
- The plan identity or digest is absent, mismatched, or no longer authorized.
- Workspace restoration succeeds partially but cannot prove the exact pre-failed-step state.
- A preserved step has output refs but lacks source logical step ID or attempt provenance.
- A preserved output ref is unavailable, unauthorized, deleted, or incompatible with downstream step input expectations.
- Multiple prior steps exist and only a subset can be safely preserved.
- The failed step succeeds in the resumed run but a later step fails and must write fresh resumed-run evidence.
- The same source execution offers full rerun, edited retry, and Resume, but each action must preserve its distinct recovery intent.

## Assumptions

- Resume eligibility and checkpoint durability are established by adjacent recovery evidence work; this story covers how `MoonMind.Run` consumes a valid checkpoint and handles invalid restoration during execution.
- Existing task artifact authorization rules apply to resume checkpoints, preserved output refs, original task snapshots, and workspace state refs.
- Failed-step Resume is not an edit flow in v1; changing authored task input remains the responsibility of edited full retry.

## Source Design Requirements

- **DESIGN-REQ-001** (Source: `docs/Tasks/TaskArchitecture.md` section 7.3, lines 397-411; original coverage ID DESIGN-REQ-018): Failed-step Resume must retry the last failed step using completed work up to that step, must not allow task input changes, must pin the source workflow and run IDs, must identify the failed step from source run evidence, must resolve a checkpoint containing completed-step outputs, prepared input refs, and pre-failed-step workspace or branch state, must import completed prior steps as preserved progress, must retry the failed step as a new attempt, must allow later steps to continue after success, must display preserved prior steps as reused from the source run, and must fail explicitly before the failed step when restoration is incomplete, corrupted, unauthorized, or inconsistent. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, FR-012, FR-013.
- **DESIGN-REQ-002** (Source: `docs/Tasks/TaskArchitecture.md` section 8.6, lines 517-525; original coverage ID DESIGN-REQ-024): `MoonMind.Run` must load and validate the resume checkpoint, verify source workflow ID, source run ID, task snapshot, and plan identity, materialize restored workspace state before the failed step, mark completed prior steps as preserved without re-execution, inject preserved outputs, retry the failed step first, and produce fresh ledger rows, artifacts, and checkpoints for retried and later steps. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-008, FR-009, FR-010, FR-011.
- **DESIGN-REQ-003** (Source: `docs/Tasks/TaskArchitecture.md` section 8.6, lines 527-531): The execution plane must not silently fall back to full rerun when Resume restoration fails, must not re-execute preserved prior steps without explicit future user intent, and preserved rows must carry source workflow ID, source run ID, logical step ID, and attempt provenance. Scope: in scope. Maps to FR-004, FR-007, FR-012.
- **DESIGN-REQ-004** (Source: `docs/Tasks/TaskArchitecture.md` invariant 14, lines 617-618): Resume must use the original task input snapshot unchanged, and any user edit to instructions, steps, attachments, runtime, publish mode, branch, presets, or dependencies requires edited full retry instead. Scope: in scope. Maps to FR-003, FR-013.
- **DESIGN-REQ-005** (Source: `docs/Tasks/TaskArchitecture.md` invariant 16, lines 623-624): Resume must display and treat prior completed steps as preserved from the source run, and re-executing them without explicit user intent is a contract violation. Scope: in scope. Maps to FR-004, FR-005, FR-007.
- **DESIGN-REQ-006** (Source: `docs/Tasks/TaskArchitecture.md` invariant 17, lines 626-627): Resume must pin both source workflow ID and source run ID so recovery cannot drift to another run of the same logical execution. Scope: in scope. Maps to FR-001, FR-002.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: A failed-step Resume request MUST identify the source workflow ID and source run ID before the resumed run can execute.
- **FR-002**: The resumed run MUST validate the resume checkpoint against the source workflow ID, source run ID, original task snapshot, failed step identity, and plan identity before executing the failed step.
- **FR-003**: Failed-step Resume MUST use the original task input snapshot unchanged.
- **FR-004**: The resumed run MUST import eligible completed prior steps as preserved progress rather than re-executing them.
- **FR-005**: Preserved prior steps MUST carry provenance identifying the source workflow ID, source run ID, logical step ID, and attempt.
- **FR-006**: The resumed run MUST materialize the checkpointed workspace, branch, commit, or equivalent state before retrying the failed step.
- **FR-007**: The task detail experience MUST show preserved prior steps as reused from the source run rather than freshly executed by the resumed run.
- **FR-008**: Preserved outputs from prior completed steps MUST be available to the retried failed step and downstream steps with the same observable contracts as a continuous run.
- **FR-009**: The failed step MUST be retried as the first newly executed step in the resumed run.
- **FR-010**: Later steps MUST execute normally after the retried failed step succeeds.
- **FR-011**: The retried failed step and all later steps MUST produce fresh resumed-run ledger rows, artifacts, and checkpoints.
- **FR-012**: If checkpoint restoration is incomplete, corrupted, unauthorized, or inconsistent with the original task input or plan identity, Resume MUST fail explicitly before executing the failed step and MUST NOT silently fall back to full rerun behavior.
- **FR-013**: Failed-step Resume MUST reject user-edited task input in v1, including changes to instructions, steps, attachments, runtime, publish mode, branch, presets, or dependencies.
- **FR-014**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key `MM-647` and the original Jira preset brief.

### Key Entities

- **Failed Source Run**: The exact source workflow and run selected for failed-step Resume, including its failed step identity, original task input snapshot, plan identity, completed-step evidence, and checkpoint refs.
- **Resume Checkpoint**: Durable evidence binding the source run to prepared input refs, completed-step output refs, failed step identity, plan identity, and workspace or branch state immediately before the failed step.
- **Preserved Step**: A completed prior step imported into the resumed run as reused progress with source workflow ID, source run ID, logical step ID, and attempt provenance.
- **Preserved Output**: A recoverable output ref from a preserved step that is injected into the retried failed step or downstream steps.
- **Resumed Run**: The new task run created with failed-step Resume intent, which starts new execution at the failed step and records fresh evidence for the retried failed step and later steps.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of valid failed-step Resume validation cases load and validate source workflow ID, source run ID, original task snapshot, failed step identity, and plan identity before the failed step executes.
- **SC-002**: 100% of valid Resume validation cases materialize checkpointed workspace, branch, commit, or equivalent state before the failed step starts.
- **SC-003**: 100% of eligible prior completed steps in validation are marked preserved with source workflow ID, source run ID, logical step ID, and attempt provenance, and 0 of those preserved steps are re-executed.
- **SC-004**: 100% of validation cases with preserved outputs make those outputs available to the retried failed step and downstream steps using the same observable contracts as a continuous run.
- **SC-005**: 100% of valid Resume validation cases retry the failed step as the first newly executed step and produce fresh resumed-run evidence for retried and later steps.
- **SC-006**: 100% of invalid restoration validation cases fail explicitly before executing the failed step and 0 cases fall back silently to full rerun behavior.
- **SC-007**: 100% of edited-input Resume attempts in v1 validation are rejected as Resume-ineligible and require edited full retry instead.
- **SC-008**: Traceability review confirms `MM-647`, the original Jira preset brief, and source coverage IDs DESIGN-REQ-018 and DESIGN-REQ-024 remain preserved in MoonSpec artifacts and final verification evidence.
