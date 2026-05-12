# Feature Specification: Backend-Computed Resume Eligibility

**Feature Branch**: `342-backend-resume-eligibility`
**Created**: 2026-05-12
**Status**: Draft
**Input**:

```text
# MM-643 MoonSpec Orchestration Input

## Source

- Jira issue: MM-643
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Distinct failed-task recovery actions with backend-computed Resume eligibility
- Priority: Medium
- Labels: `moonmind-workflow-mm-a1fb7aa8-954b-4c59-acc2-c0a2c5339282`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira issue fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, `presetInstructions`, or `recommendedPresetInstructions`.
- Trusted response artifact: `/work/agent_jobs/mm:5cfd8ca1-2db8-4730-bd92-9af2c68b9fb6/artifacts/moonspec-inputs/MM-643-trusted-jira-get-issue.json`

## Canonical MoonSpec Feature Request

Jira issue: MM-643 from MM project
Summary: Distinct failed-task recovery actions with backend-computed Resume eligibility
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-643 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-643: Distinct failed-task recovery actions with backend-computed Resume eligibility

Source Reference
Source Document: docs/Tasks/TaskArchitecture.md
Source Title: Task Architecture (Control Plane)
Source Sections:
- 3.6 Failed-step resume is not full rerun
- 5.7 Failed-task recovery orchestration
- Invariant 13
Coverage IDs:
- DESIGN-REQ-006
- DESIGN-REQ-013
- DESIGN-REQ-015

As a Mission Control user, I want failed-task details to expose Edit task, Rerun, and Resume as separate actions whose availability is computed by the backend so that I can select the correct recovery intent (edited full retry, exact full rerun, or resume from failed step) without the platform inferring Resume from a generic rerun.

Acceptance Criteria
- Backend exposes per-execution capability fields for editTask/rerun/resume.
- Resume eligibility requires snapshot, sourceWorkflowId, sourceRunId, ledger-identified failed step, completed-step refs, workspace checkpoint, and matching plan identity/digest.
- Missing/stale/unauthorized/inconsistent evidence produces an explicit rejection with operator-readable reason.
- UI never offers Resume from inferred state; capability flag governs availability.
- Recovery submissions carry TaskRecoveryProvenance with kind and pinned source workflow/run ids; Resume submissions also carry ResumeFromFailedStepRef.
- Generic rerun requests cannot be reinterpreted as Resume.

Requirements
Implement recovery capability endpoint, contract validation for recovery/resume payloads, and explicit rejection paths.
```

<!-- Moon Spec specs contain exactly one independently testable user story. Use /speckit.breakdown for technical designs that contain multiple stories. -->

## User Story - Backend-Governed Failed-Task Recovery Choices

**Summary**: As a Mission Control user recovering a failed task, I want Edit task, Rerun, and Resume to appear as separate actions based on backend-computed eligibility so that I can choose the correct recovery intent without the platform inferring Resume from a generic rerun.

**Goal**: Failed-task details expose clear recovery choices, with Resume available only when the system has durable evidence to restore completed work and retry the failed step, and with unsupported Resume requests rejected explicitly.

**Independent Test**: Can be fully tested by evaluating failed task details and recovery submissions across eligible, ineligible, stale, unauthorized, and inconsistent evidence cases, then confirming that displayed actions, rejection reasons, and submitted recovery intent records match the backend-computed capability state.

**Acceptance Scenarios**:

1. **Given** a failed execution has backend evidence for edit, rerun, and resume recovery, **When** a user views failed-task details, **Then** Edit task, Rerun, and Resume are exposed as separate actions according to independent capability values.
2. **Given** a failed execution lacks any required Resume evidence, **When** failed-task details are loaded, **Then** Resume is not offered and the system provides an operator-readable reason for the unavailable action.
3. **Given** Resume evidence is stale, unauthorized, inconsistent, or does not match the planned step graph, **When** a Resume action is evaluated or submitted, **Then** the system rejects Resume explicitly before recovery work starts.
4. **Given** a user submits an exact rerun or edited full retry, **When** the request is processed, **Then** the system records that full-task recovery intent and does not reinterpret the request as Resume.
5. **Given** a user submits Resume for a failed execution, **When** the request is accepted, **Then** the submitted recovery data pins the source workflow and run, identifies the failed step, and references the required snapshot, completed-step evidence, checkpoint, and plan identity or digest.
6. **Given** a user changes instructions, steps, attachments, runtime, publish mode, branch, presets, or dependencies, **When** the changed task is submitted, **Then** the system treats it as edited full retry rather than Resume.

### Edge Cases

- A failed execution supports Edit task and Rerun but has no recoverable completed-step evidence for Resume.
- The failed step can be identified, but one or more completed prior steps have no durable refs.
- The source workflow is known, but the pinned run is missing, superseded, or unauthorized for the current user.
- A workspace, branch, commit, or equivalent checkpoint exists but does not correspond to the failed step's planned graph.
- The recovery submission omits required provenance or resume reference data.
- A generic rerun request is submitted with partial Resume-shaped data.

## Assumptions

