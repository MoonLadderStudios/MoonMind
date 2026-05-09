# Feature Specification: Define Canonical Task-Shaped Contract and Server-Side Normalization

**Feature Branch**: `330-define-canonical-task-contract`
**Created**: 2026-05-08
**Status**: Draft
**Input**: User description: """
Use the Jira preset brief for MM-638 as the canonical Moon Spec orchestration input.

Additional constraints:

Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): docs/Tasks/TaskArchitecture.md

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-638 MoonSpec Orchestration Input

## Source

- Jira issue: MM-638
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: recovered from Temporal workflow memo mm:6d42a4d0-fb09-4a9d-8af6-6939818dbdbb (not accessible from connected Atlassian workspace gaudhammer.atlassian.net)
- Summary: Define canonical task-shaped contract & server-side normalization
- Trusted recovery source: Temporal workflow memo title field confirmed by previous orchestration step (run dd8cccb8-bb6b-4295-a05a-e2dfcec6eef0)

## Canonical MoonSpec Feature Request

Jira issue: MM-638 from MM project
Summary: Define canonical task-shaped contract & server-side normalization
Issue type: Story
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-638 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-638: Define canonical task-shaped contract & server-side normalization

Source Reference
Source Document: docs/Tasks/TaskArchitecture.md
Source Title: Task Architecture (Control Plane)
Source Sections:
- 5.3 Task contract normalization
- 6 Canonical task-shaped contract
- 7 Snapshot, full retry, and Resume architecture
- 11 Invariants

The brief defines the gap between what task_contract.py currently implements
(TaskExecutionSpec, TaskStepSource, AuthoredPresetBinding) and what
TaskArchitecture.md §6 requires but is absent — specifically TaskRecoveryKind,
TaskRecoveryProvenance, ResumeFromFailedStepRef, recovery/resume fields on
TaskExecutionSpec, dependsOn, and removal of the legacy targetBranch field —
plus server-side normalization enforcement at the executions API boundary.

Acceptance Criteria
- The task contract defines TaskRecoveryKind, TaskRecoveryProvenance, and ResumeFromFailedStepRef with the fields specified in TaskArchitecture.md §6.
- TaskExecutionSpec accepts optional recovery and resume fields matching those types.
- TaskExecutionSpec accepts an optional dependsOn list.
- The canonical git field is task.git.branch; targetBranch is removed from new task-shaped payloads.
- The executions API boundary enforces normalization: malformed or incomplete recovery/resume payloads are rejected with explicit errors.
- Recovery intent (exact_full_rerun, edited_full_retry, resume_from_failed_step) is validated as a discriminated set; resume cannot be inferred from a rerun request.

## Orchestration Constraints

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.
"""

<!-- Moon Spec specs contain exactly one independently testable user story. Use /speckit.breakdown for technical designs that contain multiple stories. -->

## Classification

- Input type: Single-story runtime feature request backed by a source design document.
- Runtime decision: Jira Orchestrate always runs as a runtime implementation workflow; `docs/Tasks/TaskArchitecture.md` is treated as runtime source requirements.
- Breakdown decision: `moonspec-breakdown` was not run because the MM-638 brief describes one independently testable story — adding missing contract types, fields, and server-side normalization to the existing `task_contract.py` surface — without multiple independent user-visible stories requiring separate specs.
- Resume decision: No existing Moon Spec artifact set for `MM-638` or `330-define-canonical-task-contract` was found under `specs/`; specification is the first incomplete stage.

## User Story - Validated Recovery and Contract Completeness for Task Submissions

**Summary**: As a system operator submitting tasks with recovery or resume intent, I want the task contract to capture explicit recovery kind, resume provenance, and dependency declarations, and the submission endpoint to reject malformed or ambiguous recovery payloads, so that recovery intent is never silently inferred or propagated incorrectly.

**Goal**: Operators and integrations that submit `exact_full_rerun`, `edited_full_retry`, or `resume_from_failed_step` recovery-typed tasks receive immediate, explicit validation errors if their payload is incomplete or contradictory, and valid payloads are preserved end-to-end so execution can safely restore prior work and retry the failed step.

**Independent Test**: Can be fully tested by submitting task payloads with and without recovery fields to the executions API and confirming that complete, well-formed recovery payloads are accepted and normalized correctly while malformed, incomplete, or ambiguous recovery payloads produce explicit validation errors at the API boundary — without requiring downstream workflow inspection.

**Acceptance Scenarios**:

