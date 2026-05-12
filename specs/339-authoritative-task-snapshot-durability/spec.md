# Feature Specification: Authoritative Task Snapshot Durability

**Feature Branch**: `339-authoritative-task-snapshot-durability`
**Created**: 2026-05-11
**Status**: Draft
**Input**: User description: """
Use the Jira preset brief for MM-639 as the canonical Moon Spec orchestration input.

Additional constraints:


Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-639 MoonSpec Orchestration Input

## Source

- Jira issue: MM-639
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Authoritative task input snapshot durability for edit/rerun/resume
- Priority: Medium
- Labels: moonmind-workflow-mm-a1fb7aa8-954b-4c59-acc2-c0a2c5339282
- Trusted fetch tool: `jira.get_issue`
- Trusted response artifact: `/work/agent_jobs/mm:e2bc69b6-9268-42f5-8b6d-deec1caaeb08/artifacts/moonspec-inputs/MM-639-trusted-jira-get-issue.json`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, `presetInstructions`, or `recommendedPresetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-639 from MM project
Summary: Authoritative task input snapshot durability for edit/rerun/resume
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-639 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-639: Authoritative task input snapshot durability for edit/rerun/resume

Source Reference
Source Document: docs/Tasks/TaskArchitecture.md
Source Title: Task Architecture (Control Plane)
Source Sections:
- 3.4 Durable reconstruction
- 5.5 Snapshot durability
- 7 Snapshot, full retry, and Resume architecture
Coverage IDs:
- DESIGN-REQ-004
- DESIGN-REQ-011

As a platform owner, I want every submitted task to persist an authoritative task input snapshot (objective text, step text/order/identity, attachment refs by target, runtime/publish, repository + single authored branch, preset application metadata, pinned preset bindings, include-tree summary, per-step provenance, detachment state, final submitted order, dependencies) so that edit, exact full rerun, edited full retry, and Resume can reconstruct the original authored task input without depending on the live preset catalog or lossy projections.

Acceptance Criteria
- Each submitted execution writes one authoritative task input snapshot artifact whose schema covers every field listed in Section 7.
- Snapshot reconstruction is independent of the live preset catalog state.
- Attachment-aware executions without a reconstructible snapshot are flagged as degraded explicitly.
- Snapshot identity is referenced from the execution and re-used unchanged for exact full rerun and Resume.
- Editing the snapshot during Resume is rejected; only edited full retry produces a new snapshot.

Requirements
Persist snapshot via artifact store with schema versioning; expose lookup by execution; expose degraded-state flag in execution metadata.
"""

## User Story - Durable Task Input Snapshot

**Summary**: As a platform owner, I want every submitted task to preserve an authoritative task input snapshot so that edit, exact full rerun, edited full retry, and Resume can reconstruct the authored task without relying on mutable catalogs or lossy projections.

**Goal**: Submitted tasks retain a durable, complete, and immutable representation of the authored task input that downstream recovery actions can use consistently.

**Independent Test**: Can be fully tested by submitting tasks that include objective text, ordered steps, attachments, runtime and publish selections, repository and branch choices, preset metadata, provenance, and dependencies, then confirming edit, exact full rerun, edited full retry, and Resume reconstruct or reuse the original authored input according to their distinct recovery intent.

**Acceptance Scenarios**:

1. **Given** a user submits a task with objective text, ordered steps, runtime and publish selections, repository and branch choices, preset-derived steps, and dependencies, **When** the task is accepted for execution, **Then** the system records one authoritative task input snapshot that contains all authored task fields needed for future reconstruction.
2. **Given** a submitted task includes objective-scoped and step-scoped attachments, **When** the task snapshot is inspected or used for reconstruction, **Then** attachment references remain bound to their original objective or step targets without silent loss or retargeting.
3. **Given** the live preset catalog has changed since a task was submitted, **When** edit, exact full rerun, edited full retry, or Resume needs the original task input, **Then** reconstruction uses the authoritative snapshot and does not depend on current preset catalog correctness.
4. **Given** a user chooses exact full rerun, **When** the rerun starts, **Then** the original authoritative task input snapshot is reused unchanged and no completed execution progress is imported.
5. **Given** a user chooses edited full retry, **When** the edited task is submitted, **Then** the new execution receives its own authoritative task input snapshot and the original execution evidence remains unchanged.
6. **Given** a user chooses Resume from a failed step, **When** Resume is accepted, **Then** the original task input snapshot is reused unchanged while prior completed work is preserved only from durable checkpoint evidence.
7. **Given** an attachment-aware execution lacks a reconstructible authoritative task input snapshot, **When** a recovery action is evaluated, **Then** the system classifies the state as degraded explicitly rather than silently dropping, retargeting, or synthesizing attachment state.

### Edge Cases

- Snapshot reconstruction must fail or mark the execution degraded when required authored task fields are missing, inconsistent, unauthorized, or unreadable.
- Resume must reject attempts to edit instructions, steps, attachments, runtime, publish mode, branch, presets, or dependencies; those changes require edited full retry.
- Recovery actions must preserve the distinction between exact full rerun, edited full retry, and Resume, even when the same source execution offers more than one action.
- Preset-derived and manually reordered steps must retain their final submitted order, identity, provenance, pinned bindings, include-tree summary, and detachment state.

