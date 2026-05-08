# Feature Specification: Expose Distinct Full Retry Recovery Actions

**Feature Branch**: `326-expose-distinct-full-retry-recovery-actions`
**Created**: 2026-05-08
**Status**: Draft
**Input**: User description: """
Use the Jira preset brief for MM-632 as the canonical Moon Spec orchestration input.

Additional constraints:


Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-632 MoonSpec Orchestration Input

## Source

- Jira issue: MM-632
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Expose distinct full retry recovery actions
- Priority: Medium
- Trusted fetch tool: `jira.get_issue`
- Trusted response artifact: `/work/agent_jobs/mm:77a4c1a0-5a57-409a-bb21-9ddb871c2245/artifacts/moonspec-inputs/MM-632-trusted-jira-get-issue-summary.json`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira issue fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, `presetInstructions`, or `recommendedPresetInstructions`; potentially related custom fields `Implementation plan`, `Backout plan`, and `Test plan` were present but empty.
- Labels: moonmind-workflow-mm-86f66178-893d-469b-ba39-7bf1a3a19bb6
- Linked issues: MM-631, MM-633

## Canonical MoonSpec Feature Request

Jira issue: MM-632 from MM project
Summary: Expose distinct full retry recovery actions
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-632 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-632: Expose distinct full retry recovery actions

Source Reference
Source Document: docs/Tasks/TaskArchitecture.md
Source Title: Task Architecture (Control Plane)
Source Sections:
- 3.6 Failed-step resume is not full rerun
- 5.7 Failed-task recovery orchestration
- 7.1 Editable full retry
- 7.2 Exact full rerun
- 11 Invariants
Coverage IDs:
- DESIGN-REQ-012
- DESIGN-REQ-014

As a user recovering from failure, I want Edit task and Rerun to be explicit full-task retry choices so I can either change the authored task or retry it exactly without importing partial progress.

Acceptance Criteria
- Failed task details expose Edit task, Rerun, and Resume as separate actions only when backend capability fields are true.
- Edit task opens edit-for-rerun mode from the authoritative snapshot and permits normal task input edits.
- Edited full retry creates a new from-beginning execution with its own snapshot.
- Exact full rerun starts from the beginning using original task input unchanged.
- Neither full retry path imports completed progress from the failed source run.
- Original failed execution state remains immutable.

Requirements
- Edit task, exact full rerun, and resume are explicit intents with distinct data mutation and execution behavior.
- Resume cannot edit or silently mutate instructions, steps, attachments, runtime, publish mode, branch, dependencies, or preset metadata.
"""

<!-- Moon Spec specs contain exactly one independently testable user story. Use /speckit.breakdown for technical designs that contain multiple stories. -->

## User Story - Explicit Full-Task Recovery Choices

**Summary**: As a user recovering from a failed task, I want Edit task, Rerun, and Resume to appear as separate recovery choices only when each choice is actually available so that I can intentionally change the task, retry it exactly, or preserve completed progress without ambiguity.

**Goal**: Failed task recovery makes the user's intent explicit: Edit task permits authored input changes and starts from the beginning, Rerun retries the original task unchanged from the beginning, and Resume preserves completed progress only when durable recovery evidence exists.

**Independent Test**: Start from failed task details covering all combinations of Edit task, Rerun, and Resume availability; verify that the visible actions match the availability state, that Edit task and Rerun both create full-task retries without importing completed progress, that Edit task allows normal authoring changes while Rerun preserves original input unchanged, and that Resume remains unavailable or fails clearly when durable progress evidence is missing or inconsistent.

**Acceptance Scenarios**:

1. **Given** a failed task has full-retry recovery capabilities available, **When** the user views the failed task details, **Then** Edit task, Rerun, and Resume are presented as separate actions according to their individual availability.
2. **Given** the user selects Edit task on a failed execution, **When** the edit-for-rerun view opens, **Then** it is populated from the authoritative original task snapshot and permits normal task input edits before starting a new from-beginning execution.
3. **Given** the user submits an edited full retry, **When** the new execution is created, **Then** it has its own authoritative task input snapshot and does not import completed progress from the failed source run.
4. **Given** the user selects Rerun on a failed execution, **When** the exact full rerun starts, **Then** it starts from the beginning using the original task input unchanged.
5. **Given** either full retry path runs after a failed source execution, **When** recovery proceeds, **Then** completed progress from the failed source run is not imported into the new full-task execution.
6. **Given** a failed execution's durable resume evidence is missing, stale, unauthorized, or inconsistent, **When** failed-task recovery actions are evaluated, **Then** Resume is unavailable or fails with an operator-readable reason while full retry choices remain distinct.
7. **Given** a failed execution has immutable source state, **When** Edit task, Rerun, or Resume is selected, **Then** the original failed execution state, snapshot, step ledger, artifacts, and checkpoints remain unchanged.

### Edge Cases

- Only one or two of the recovery choices are available for a failed execution.
- A failed execution has an original task snapshot but no durable completed-progress checkpoint.
- Resume evidence exists but does not match the planned step graph for the failed execution.
- A user changes attachments, runtime, branch, dependencies, publish mode, or preset metadata while editing a full retry.
- A rerun request is attempted after source execution state has changed elsewhere.
- The recovery surface receives an unknown or incomplete availability state.

## Assumptions

- This story covers failed task details and recovery submission behavior for task-level recovery actions; the deeper durable checkpoint and resume execution mechanics are covered by adjacent Resume-specific work.
- Availability for each recovery action is determined by system-provided capability state rather than inferred from display text alone.
- Exact full rerun and edited full retry both start from the beginning; only Resume may preserve completed progress.
- Existing authorization and task ownership rules continue to apply to all recovery actions.

## Source Design Requirements