- This story covers backend-computed capability state, recovery intent validation, and operator-visible eligibility reasons; full execution of a resumed failed step is covered by adjacent Resume execution work.
- Existing authorization and task ownership rules apply to all recovery evidence checks.
- Mission Control consumes backend capability state directly and does not derive Resume availability from labels, local heuristics, or a generic rerun status.

## Source Design Requirements

- **DESIGN-REQ-001** (Source: `docs/Tasks/TaskArchitecture.md` section 3.6, lines 101-110; original coverage ID DESIGN-REQ-006): Failed-task recovery must keep full-task retry and failed-step Resume as separate explicit workflows; Resume must use original task input and durable prior work, and must be unavailable or fail explicitly when prior work cannot be restored faithfully. Scope: in scope. Maps to FR-001, FR-002, FR-004, FR-005, FR-006, FR-009.
- **DESIGN-REQ-002** (Source: `docs/Tasks/TaskArchitecture.md` section 5.7, lines 206-224; original coverage ID DESIGN-REQ-013): Failed task details may expose Edit task, Rerun, and Resume separately only when capability fields are true; Resume eligibility must be computed by the backend and requires an original snapshot, pinned source workflow and run, failed-step ledger identity, durable completed-step refs, checkpoint state, and matching plan identity or digest. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-005, FR-006, FR-007, FR-008.
- **DESIGN-REQ-003** (Source: `docs/Tasks/TaskArchitecture.md` representative recovery contract, lines 296-321): Recovery submissions must carry structured recovery provenance, and Resume submissions must additionally carry the failed-step reference and required resume evidence references. Scope: in scope. Maps to FR-005, FR-006, FR-007, FR-008.
- **DESIGN-REQ-004** (Source: `docs/Tasks/TaskArchitecture.md` invariant 13, lines 614-615; original coverage ID DESIGN-REQ-015): Full rerun, edited full retry, and Resume must remain distinct intents, and the system must not infer Resume from a generic rerun request. Scope: in scope. Maps to FR-001, FR-004, FR-009.
- **DESIGN-REQ-005** (Source: `docs/Tasks/TaskArchitecture.md` invariants 14-17, lines 617-627): Resume must preserve original inputs, require checkpointed progress, avoid silent re-execution of preserved steps, and pin both source workflow and source run. Scope: in scope. Maps to FR-002, FR-005, FR-006, FR-007, FR-008.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Failed task details MUST represent Edit task, Rerun, and Resume as separate recovery intents with independent availability values.
- **FR-002**: Resume availability MUST be computed by the backend and MUST NOT be inferred by Mission Control from display text, generic rerun state, or locally reconstructed evidence.
- **FR-003**: Backend-computed recovery capability state MUST include per-execution availability for Edit task, Rerun, and Resume.
- **FR-004**: Generic rerun and edited full retry submissions MUST NOT be reinterpreted as Resume.
- **FR-005**: Accepted recovery submissions MUST include recovery provenance identifying the recovery kind and pinned source workflow and run.
- **FR-006**: Accepted Resume submissions MUST include a failed-step resume reference identifying the source workflow, source run, failed step, resume checkpoint, task input snapshot, and applicable plan identity or digest.
- **FR-007**: Resume eligibility MUST require an authoritative original task input snapshot, pinned source workflow and run, a ledger-identified failed step, durable refs for completed prior steps, a workspace, branch, commit, or equivalent checkpoint, and matching plan identity or digest.
- **FR-008**: Missing, stale, unauthorized, or inconsistent Resume evidence MUST produce an explicit rejection or unavailable-action reason that an operator can understand before recovery work starts.
- **FR-009**: Resume MUST preserve original task inputs; any user change to instructions, steps, attachments, runtime, publish mode, branch, presets, or dependencies MUST require edited full retry instead.
- **FR-010**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key `MM-643` and the original Jira preset brief.

### Key Entities

- **Failed Execution**: The source task execution that ended in failure and may expose recovery choices.
- **Recovery Capability State**: Backend-computed per-execution availability and reason data for Edit task, Rerun, and Resume.
- **Recovery Provenance**: Submitted recovery intent data that identifies the recovery kind and pins the source workflow and run.
- **Resume Evidence**: Durable records required for Resume, including original task snapshot, failed-step identity, completed-step refs, checkpoint state, and plan identity or digest.
- **Unavailable Reason**: Operator-readable explanation for why a recovery action, especially Resume, cannot be offered or accepted.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of tested failed-task detail states display Edit task, Rerun, and Resume according to backend-provided capability values.
- **SC-002**: 100% of tested Resume-unavailable cases with missing, stale, unauthorized, or inconsistent evidence include an operator-readable unavailable or rejection reason.
- **SC-003**: 100% of accepted Resume submissions in validation include pinned source workflow and run, failed-step identity, required snapshot and checkpoint refs, completed-step refs, and plan identity or digest.
- **SC-004**: 0 generic rerun or edited full retry submissions in validation are interpreted as Resume.
- **SC-005**: 100% of tested task-input edits route to edited full retry behavior rather than Resume.
- **SC-006**: Traceability review confirms `MM-643`, the original Jira preset brief, and source coverage IDs DESIGN-REQ-006, DESIGN-REQ-013, and DESIGN-REQ-015 remain preserved in MoonSpec artifacts and final verification evidence.
