# Feature Specification: Step Ledger Checkpoint Durability

**Feature Branch**: `345-step-ledger-checkpoint-durability`
**Created**: 2026-05-13
**Status**: Draft
**Input**: User description: """
Use the Jira preset brief for MM-646 as the canonical Moon Spec orchestration input.

Additional constraints:

Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-646 MoonSpec Orchestration Input

## Source

- Jira issue: MM-646
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Step ledger & resume checkpoint durability in MoonMind.Run
- Labels: `moonmind-workflow-mm-a1fb7aa8-954b-4c59-acc2-c0a2c5339282`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-646 from MM project
Summary: Step ledger & resume checkpoint durability in MoonMind.Run
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-646 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-646: Step ledger & resume checkpoint durability in MoonMind.Run

Source Reference
Source Document: docs/Tasks/TaskArchitecture.md
Source Title: Task Architecture (Control Plane)
Source Sections:
- 8.1 Workflow responsibilities
- 8.5 Resume checkpoint responsibilities
- 12.1 MoonMind.Run
- Invariant 15
Coverage IDs:
- DESIGN-REQ-019
- DESIGN-REQ-023

As an execution-plane engineer, I want MoonMind.Run to durably record prepared input refs, per-step semantic output refs, and workspace/branch/commit checkpoints around step boundaries (idempotently and outside large inline workflow histories) so that Resume eligibility for any failed step is provable from durable evidence.

Acceptance Criteria
- After prepare succeeds, prepared input refs are recorded so prepare can be skipped on safe Resume.
- After each step succeeds, bounded step state + semantic output refs are recorded.
- Workspace/branch/commit checkpoints are recorded around step boundaries when runtime mutates state.
- Checkpoint writes are idempotent under activity/workflow-task retries.
- Large or binary checkpoint payloads stay outside inline workflow histories (artifact refs).
- Steps without recoverable output refs or state checkpoints are marked Resume-ineligible at the ledger level.

Requirements
Implement step ledger persistence, prepared-input ref recording, and idempotent checkpoint emission inside MoonMind.Run activities.
"""

<!-- Moon Spec specs contain exactly one independently testable user story. Use /speckit.breakdown for technical designs that contain multiple stories. -->

## Classification

- Input type: Single-story runtime feature request.
- Runtime decision: Jira Orchestrate always runs as a runtime implementation workflow, and `docs/Tasks/TaskArchitecture.md` is treated as runtime source requirements.
- Breakdown decision: `moonspec-breakdown` was not run because the MM-646 Jira preset brief defines one execution-plane story with one durable evidence goal, bounded acceptance criteria, and no competing independent stories.
- Resume decision: No existing Moon Spec artifact set for `MM-646` was found under `specs/`; specification was the first incomplete stage.

## User Story - Durable Step Evidence for Resume

**Summary**: As an execution-plane engineer, I want task runs to durably record prepared input refs, per-step output refs, and workspace checkpoints around step boundaries so that failed-step Resume eligibility is provable from durable evidence.

**Goal**: A task run produces enough bounded, durable evidence after preparation and after each completed step to determine whether completed work before a failed step can be safely preserved during Resume.

**Independent Test**: Execute representative task runs with successful preparation, successful steps, workspace-mutating steps, retried checkpoint writes, large checkpoint payloads, and completed steps missing recovery evidence; verify that durable refs and checkpoint evidence are recorded when required, remain bounded, are idempotent under retry, and drive Resume eligibility or ineligibility decisions.

**Acceptance Scenarios**:

1. **Given** task preparation succeeds and its prepared inputs can be reused safely, **When** the run records preparation results, **Then** the durable evidence identifies the prepared input refs needed to skip preparation during a later safe Resume.
2. **Given** a task step succeeds, **When** the run advances past the step boundary, **Then** bounded step state and semantic output refs are recorded for downstream steps and possible Resume preservation.
3. **Given** a step mutates workspace, branch, commit, or equivalent runtime state, **When** the step boundary is reached, **Then** a durable checkpoint identifies the recoverable state needed before the next step.
4. **Given** checkpoint recording is retried, **When** the same preparation or step boundary is processed again, **Then** checkpoint writes remain idempotent and continue to refer to the same recoverable evidence without duplicating or corrupting it.
5. **Given** checkpoint evidence contains large or binary payloads, **When** evidence is recorded or surfaced for Resume decisions, **Then** large or binary content remains behind refs and is not embedded inline in run histories or eligibility summaries.
6. **Given** a completed step lacks recoverable output refs or required state checkpoint evidence, **When** Resume eligibility is evaluated for preserving that step, **Then** the step is marked Resume-ineligible at the ledger level with a bounded reason.
7. **Given** a step delegates work to a child execution, **When** parent task evidence is recorded, **Then** the parent task run remains the source of truth for step ledger state and resume checkpoints.

### Edge Cases

- Preparation succeeds but a prepared input ref is missing, unavailable, unauthorized, or inconsistent with the source task.
- A completed step produces user-visible output but no semantic output ref that can be reused by downstream steps.
- A step mutates workspace state without a matching workspace, branch, commit, or equivalent checkpoint.
- A checkpoint write is retried after a transient activity or workflow-task retry.
- A large or binary checkpoint payload is accidentally included inline instead of behind a ref.
- A child execution completes successfully, but the parent run does not record parent-owned evidence needed for Resume.
- Multiple completed steps exist before a failed step, but only some have recoverable refs and checkpoints.

## Assumptions

- Existing authorization rules for task artifacts, run evidence, and recovery decisions apply to all durable refs and checkpoint evidence.
- This story covers producing and classifying durable evidence for Resume eligibility; detailed execution of a validated failed-step Resume is covered by adjacent Resume execution work.
- Evidence summaries may expose bounded metadata and refs, but not raw large payload bodies.

