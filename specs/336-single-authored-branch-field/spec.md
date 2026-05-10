# Feature Specification: Single Authored Branch Field

**Feature Branch**: `336-single-authored-branch-field`
**Created**: 2026-05-10
**Status**: Draft
**Input**: User description:

```text
MoonSpec Orchestration Input: MM-668

Jira Issue: MM-668
Issue Type: Story
Status: In Progress
Summary: Single authored branch field with legacy migration rules

Source Reference:
- Source Document: docs/Tasks/TaskPublishing.md
- Source Title: Task Publishing
- Source Sections:
  - Branch Fields
  - Legacy Migration
- Coverage IDs:
  - DESIGN-REQ-009
  - DESIGN-REQ-010

Story:
As an operator and platform owner, I want new authored submissions to expose only one `branch` field while older snapshots that still carry `startingBranch`/`targetBranch` are normalized safely or surface a reconstruction warning when their intent cannot round-trip.

Acceptance Criteria:
- New authored submissions emit `git.branch` only and never `targetBranch` as authored or operator-facing input.
- `Publish Mode` remains part of the task contract regardless of UI placement.
- Legacy `startingBranch` is normalized to the new authored `branch` when reconstructing older submissions.
- Legacy `targetBranch` is retained only as historical metadata for audit/debug displays and never drives active submission logic.
- Legacy two-branch branch-publish snapshots that cannot be represented by one authored `branch` surface a reconstruction warning rather than silently round-tripping.

Requirements:
- Strip `targetBranch` from new authored submission contracts across UI, API, snapshot, and runtime planning surfaces.
- Implement legacy normalization that maps `startingBranch` to authored `branch` for reconstructed submissions.
- Implement reconstruction warning for legacy two-branch branch-publish snapshots that cannot collapse to a single authored `branch`.
```

<!-- Moon Spec specs contain exactly one independently testable user story. Use /speckit.breakdown for technical designs that contain multiple stories. -->

## User Story - Normalize Authored Branch Input

**Summary**: As an operator and platform owner, I want authored task submissions to expose one branch value while legacy two-branch snapshots are reconstructed safely so that new work has a clear branch contract and old work remains auditable.

**Goal**: New authored submissions use a single `branch` value for branch intent, while older submissions that contain `startingBranch` and `targetBranch` are either normalized to that single value or surfaced with a reconstruction warning when their intent cannot round-trip.

**Independent Test**: Can be fully tested by creating a new authored submission with branch publishing enabled, reconstructing representative legacy snapshots with `startingBranch` and `targetBranch`, and confirming the submitted or reconstructed task contract exposes the expected branch value, publish mode, audit metadata, and warning state.

**Acceptance Scenarios**:

1. **Given** an operator authors a new submission with a selected branch and publish mode, **When** the submission is saved, submitted, snapshotted, or prepared for runtime execution, **Then** the authored branch intent is represented as `git.branch`, `targetBranch` is absent from authored and operator-facing input, and publish mode remains present in the task contract.
2. **Given** an older snapshot contains `startingBranch` and no conflicting branch intent, **When** the system reconstructs it for display, editing, rerun, or submission preparation, **Then** `startingBranch` is normalized to the authored `branch` value and the reconstructed submission can be reviewed without a loss-of-intent warning.
3. **Given** an older snapshot contains a legacy `targetBranch`, **When** the system reconstructs or audits that snapshot, **Then** `targetBranch` is retained only as historical metadata and never determines the active branch for a new or rerun submission.
4. **Given** an older branch-publish snapshot contains two branch values whose intent cannot be represented by one authored `branch`, **When** the system reconstructs the snapshot, **Then** the system surfaces a reconstruction warning and does not silently round-trip the snapshot as if no intent was lost.

### Edge Cases

- A new authored submission omits branch information while publish mode requires a branch: the system rejects or blocks the submission with a recoverable validation message rather than deriving a branch from legacy fields.
- A legacy snapshot contains both `startingBranch` and `targetBranch` with identical values: the system may normalize the authored branch from `startingBranch` while preserving the legacy `targetBranch` only as audit metadata.
- A legacy snapshot contains `targetBranch` but no `startingBranch`: the system must not use `targetBranch` as the active authored branch and must surface that the active branch cannot be reconstructed from the supported authored field.
- A legacy snapshot contains unrelated metadata or unknown branch-shaped fields: only the recognized legacy fields are considered, and unknown values do not override `git.branch`.
- A user edits a reconstructed legacy task after a warning is shown: the edited task must submit only the new single branch field and must not reintroduce legacy branch fields as authored input.