- **DESIGN-REQ-001** (Source: `docs/Tasks/TaskArchitecture.md` section 3.6, lines 101-110): Failed-task recovery must keep full-task retry and failed-step resume as separate explicit workflows; Edit task loads the original task snapshot for editable from-beginning retry, while Resume does not open authoring and must not silently edit task inputs. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-006, FR-009.
- **DESIGN-REQ-002** (Source: `docs/Tasks/TaskArchitecture.md` section 5.7, lines 206-224): Failed task details may expose Edit task, Rerun, and Resume as separate actions when capability state allows; Edit task and Rerun start from the beginning, while Resume requires durable evidence for completed prior work and must be rejected when evidence is missing, stale, unauthorized, or inconsistent. Scope: in scope. Maps to FR-001, FR-004, FR-005, FR-006, FR-007, FR-008, FR-010.
- **DESIGN-REQ-003** (Source: `docs/Tasks/TaskArchitecture.md` section 7.1, lines 373-384): Editable full retry must open from the authoritative task input snapshot, permit normal authoring edits, create a new from-beginning execution with its own snapshot, preserve the original failed execution state, and import no completed progress. Scope: in scope. Maps to FR-002, FR-003, FR-006, FR-007.
- **DESIGN-REQ-004** (Source: `docs/Tasks/TaskArchitecture.md` section 7.2, lines 386-395): Exact full rerun must reuse the original task input unchanged, start from the beginning, follow the normal execution path, and import no completed progress from the failed source run. Scope: in scope. Maps to FR-004, FR-005, FR-006.
- **DESIGN-REQ-005** (Source: `docs/Tasks/TaskArchitecture.md` section 7.3, lines 397-410): Resume must remain a failed-step recovery path that preserves completed work up to the failed step and is not an edit flow. Scope: out of scope except for action separation and preventing full retry paths from being treated as Resume; detailed Resume execution behavior belongs to Resume-specific work. Maps to FR-001, FR-009, FR-010.
- **DESIGN-REQ-006** (Source: `docs/Tasks/TaskArchitecture.md` section 11, lines 590-597): Snapshot-based durability and preset provenance durability require task snapshots to support attachment-aware edit and rerun and to preserve preset binding and final submitted order. Scope: in scope. Maps to FR-002, FR-003, FR-004, FR-007.
- **DESIGN-REQ-007** (Source: `docs/Tasks/TaskArchitecture.md` section 11, lines 614-624): Full rerun, edited full retry, and Resume must remain distinct intents; Resume preserves original inputs and requires checkpointed progress, while preserved steps must not be silently re-executed or imported into unrelated recovery paths. Scope: in scope. Maps to FR-001, FR-005, FR-006, FR-008, FR-009, FR-010.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Failed task details MUST represent Edit task, Rerun, and Resume as separate recovery intents with distinct labels, eligibility, and resulting behavior.
- **FR-002**: Edit task MUST open an editable full retry from the authoritative original task input snapshot.
- **FR-003**: Edit task MUST permit normal task input edits before submission, including instructions, steps, attachments, runtime, publish mode, branch, dependencies, and preset metadata subject to normal validation.
- **FR-004**: Rerun MUST start an exact full rerun from the authoritative original task input unchanged.
- **FR-005**: Exact full rerun MUST start from the beginning and follow the normal from-beginning execution path.
- **FR-006**: Edited full retry and exact full rerun MUST NOT import completed progress, preserved steps, resume checkpoints, or partial work from the failed source execution.
- **FR-007**: Edited full retry MUST create a new execution with its own authoritative task input snapshot.
- **FR-008**: Recovery action availability MUST expose each action only when its system-provided capability state is true.
- **FR-009**: Resume MUST NOT allow edits to instructions, steps, attachments, runtime, publish mode, branch, dependencies, or preset metadata.
- **FR-010**: Resume MUST be unavailable or fail with an operator-readable reason when required durable progress evidence is missing, stale, unauthorized, or inconsistent.
- **FR-011**: Any recovery action MUST preserve the original failed execution state, snapshot, step ledger, artifacts, and checkpoints unchanged.
- **FR-012**: Recovery behavior MUST fail visibly rather than silently translating a generic rerun request into Resume or a Resume request into a full-task retry.
- **FR-013**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key `MM-632` and the canonical Jira preset brief.

### Key Entities

- **Failed Execution**: The original task execution that ended in failure and exposes recovery choices.
- **Authoritative Task Input Snapshot**: The preserved original task input used to reconstruct Edit task and exact Rerun behavior.
- **Recovery Action Capability**: System-provided availability state indicating whether Edit task, Rerun, or Resume can be offered.
- **Edited Full Retry**: A from-beginning retry created after the user edits the original task input.
- **Exact Full Rerun**: A from-beginning retry using the original task input unchanged.
- **Resume Evidence**: Durable progress evidence required before the Resume action can preserve completed prior work.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of failed task detail views in the tested matrix show Edit task, Rerun, and Resume independently according to their individual capability states.
- **SC-002**: 100% of edited full retries in validation start from the authoritative snapshot, allow normal task input edits, and create a new snapshot for the new execution.
- **SC-003**: 100% of exact full reruns in validation reuse the original task input unchanged and start from the beginning.
- **SC-004**: 0 full retry paths import completed progress, preserved steps, or resume checkpoints from the failed source execution.
- **SC-005**: 100% of Resume-unavailable cases with missing, stale, unauthorized, or inconsistent evidence are hidden or rejected with an operator-readable reason.
- **SC-006**: 100% of recovery action attempts preserve the original failed execution state unchanged.
- **SC-007**: Traceability review confirms `MM-632`, the canonical Jira preset brief, and source coverage IDs DESIGN-REQ-012 and DESIGN-REQ-014 remain preserved across MoonSpec artifacts and final verification evidence.