1. **Given** a task payload includes `task.recovery.kind = "resume_from_failed_step"` and a complete `task.resume` block with `sourceWorkflowId`, `sourceRunId`, `failedStepId`, `resumeCheckpointRef`, and `taskInputSnapshotRef`, **When** it is submitted to the executions endpoint, **Then** it is accepted, normalized, and the recovery and resume fields are preserved in the canonical output.
2. **Given** a task payload includes `task.recovery.kind = "resume_from_failed_step"` but `task.resume` is absent or missing required fields, **When** it is submitted to the executions endpoint, **Then** the endpoint returns a validation error identifying the missing resume block or required fields.
3. **Given** a task payload includes a `task.resume` block but `task.recovery.kind` is not `"resume_from_failed_step"`, **When** it is submitted to the executions endpoint, **Then** the endpoint returns a validation error because resume provenance without the matching recovery kind is ambiguous.
4. **Given** a task payload includes `task.recovery.kind = "exact_full_rerun"` with `sourceWorkflowId` and `sourceRunId`, **When** it is submitted to the executions endpoint, **Then** it is accepted, normalized, and the recovery fields are preserved without a resume block.
5. **Given** a task payload includes `task.recovery.kind = "edited_full_retry"` with `sourceWorkflowId` and `sourceRunId`, **When** it is submitted to the executions endpoint, **Then** it is accepted, normalized, and the recovery fields are preserved without a resume block.
6. **Given** a task payload includes a `task.git.targetBranch` field (legacy), **When** it is submitted to the executions endpoint, **Then** the endpoint either rejects it with an explicit error or normalizes it away so the canonical output uses `task.git.branch` instead.
7. **Given** a task payload declares `task.dependsOn` with a list of dependency identifiers, **When** it is submitted to the executions endpoint, **Then** the dependency list is preserved verbatim in the canonical normalized output.
8. **Given** a `task.resume` payload has `resumeCheckpointRef` that is empty or missing after normalization, **When** submitted, **Then** the endpoint rejects it with an explicit error because a resume checkpoint is required.

### Edge Cases

- A task submission with neither `recovery` nor `resume` is valid and must not be affected by the new fields.
- A `dependsOn` list that is empty must be treated the same as absent (no-op).
- `recovery.requestedBy` and `recovery.requestedAt` are optional fields; their absence must not cause validation failure.
- `resume.failedStepAttempt`, `resume.planRef`, and `resume.planDigest` are optional; their absence must not cause validation failure when the required fields are present.
- `task.recovery.kind` must be rejected if it is any value outside the three canonical literals.
- If `task.git.branch` and `task.git.startingBranch` are both provided, normalization must not silently overwrite one with the other; field semantics are distinct.

## Assumptions

- `task.git.branch` is the authored branch field (PR base or push target) and `task.git.startingBranch` (the checkout ref or source SHA) remains a separate internal field; this story removes `targetBranch` as a canonical authored field without affecting `startingBranch`.
- Normalization of legacy `targetBranch` into `branch` is acceptable for backward compatibility within `build_canonical_task_view`; pre-release rules (Constitution Principle XIII) allow removal without a deprecation window.
- Server-side normalization in this story applies to the canonical `task` (type `task`) path in the executions API; legacy `codex_exec` and `codex_skill` paths are out of scope unless they route through canonical normalization.
- This story covers contract definition and API-boundary validation; downstream workflow enforcement (e.g., the Temporal worker refusing to run a resume payload without durable checkpoint evidence) is out of scope and belongs to a separate story.
- `task.dependsOn` carries dependency identifiers as opaque strings in this story; resolution, validation against live workflow state, and blocking behavior are out of scope.

## Source Design Requirements

