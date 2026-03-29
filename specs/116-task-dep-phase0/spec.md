# Feature Specification: Task Dependencies Phase 0 — Spec Alignment

**Feature Branch**: `116-task-dep-phase0`
**Created**: 2026-03-29
**Status**: Draft
**Input**: User description: "Fully implement Phase 0 from docs/tmp/011-TaskDependenciesPlan.md"

## Overview

Phase 0 of the Task Dependencies implementation plan requires aligning the canonical documentation `docs/Tasks/TaskDependencies.md` with the current Temporal-backed architecture. This includes updating terminology, adding an implementation snapshot (what exists vs. what is missing), and clarifying the v1 scope constraints.

An audit of `docs/Tasks/TaskDependencies.md` against these requirements shows that the document **has already been rewritten and reflects the current design**. Phase 0 deliverables are verified as complete:

- Terminology uses `/api/executions`, `workflowId`, `taskId == workflowId`, and `initialParameters.task.dependsOn`.
- A current implementation snapshot (§3) documents what is already implemented vs. still missing.
- v1 scope (§2.1) is explicitly described: create-time only, `MoonMind.Run` only, no edit support, no cross-type dependencies.
- The document is structured as declarative desired state, separate from the implementation backlog in `docs/tmp/011-TaskDependenciesPlan.md`.

The role of this spec is to formally document and close Phase 0 in the speckit pipeline, update the plan status to reflect completion, and confirm constitutional alignment for the documentation approach.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Documentation is aligned with Temporal architecture (Priority: P1)

A developer or operator reading `docs/Tasks/TaskDependencies.md` finds accurate, Temporal-aligned terminology and design contracts. They do not encounter legacy terms like `taskRun`, `legacy queue`, or mismatched endpoint paths. The v1 scope is unambiguous.

**Why this priority**: The canonical doc is the reference point for all downstream implementation phases. If it is stale or incorrect, every subsequent phase is at risk.

**Independent Test**: Open `docs/Tasks/TaskDependencies.md` and verify all terminology, the implementation snapshot, and the v1 scope constraints match the Phase 0 requirements from `docs/tmp/011-TaskDependenciesPlan.md`.

**Acceptance Scenarios**:

1. **Given** `docs/Tasks/TaskDependencies.md`, **When** reviewed for terminology, **Then** all references use `/api/executions`, `workflowId`, `taskId == workflowId`, and `initialParameters.task.dependsOn`.
2. **Given** `docs/Tasks/TaskDependencies.md`, **When** reviewed for implementation snapshot, **Then** §3 clearly delineates what is already implemented and what is still missing.
3. **Given** `docs/Tasks/TaskDependencies.md`, **When** reviewed for v1 scope, **Then** §2.1 states: create-time only, `MoonMind.Run` only, no edit support, no cross-type dependencies.

---

### User Story 2 - Plan tracking doc acknowledges Phase 0 completion (Priority: P2)

The `docs/tmp/011-TaskDependenciesPlan.md` status reflects that Phase 0 is complete, so downstream implementers and orchestration tools know to move to Phase 1.

**Why this priority**: The plan doc drives implementation sequencing. Phase 0 being marked as "Proposed" with no status update creates confusion about what work remains.

**Independent Test**: Read `docs/tmp/011-TaskDependenciesPlan.md` and confirm Phase 0 shows a completed status, while Phases 1–5 remain open.

**Acceptance Scenarios**:

1. **Given** `docs/tmp/011-TaskDependenciesPlan.md`, **When** Phase 0 is reviewed, **Then** it is marked as complete.
2. **Given** `docs/tmp/011-TaskDependenciesPlan.md`, **When** Phases 1–5 are reviewed, **Then** they remain open (not marked complete).

---

### Edge Cases

- What if `docs/Tasks/TaskDependencies.md` has terminology gaps not caught in the audit? The spec currently assumes the document is aligned based on the audit. Any remaining gaps would be minor editorial fixes.
- What if the plan doc update creates conflicting states across branches? The plan doc update must be merged quickly to avoid branches diverging on status.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The canonical document `docs/Tasks/TaskDependencies.md` MUST use `workflowId` as the dependency target identifier (not `taskId`, `runId`, or legacy queue identifiers).
- **FR-002**: The canonical document MUST state that `taskId == workflowId` for Temporal-backed task surfaces.
- **FR-003**: The canonical document MUST state that `dependsOn` values are provided via `initialParameters.task.dependsOn`.
- **FR-004**: The canonical document MUST include an "implementation snapshot" section that distinguishes what is already implemented from what is still missing.
- **FR-005**: The canonical document MUST state the v1 scope limits: create-time-only dependency declaration, `MoonMind.Run`-to-`MoonMind.Run` only, maximum 10 dependencies, no editing after create, no cross-workflow-type dependencies.
- **FR-006**: The plan tracking document `docs/tmp/011-TaskDependenciesPlan.md` MUST be updated to reflect Phase 0 as complete.
- **FR-007**: The plan tracking document MUST leave Phases 1–5 status unchanged (still open).

### Key Entities

- **TaskDependencies canonical doc** (`docs/Tasks/TaskDependencies.md`): The declarative desired-state reference for the task dependencies feature. Must reflect Temporal-aligned terminology and current implementation state.
- **Phase 0 Plan Entry** (`docs/tmp/011-TaskDependenciesPlan.md`): The implementation backlog tracker for this feature. Phase 0 entry must be marked complete.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `docs/Tasks/TaskDependencies.md` contains no legacy or misaligned terminology; all terminology audit checks pass.
- **SC-002**: `docs/Tasks/TaskDependencies.md` contains a section clearly delineating implemented vs. missing features (§3 or equivalent).
- **SC-003**: `docs/tmp/011-TaskDependenciesPlan.md` Phase 0 entry is updated to reflect completion.
- **SC-004**: All existing unit tests pass with zero regressions (`./tools/test_unit.sh` exit code 0).