## Source Design Requirements

- **DESIGN-REQ-001** (Source: `docs/Tasks/TaskArchitecture.md` section 8.1, lines 463-472; original coverage ID DESIGN-REQ-019): `MoonMind.Run` must own durable state progression and preserve step ledger state and refs required for later Resume eligibility. Scope: in scope. Maps to FR-001, FR-002, FR-006, FR-007, FR-009.
- **DESIGN-REQ-002** (Source: `docs/Tasks/TaskArchitecture.md` section 8.5, lines 502-508): After preparation succeeds, the run must record prepared input refs needed to avoid repeating preparation during safe Resume. Scope: in scope. Maps to FR-001, FR-006, FR-007.
- **DESIGN-REQ-003** (Source: `docs/Tasks/TaskArchitecture.md` section 8.5, line 509): After each successful step, the run must record bounded step state and semantic output refs needed by downstream steps. Scope: in scope. Maps to FR-002, FR-006, FR-007.
- **DESIGN-REQ-004** (Source: `docs/Tasks/TaskArchitecture.md` section 8.5, lines 510-512): Around step boundaries where runtime state mutates, the run must record workspace, branch, commit, or equivalent checkpoints; checkpoint writes must be idempotent; and large or binary checkpoint content must remain behind refs. Scope: in scope. Maps to FR-003, FR-004, FR-005, FR-006.
- **DESIGN-REQ-005** (Source: `docs/Tasks/TaskArchitecture.md` section 8.5, line 513): A completed step without recoverable output refs or state checkpoint evidence is not eligible for Resume preservation. Scope: in scope. Maps to FR-007, FR-008.
- **DESIGN-REQ-006** (Source: `docs/Tasks/TaskArchitecture.md` section 12.1, lines 635-643; original coverage ID DESIGN-REQ-023): `MoonMind.Run` is the canonical parent workflow that produces step ledger state and resume checkpoints, including when individual steps delegate work to child executions. Scope: in scope. Maps to FR-009.
- **DESIGN-REQ-007** (Source: `docs/Tasks/TaskArchitecture.md` invariant 15, lines 620-621): Resume may be offered only when completed work before the failed step is recoverable from durable step refs and workspace or branch checkpoints. Scope: in scope. Maps to FR-006, FR-007, FR-008.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: After successful preparation, the run MUST durably record prepared input refs needed to skip preparation during a later safe Resume.
- **FR-002**: After each successful step, the run MUST durably record bounded step state and semantic output refs needed by downstream steps and possible Resume preservation.
- **FR-003**: When a step mutates workspace, branch, commit, or equivalent runtime state, the run MUST durably record a checkpoint at the associated step boundary.
- **FR-004**: Checkpoint writes MUST be idempotent across activity retries, workflow-task retries, and repeated processing of the same preparation or step boundary.
- **FR-005**: Large or binary checkpoint payloads MUST remain outside inline run histories, task input text, and eligibility summaries, with only durable refs and bounded metadata carried inline.
- **FR-006**: Resume eligibility evidence MUST be derived from durable prepared input refs, completed-step refs, semantic output refs, and workspace or branch checkpoint refs rather than logs or UI reconstruction.
- **FR-007**: The step ledger MUST identify which completed steps have recoverable refs and checkpoints that make them eligible for Resume preservation.
- **FR-008**: A completed step without recoverable output refs or required checkpoint evidence MUST be marked Resume-ineligible with a bounded operator-readable reason.
- **FR-009**: Parent task runs MUST remain the source of truth for step ledger state and resume checkpoint evidence even when a step delegates execution to a child run.
- **FR-010**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key `MM-646` and the original Jira preset brief.

### Key Entities

- **Prepared Input Refs**: Durable references to prepared task inputs that can be reused when safe during Resume.
- **Step Ledger Evidence**: Bounded per-step state, semantic output refs, eligibility markers, and reasons used to understand completed and failed step recovery state.
- **Semantic Output Refs**: Durable references to outputs needed by downstream steps or later Resume preservation.
- **Resume Checkpoint**: Durable evidence binding a step boundary to recoverable workspace, branch, commit, or equivalent runtime state.
- **Resume Eligibility Marker**: Step-level classification indicating whether a completed step has enough durable evidence to be preserved during Resume.
- **Parent Task Run**: The task run that owns step ledger and checkpoint evidence even when individual steps delegate work elsewhere.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of successful preparation cases in validation produce durable prepared input refs when those refs are required for safe Resume reuse.
- **SC-002**: 100% of successful step cases in validation produce bounded step state and semantic output refs before downstream preservation decisions depend on them.
- **SC-003**: 100% of workspace-mutating step cases in validation produce a durable workspace, branch, commit, or equivalent checkpoint at the relevant boundary.
- **SC-004**: 100% of checkpoint retry scenarios in validation preserve one logical checkpoint identity without duplicate or corrupted recovery evidence.
- **SC-005**: 0 large or binary checkpoint payloads are embedded inline in run histories, task input text, or eligibility summaries during validation.
- **SC-006**: 100% of completed-step cases missing recoverable refs or checkpoint evidence are marked Resume-ineligible with a bounded reason.
- **SC-007**: 100% of delegated-step validation cases preserve parent-owned step ledger and checkpoint evidence for Resume decisions.
- **SC-008**: Traceability review confirms `MM-646`, the original Jira preset brief, and source coverage IDs DESIGN-REQ-019 and DESIGN-REQ-023 remain preserved in MoonSpec artifacts and final verification evidence.