## Assumptions

- The selected story is limited to branch field semantics for task publishing and legacy reconstruction; broader branch resolution behavior outside the Branch Fields and Legacy Migration source sections is out of scope.
- `Publish Mode` remains a required task-contract concept, but this story does not prescribe where publish mode appears in any specific screen.
- Historical metadata is visible only in audit or diagnostic contexts and is not treated as user-authored input for new submissions.

## Source Design Requirements

- **DESIGN-REQ-009**: New authored submissions use a single authored `branch` field for branch intent, `targetBranch` is not authored or operator-facing input, and publish mode remains part of the task contract.
  - Source: `docs/Tasks/TaskPublishing.md` lines 64-69, Branch Fields
  - Scope: in scope
  - Mapped FR: FR-001, FR-002, FR-003, FR-004, FR-005
- **DESIGN-REQ-010**: Legacy snapshots may contain `startingBranch` and `targetBranch`; `startingBranch` may be normalized to the new authored branch, legacy `targetBranch` may be retained only as historical metadata, legacy `targetBranch` must never drive active new submission logic, and unreconstructable two-branch branch-publish snapshots must surface a warning.
  - Source: `docs/Tasks/TaskPublishing.md` lines 71-81, Legacy Migration
  - Scope: in scope
  - Mapped FR: FR-006, FR-007, FR-008, FR-009, FR-010, FR-011

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST represent the branch selected for a newly authored submission as a single `git.branch` value across all authored task input surfaces.
- **FR-002**: System MUST NOT expose `targetBranch` as a user-authored or operator-facing input field for newly authored submissions.
- **FR-003**: System MUST preserve `Publish Mode` as part of the task contract whenever a new authored submission is created, saved, submitted, snapshotted, or prepared for runtime execution.
- **FR-004**: System MUST ensure new authored submission snapshots and prepared runtime inputs do not contain `targetBranch` as active authored branch intent.
- **FR-005**: System MUST reject or block new authored submissions that require branch intent but lack a valid `git.branch`, rather than deriving active branch intent from legacy branch fields.
- **FR-006**: System MUST normalize legacy `startingBranch` to the current authored `branch` value when reconstructing older submissions whose branch intent can be represented by one branch.
- **FR-007**: System MUST retain legacy `targetBranch` only as historical metadata for audit or diagnostic display when reconstructing older submissions.
- **FR-008**: System MUST NOT allow legacy `targetBranch` to determine active branch intent for editing, rerun, resubmission, or runtime preparation.
- **FR-009**: System MUST surface a reconstruction warning when an older branch-publish snapshot contains two branch values whose original intent cannot be represented by one authored `branch`.
- **FR-010**: System MUST avoid silently round-tripping an unreconstructable legacy two-branch branch-publish snapshot as if it were equivalent to the new single-branch authored contract.
- **FR-011**: System MUST preserve enough visible warning or audit information for operators to distinguish normalized legacy snapshots from snapshots whose branch intent could not round-trip.

### Key Entities

- **Authored Task Submission**: A task draft, saved input, submitted task, or runtime-prepared task contract created under the current single-branch model; its active branch intent is `git.branch`.
- **Legacy Task Snapshot**: An older persisted task input that may contain `startingBranch`, `targetBranch`, or both and must be reconstructed under the current authored contract.
- **Branch Intent Metadata**: Historical branch-related values retained for audit or diagnostics but not used as active authored branch input.
- **Reconstruction Warning**: Operator-visible evidence that a legacy snapshot's branch intent could not be represented exactly by one authored branch value.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of newly authored branch-enabled submissions inspected after save, submit, snapshot, and runtime preparation contain `git.branch` as the active branch value and contain no active authored `targetBranch`.
- **SC-002**: 100% of new authored submission flows preserve publish mode in the task contract while using the single authored branch field.
- **SC-003**: 100% of reconstructable legacy snapshots with `startingBranch` are displayed or prepared with that value normalized to the current authored branch field.
- **SC-004**: 100% of legacy snapshots containing `targetBranch` retain it only in audit or diagnostic metadata and never use it as the active branch for a new submission.
- **SC-005**: 100% of unreconstructable legacy two-branch branch-publish snapshots produce a reconstruction warning before an operator can treat the reconstructed task as equivalent to the original.
