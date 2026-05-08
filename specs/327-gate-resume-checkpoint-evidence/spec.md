# Feature Specification: Gate Resume on Durable Checkpoint Evidence

**Feature Branch**: `327-gate-resume-checkpoint-evidence`
**Created**: 2026-05-08
**Status**: Draft
**Input**: User description: """
Use the Jira preset brief for MM-633 as the canonical Moon Spec orchestration input.

Additional constraints:


Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-633 MoonSpec Orchestration Input

## Source

- Jira issue: MM-633
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Gate Resume on durable checkpoint evidence
- Labels: moonmind-workflow-mm-86f66178-893d-469b-ba39-7bf1a3a19bb6
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira issue fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, `presetInstructions`, or `recommendedPresetInstructions`; related custom fields `Implementation plan`, `Backout plan`, and `Test plan` were present but empty.
- Trusted response artifact: `/work/agent_jobs/mm:be31223e-5ff3-4a5b-a8ef-b41922d005eb/artifacts/moonspec-inputs/MM-633-trusted-jira-get-issue-expanded.json`

## Canonical MoonSpec Feature Request

Jira issue: MM-633 from MM project
Summary: Gate Resume on durable checkpoint evidence
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-633 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-633: Gate Resume on durable checkpoint evidence

Source Reference
Source Document: docs/Tasks/TaskArchitecture.md
Source Title: Task Architecture (Control Plane)
Source Sections:
- 5.7 Failed-task recovery orchestration
- 7.3 Resume from failed step
- 8.5 Resume checkpoint responsibilities
- 11 Invariants
Coverage IDs:
- DESIGN-REQ-013
- DESIGN-REQ-016

As an operator, I want Resume offered only when MoonMind can prove the failed step and completed work are recoverable from pinned run evidence.

Acceptance Criteria
- Resume eligibility is computed by the backend, not inferred by UI.
- Eligibility requires original snapshot, pinned source workflowId/runId, failed-step ledger identity, completed-step refs, workspace/branch/commit checkpoint, and plan identity/digest.
- Checkpoint creation and writes are idempotent.
- Large or binary checkpoint content remains behind refs.
- Resume requests fail explicitly before execution when evidence is missing, stale, unauthorized, corrupted, or inconsistent.

Requirements
- Resume requires backend evidence: snapshot, pinned workflow/run IDs, failed-step ledger state, completed outputs, checkpoint, and plan identity.
- Missing, stale, unauthorized, corrupted, or inconsistent resume evidence must block Resume or fail before execution, never full-rerun silently.

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
- Breakdown decision: `moonspec-breakdown` was not run because the MM-633 Jira preset brief defines one operator story with one recovery eligibility goal, bounded evidence requirements, and explicit failure behavior.
- Resume decision: No existing Moon Spec artifact set for `MM-633` was found under `specs/`; specification was the first incomplete stage.

## User Story - Evidence-Gated Resume Eligibility

**Summary**: As an operator recovering from a failed task, I want Resume to be offered only when MoonMind can prove the failed step and completed prior work are recoverable from durable, pinned evidence so that a resume attempt never silently falls back to an unsafe full rerun or partial reconstruction.

**Goal**: Failed-step Resume is gated by backend-verified evidence for the original task snapshot, pinned source workflow and run, failed-step identity, completed-step refs, workspace or branch checkpoint, and plan identity before the action is exposed or executed.

**Independent Test**: Create failed task recovery cases with complete, missing, stale, unauthorized, corrupted, and inconsistent resume evidence; verify that only complete evidence enables Resume, that valid Resume requests carry pinned source and checkpoint identity, and that invalid requests fail before execution with an operator-readable reason while preserving large or binary content behind refs.

**Acceptance Scenarios**:

1. **Given** a failed task has a complete original snapshot, pinned source workflow and run, failed-step ledger identity, completed-step refs, checkpoint, and plan identity, **When** backend recovery eligibility is evaluated, **Then** Resume is eligible and the eligibility result identifies the durable evidence used.
2. **Given** a failed task lacks any required resume evidence, **When** backend recovery eligibility is evaluated, **Then** Resume is not eligible and the result explains the missing evidence without relying on UI inference.
3. **Given** a Resume request is submitted with missing, stale, unauthorized, corrupted, or inconsistent evidence, **When** execution would otherwise start, **Then** the request fails explicitly before executing the failed step.
4. **Given** completed prior steps have recoverable output refs and workspace or branch checkpoint evidence, **When** Resume eligibility is computed, **Then** those steps may be preserved by ref rather than re-executed.
5. **Given** completed prior steps have large or binary checkpoint content, **When** checkpoint evidence is recorded or evaluated, **Then** large or binary content remains behind durable refs and is not embedded in workflow history or inline eligibility payloads.
6. **Given** checkpoint creation or checkpoint writes are retried, **When** the same recovery boundary is processed again, **Then** checkpoint records remain idempotent and continue to identify the same recoverable state.
7. **Given** plan identity or digest does not match the failed source run, **When** Resume eligibility is evaluated or a Resume request is submitted, **Then** Resume is blocked before execution as inconsistent evidence.

### Edge Cases

- The original task input snapshot exists, but the source `workflowId` or `runId` is missing or no longer matches the failed run.
- The failed-step ledger contains no single last failed step or contains a step identity that does not belong to the plan graph.
- A completed step succeeded but has no durable output refs or no recoverable workspace or branch checkpoint.
- A checkpoint ref points to content that is unavailable, unauthorized for the requester, corrupted, or inconsistent with the source run.
- The plan identity or digest changed between the source run and the resume attempt.
- A checkpoint write is retried after an activity or workflow task retry.
- The recovery surface receives a stale eligibility result after source run evidence has changed.

## Assumptions

- This story covers backend Resume eligibility, durable evidence gating, and pre-execution rejection behavior; detailed Resume execution after a valid checkpoint is loaded remains bounded to the adjacent failed-step Resume execution story.
- Existing authorization rules for task details, artifacts, and execution recovery apply to all resume evidence reads.
- Evidence refs are operator-visible enough to support diagnostics, but the spec does not require exposing large checkpoint contents inline.

## Source Design Requirements

- **DESIGN-REQ-001** (Source: `docs/Tasks/TaskArchitecture.md` section 5.7, lines 206-224): Resume eligibility must be computed by the backend and require an authoritative original snapshot, pinned source workflow/run identity, failed-step ledger identity, completed-step refs, checkpoint state, plan identity, and explicit rejection when required evidence is missing, stale, unauthorized, or inconsistent. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-011.
- **DESIGN-REQ-002** (Source: `docs/Tasks/TaskArchitecture.md` section 7.3, lines 397-450): Resume must pin the source execution, identify the last failed step, create or resolve a checkpoint containing completed-step refs and workspace or branch state, preserve prior progress instead of re-executing it, and fail before executing when restoration evidence is incomplete, corrupted, unauthorized, or inconsistent. Scope: in scope for eligibility and pre-execution gating; detailed resumed execution of later steps is out of scope. Maps to FR-001, FR-003, FR-004, FR-005, FR-006, FR-007, FR-011, FR-012.
- **DESIGN-REQ-003** (Source: `docs/Tasks/TaskArchitecture.md` section 8.5, lines 502-512): The execution plane must record prepared input refs, bounded step state, semantic output refs, workspace or branch checkpoints, idempotent checkpoint writes, and external refs for large or binary checkpoint content; steps without recoverable refs or checkpoints are not eligible for preservation. Scope: in scope. Maps to FR-004, FR-007, FR-008, FR-009, FR-010.
- **DESIGN-REQ-004** (Source: `docs/Tasks/TaskArchitecture.md` section 11, lines 574-624): System invariants require snapshot-based durability, explicit recovery intent, unchanged original inputs for Resume, checkpointed progress before Resume is offered, no silent re-execution of preserved steps, and pinned source workflow/run identity. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004, FR-006, FR-011, FR-012, FR-013.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Resume eligibility MUST be computed by backend recovery logic rather than inferred by the UI.
- **FR-002**: Resume eligibility MUST require an authoritative original task input snapshot for the failed source execution.
- **FR-003**: Resume eligibility MUST require pinned source `workflowId` and `runId` values for the failed source execution.
- **FR-004**: Resume eligibility MUST require a failed-step ledger identity that uniquely identifies the last failed step in the source run's planned step graph.
- **FR-005**: Resume eligibility MUST require durable refs for every completed step that would be preserved before the failed step.
- **FR-006**: Resume eligibility MUST require a workspace, branch, commit, or equivalent checkpoint representing recoverable state immediately before the failed step.
- **FR-007**: Resume eligibility MUST require a plan identity or digest proving that preserved progress belongs to the same planned step graph.
- **FR-008**: Checkpoint creation and checkpoint writes MUST be idempotent across activity and workflow retries.
- **FR-009**: Large or binary checkpoint content MUST remain behind durable refs and MUST NOT be embedded inline in workflow history, eligibility payloads, or task input text.
- **FR-010**: A completed step without recoverable output refs or state checkpoint evidence MUST NOT be eligible for Resume preservation.
- **FR-011**: Resume requests MUST fail explicitly before executing the failed step when required evidence is missing, stale, unauthorized, corrupted, or inconsistent.
- **FR-012**: Resume recovery MUST NOT silently fall back to full rerun behavior when evidence validation fails.
- **FR-013**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key `MM-633` and the canonical Jira preset brief.

### Key Entities

- **Failed Source Execution**: The original failed task run whose workflow and run identity must be pinned for Resume.
- **Original Task Input Snapshot**: The authoritative immutable task input captured for the source execution.
- **Failed-Step Ledger Identity**: The durable identity of the last failed step within the planned step graph.
- **Completed-Step Refs**: Durable refs for outputs and bounded state produced by steps before the failed step.
- **Resume Checkpoint**: The recoverable state record that binds source execution, plan identity, completed-step refs, prepared inputs, and workspace or branch state.
- **Plan Identity**: A stable plan identifier or digest used to prove preserved progress belongs to the same planned step graph.
- **Resume Eligibility Result**: Backend-computed availability and diagnostic state explaining whether Resume can be offered or must be blocked.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of eligibility evaluations in the test matrix are computed from backend evidence fields rather than UI-only state.
- **SC-002**: 100% of valid Resume-eligible cases include original snapshot, pinned workflow/run IDs, failed-step identity, completed-step refs, checkpoint state, and plan identity.
- **SC-003**: 100% of missing, stale, unauthorized, corrupted, or inconsistent evidence cases block Resume or fail before execution with an operator-readable reason.
- **SC-004**: 0 invalid Resume requests silently become full reruns or re-execute preserved prior steps.
- **SC-005**: 100% of checkpoint retry scenarios preserve idempotent checkpoint identity and do not duplicate or corrupt evidence.
- **SC-006**: 0 large or binary checkpoint payloads are embedded inline in workflow history, eligibility payloads, or task input text during validation.
- **SC-007**: Traceability review confirms `MM-633`, the canonical Jira preset brief, and source coverage IDs DESIGN-REQ-013 and DESIGN-REQ-016 remain preserved across MoonSpec artifacts and final verification evidence.