## Assumptions

- Input classification: single-story runtime feature request. The brief contains one actor, one durable reconstruction goal, one acceptance set, and one bounded source document reference.
- Existing authorization and artifact access policies continue to apply to snapshot reads and recovery action eligibility.
- The canonical source design for this story is `docs/Tasks/TaskArchitecture.md`, sections 3.4, 5.5, and 7, with linked invariants used to clarify recovery intent.

## Source Design Requirements

- **DESIGN-REQ-004** (`docs/Tasks/TaskArchitecture.md`, section 3.4): Task input reconstruction MUST use an authoritative snapshot; text-only reconstruction is insufficient for attachment-aware tasks, and silent attachment-binding loss is a contract violation. Scope: in scope. Maps to FR-001, FR-002, FR-006, FR-007, and FR-008.
- **DESIGN-REQ-011** (`docs/Tasks/TaskArchitecture.md`, section 7): The original task input snapshot MUST preserve objective text, attachment refs by target, step text, step identity and order, runtime and publish selections, repository and branch selection, preset metadata, pinned preset bindings, include-tree summary, per-step provenance, detachment state, final submitted order, and dependency declarations. Scope: in scope. Maps to FR-001 through FR-008.
- **DESIGN-REQ-012** (`docs/Tasks/TaskArchitecture.md`, sections 7.1 through 7.3): Exact full rerun, edited full retry, and Resume MUST remain distinct recovery intents with different snapshot and progress-preservation behavior. Scope: in scope. Maps to FR-003, FR-004, FR-005, and FR-006.
- **DESIGN-REQ-013** (`docs/Tasks/TaskArchitecture.md`, section 11, invariants 5, 7, 13, and 14): Snapshot-based durability, preset provenance durability, explicit recovery intent, and unchanged Resume inputs MUST remain invariant across supported recovery flows. Scope: in scope. Maps to FR-001 through FR-007.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST record one authoritative task input snapshot for each submitted execution before recovery actions depend on the execution.
- **FR-002**: The authoritative snapshot MUST include objective text, objective-scoped attachment refs, step text, step-scoped attachment refs, step order, step identity, runtime selections, publish selections, repository selection, single authored branch selection, dependency declarations, preset application metadata, pinned preset bindings, include-tree summary, per-step provenance, detachment state, and final submitted order.
- **FR-003**: Snapshot-based reconstruction MUST be independent of live preset catalog state, live template definitions, rendered logs, execution projections, partial summaries, or other lossy derived views.
- **FR-004**: Exact full rerun MUST reuse the original authoritative task input snapshot unchanged and MUST NOT import completed execution progress from the source run.
- **FR-005**: Edited full retry MUST create a new execution with its own authoritative task input snapshot while preserving the original execution snapshot, step evidence, artifacts, and checkpoints unchanged.
- **FR-006**: Resume MUST reuse the original authoritative task input snapshot unchanged and MUST NOT expose task input fields as editable during Resume.
- **FR-007**: Attachment-aware executions without a reconstructible authoritative task input snapshot MUST be classified as degraded explicitly and MUST NOT silently drop attachments, retarget attachments, or synthesize replacement authoring state.
- **FR-008**: Recovery eligibility and reconstruction outcomes MUST expose whether an authoritative snapshot exists and whether the execution is degraded due to missing or unreconstructible snapshot data.
- **FR-009**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key `MM-639` and the canonical Jira preset brief for traceability.

### Key Entities

- **Authoritative Task Input Snapshot**: Durable representation of the authored task input, including objective, steps, attachment targets, runtime and publish selections, repository and branch choice, preset metadata, provenance, final order, and dependencies.
- **Recovery Action**: User-visible action that reconstructs or reuses task input after a source execution, including edit, exact full rerun, edited full retry, and Resume.
- **Degraded Reconstruction State**: Explicit state indicating that recovery cannot faithfully reconstruct the authored task input from a required authoritative snapshot.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For 100% of newly submitted executions in covered task flows, a retrievable authoritative task input snapshot is associated with the execution before recovery actions are evaluated.
- **SC-002**: In test scenarios with changed live preset definitions, edit, exact full rerun, edited full retry, and Resume reconstruct or reuse the original authored task input from the snapshot with zero dependence on the current preset catalog.
- **SC-003**: In attachment-aware recovery tests, 100% of objective-scoped and step-scoped attachment refs retain their original target binding after reconstruction or are blocked by an explicit degraded state.
- **SC-004**: Exact full rerun, edited full retry, and Resume each demonstrate distinct behavior in validation evidence: unchanged full rerun input, new edited retry snapshot, and unchanged Resume input with preserved prior progress.
- **SC-005**: Missing or unreconstructible snapshot scenarios produce an explicit degraded or rejected outcome in every covered recovery path, with no silent attachment loss or synthesized authoring state.
- **SC-006**: Traceability review confirms `MM-639`, the canonical Jira preset brief, and DESIGN-REQ-004/DESIGN-REQ-011/DESIGN-REQ-012/DESIGN-REQ-013 remain preserved in MoonSpec artifacts and final verification evidence.