- **DESIGN-REQ-001** (`docs/Tasks/TaskArchitecture.md` §6, lines 296–321): The task contract must define `TaskRecoveryKind` as a three-value discriminated literal (`"exact_full_rerun"`, `"edited_full_retry"`, `"resume_from_failed_step"`), `TaskRecoveryProvenance` with `kind`, `sourceWorkflowId`, `sourceRunId`, `requestedBy?`, and `requestedAt?`, and `ResumeFromFailedStepRef` with `kind`, `sourceWorkflowId`, `sourceRunId`, `failedStepId`, `failedStepAttempt?`, `resumeCheckpointRef`, `taskInputSnapshotRef`, `planRef?`, and `planDigest?`. Scope: in scope. Mapped to FR-001, FR-002, FR-003.
- **DESIGN-REQ-002** (`docs/Tasks/TaskArchitecture.md` §6, lines 322–342): `TaskExecutionSpec` (equivalent to `TaskPayloadWithRecovery`) must accept optional `recovery` and `resume` top-level fields. The rules govern when each combination is valid. Scope: in scope. Mapped to FR-004, FR-005, FR-006, FR-007, FR-008.
- **DESIGN-REQ-003** (`docs/Tasks/TaskArchitecture.md` §6, lines 291–293): `task.dependsOn` is a declared optional list of dependency identifiers that must be accepted and preserved. Scope: in scope. Mapped to FR-009.
- **DESIGN-REQ-004** (`docs/Tasks/TaskArchitecture.md` §6, lines 333–334): `task.git.branch` is the single authored branch field; new canonical payloads must not include `targetBranch`. Scope: in scope. Mapped to FR-010, FR-011.
- **DESIGN-REQ-005** (`docs/Tasks/TaskArchitecture.md` §5.3, lines 172–178): Server-side normalization must validate the task-shaped payload at the executions API boundary and fail explicitly on contract violations. Scope: in scope. Mapped to FR-012, FR-013.
- **DESIGN-REQ-006** (`docs/Tasks/TaskArchitecture.md` §11 Invariants 13–17, lines 611–624): Recovery intents are distinct; Resume must be pinned with both `sourceWorkflowId` and `sourceRunId`; Resume may not silently fall back to full rerun. Scope: in scope. Mapped to FR-007, FR-008, FR-013.
- **DESIGN-REQ-007** (`docs/Tasks/TaskArchitecture.md` §7, lines 344–448): The snapshot, full retry, and Resume architecture rules apply at the submission contract level for this story: `recovery.kind` governs which execution path is taken; `resume` block identifies the pinned source run and checkpoint. Detailed workflow enforcement is out of scope. Scope: partially in scope (contract only). Mapped to FR-005, FR-006, FR-008.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The task contract MUST define a `TaskRecoveryKind` discriminated type with exactly three valid values: `"exact_full_rerun"`, `"edited_full_retry"`, and `"resume_from_failed_step"`.
- **FR-002**: The task contract MUST define a `TaskRecoveryProvenance` type with required fields `kind` (TaskRecoveryKind), `sourceWorkflowId` (non-empty string), and `sourceRunId` (non-empty string), and optional fields `requestedBy` and `requestedAt`.
- **FR-003**: The task contract MUST define a `ResumeFromFailedStepRef` type with required fields `kind` (literal `"resume_from_failed_step"`), `sourceWorkflowId` (non-empty string), `sourceRunId` (non-empty string), `failedStepId` (non-empty string), `resumeCheckpointRef` (non-empty string), and `taskInputSnapshotRef` (non-empty string), and optional fields `failedStepAttempt`, `planRef`, and `planDigest`.
- **FR-004**: `TaskExecutionSpec` MUST accept an optional `recovery` field typed as `TaskRecoveryProvenance`.
- **FR-005**: `TaskExecutionSpec` MUST accept an optional `resume` field typed as `ResumeFromFailedStepRef`.
- **FR-006**: When `task.recovery.kind` is `"resume_from_failed_step"`, the `task.resume` block MUST be present and valid; the absence or incompleteness of `task.resume` MUST produce an explicit validation error.
- **FR-007**: When `task.resume` is present, `task.recovery.kind` MUST be `"resume_from_failed_step"`; any other recovery kind paired with a `task.resume` block MUST produce an explicit validation error.
- **FR-008**: When `task.recovery.kind` is `"exact_full_rerun"` or `"edited_full_retry"`, `task.recovery.sourceWorkflowId` and `task.recovery.sourceRunId` MUST be present and non-empty; the absence of either MUST produce an explicit validation error.
- **FR-009**: `TaskExecutionSpec` MUST accept an optional `dependsOn` field containing a list of zero or more dependency identifier strings; the list MUST be preserved verbatim in the normalized canonical output.
- **FR-010**: The canonical `task.git` contract MUST expose `branch` as the single authored branch field; `targetBranch` MUST NOT appear as a first-class field in new canonical task-shaped payloads.
- **FR-011**: Submissions that include `task.git.targetBranch` in a canonical task payload MUST be handled without silently propagating `targetBranch` into the normalized output; the system MUST either normalize it to `branch` or reject it with an explicit error.
- **FR-012**: The executions API boundary MUST invoke task contract normalization for canonical `task`-typed submissions and MUST surface task contract validation errors as API-level rejections with operator-readable messages.
- **FR-013**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `MM-638` and the original Jira preset brief for traceability.

### Key Entities

- **TaskRecoveryKind**: A three-value discriminated literal that classifies which recovery workflow was requested for a task submission.
- **TaskRecoveryProvenance**: A structured record attached to a task submission that identifies the recovery kind and the source workflow run being retried or resumed.
- **ResumeFromFailedStepRef**: A structured record that pins a resume submission to a specific source run, failed step, resume checkpoint, and task input snapshot so the execution plane can restore prior progress.
- **Canonical Branch Field**: `task.git.branch` — the single authored field identifying the target repository branch for publish or PR base; replaces `targetBranch` in new canonical payloads.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In validation scenarios with well-formed `resume_from_failed_step` payloads, all required `ResumeFromFailedStepRef` fields are accepted and the canonical normalized output preserves them without alteration.
- **SC-002**: In validation scenarios with missing or incomplete `task.resume` blocks paired with `recovery.kind = "resume_from_failed_step"`, the executions API returns an explicit error identifying the missing field, not a generic or silent failure.
- **SC-003**: In validation scenarios with a `task.resume` block absent a matching `recovery.kind = "resume_from_failed_step"`, the executions API returns an explicit error preventing ambiguous recovery inference.
- **SC-004**: In validation scenarios with `exact_full_rerun` and `edited_full_retry` recovery types, the API accepts valid payloads and preserves `sourceWorkflowId` and `sourceRunId` in normalized output.
- **SC-005**: In validation scenarios with a `dependsOn` list, the normalized output contains the exact dependency strings from the input without reordering or modification.
- **SC-006**: In validation scenarios where `task.git.targetBranch` is submitted in a canonical payload, it does not appear in the normalized canonical output; `task.git.branch` is the field present in the output.
- **SC-007**: Traceability review confirms `MM-638`, the original Jira preset brief, and all DESIGN-REQ-001 through DESIGN-REQ-007 are preserved in MoonSpec artifacts and final verification evidence.
