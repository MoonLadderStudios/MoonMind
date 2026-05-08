# Feature Specification: Persist Authoritative Task Snapshots

**Feature Branch**: `323-persist-task-snapshots`
**Created**: 2026-05-08
**Status**: Draft
**Input**: User description: """
Use the Jira preset brief for MM-629 as the canonical Moon Spec orchestration input.

Additional constraints:

Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): docs/Tasks/TaskArchitecture.md.

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-629 MoonSpec Orchestration Input

## Source

- Jira issue: MM-629
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Persist authoritative task snapshots for reconstruction
- Priority: Medium
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, `presetInstructions`, or `recommendedPresetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-629 from MM project
Summary: Persist authoritative task snapshots for reconstruction
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-629 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-629: Persist authoritative task snapshots for reconstruction

Source Reference
Source Document: docs/Tasks/TaskArchitecture.md
Source Title: Task Architecture (Control Plane)
Source Sections:
- 3.4 Durable reconstruction
- 5.5 Snapshot durability
- 7 Snapshot, full retry, and Resume architecture
- 11 Invariants

Coverage IDs:
- DESIGN-REQ-004
- DESIGN-REQ-010

As a user retrying or reviewing a task, I want MoonMind to reconstruct the original authored task from a durable snapshot so edit, rerun, and resume flows cannot lose task intent.

Acceptance Criteria:
- Submission persists an authoritative task input snapshot with all section 7 fields.
- Edit and full retry initial browser state comes from the snapshot, not derived projections.
- Snapshot reconstruction preserves attachment target binding exactly.
- Snapshot reconstruction preserves pinned preset bindings, include-tree summary, per-step provenance, detachment state, and final submitted order.
- Attachment-aware executions without reconstructible snapshots are explicitly degraded.

Requirements:
- Edit, rerun, full retry, and resume reconstruct authored input from durable snapshots rather than lossy projections.
- Snapshots and payloads preserve authored preset bindings, include-tree summaries, source provenance, detachment state, and final order.

Relevant Jira links from trusted issue response:
- MM-629 blocks MM-628: Route binary inputs through authorized artifact refs (status: Done)
- MM-629 is blocked by MM-630: Compile recursive task presets before execution (status: Backlog)
"""

## Classification

Input classification: single-story runtime feature request. The Jira brief selects one independently testable task reconstruction and retry story from `docs/Tasks/TaskArchitecture.md`; it does not require `moonspec-breakdown`.

Resume decision: no existing Moon Spec feature directory or checked-in spec artifact matched `MM-629` under `specs/`, so `Specify` is the first incomplete stage.

## User Story - Authoritative Task Snapshot Reconstruction

**Summary**: As a user retrying or reviewing a task, I want MoonMind to reconstruct the original authored task from a durable snapshot so edit, rerun, and resume flows cannot lose task intent.

**Goal**: Preserve the complete authored task input as the authoritative source for reconstruction so all recovery and review flows retain the user's original intent, attachment targets, preset provenance, and submitted ordering.

**Independent Test**: Submit a task with objective text, ordered steps, target-bound attachments, runtime and publish selections, pinned preset metadata, provenance, dependencies, and detached state; then open edit, full retry, exact rerun, and resume views and verify each reconstructs from the authoritative snapshot with no field loss or retargeting.

**Acceptance Scenarios**:
1. **Given** a submitted task with objective-scoped and step-scoped attachments, **When** the task is persisted, **Then** the authoritative snapshot contains the task objective, ordered steps, attachment refs and targets, runtime selections, publish selections, branch selection, preset metadata, provenance, detachment state, dependencies, and final submitted order.
2. **Given** an existing execution with an authoritative snapshot, **When** a user opens edit or full retry, **Then** the initial browser state is reconstructed from that snapshot rather than from derived execution projections.
3. **Given** an existing execution with pinned preset bindings and flattened steps, **When** reconstruction occurs, **Then** the preset bindings, include-tree summary, per-step provenance, detachment state, and final submitted order remain visible and intact without live preset catalog lookup.
4. **Given** an attachment-aware execution without a reconstructible authoritative snapshot, **When** edit, rerun, full retry, or resume reconstruction is requested, **Then** the system explicitly reports the execution as degraded instead of silently dropping attachments or retargeting inputs.
5. **Given** a failed execution with completed prior steps and an authoritative snapshot, **When** resume is evaluated, **Then** resume uses the original task input snapshot unchanged and does not present it as an editable authoring surface.

**Edge Cases**:
- A task contains only text fields and no attachments but still requires preset provenance and ordering to reconstruct accurately.
- A task references attachments whose artifact metadata exists but whose task target binding is missing from the snapshot.
- A preset used by the original task has changed or been removed after submission.
- A user reorders or edits steps during full retry while existing attachments remain bound to their original targets unless explicitly changed through normal authoring controls.
- Resume is requested for a run whose checkpoint exists but whose original task input snapshot is missing, corrupted, or unauthorized.

## Assumptions

- Existing artifact authorization and attachment upload behavior remain the source of binary access control; this story concerns the durable task input snapshot and reconstruction semantics.
- Existing edit, rerun, full retry, and resume entrypoints remain the operator-facing recovery surfaces.
- Jira-linked MM-630 may add recursive preset compilation details later, but this story preserves the compiled bindings and provenance that are present at submission time.

## Source Design Requirements

- **DESIGN-REQ-001** (`docs/Tasks/TaskArchitecture.md` section 3.4): Task input reconstruction must use an authoritative snapshot; text-only reconstruction is insufficient for attachment-aware tasks. Scope: in scope, mapped to FR-001, FR-002, FR-006.
- **DESIGN-REQ-002** (`docs/Tasks/TaskArchitecture.md` section 3.4): Silent loss of attachment bindings is a contract violation. Scope: in scope, mapped to FR-004, FR-006, FR-008.
- **DESIGN-REQ-003** (`docs/Tasks/TaskArchitecture.md` section 5.5): Edit and rerun must reconstruct from the authoritative snapshot rather than lossy derived projections. Scope: in scope, mapped to FR-002, FR-003.
- **DESIGN-REQ-004** (`docs/Tasks/TaskArchitecture.md` section 5.5): The snapshot must preserve attachment target binding. Scope: in scope, mapped to FR-001, FR-004, FR-008.
- **DESIGN-REQ-005** (`docs/Tasks/TaskArchitecture.md` section 5.5): The snapshot must preserve pinned preset bindings, include-tree summary, per-step provenance, detachment state, and final submitted order. Scope: in scope, mapped to FR-001, FR-005.
- **DESIGN-REQ-006** (`docs/Tasks/TaskArchitecture.md` section 7): Original task input snapshot must preserve objective text, objective attachments, step text, step attachments, step order and identity, runtime and publish selections, repository and branch selection, preset application metadata, dependency declarations, and other authored fields. Scope: in scope, mapped to FR-001, FR-005.
- **DESIGN-REQ-007** (`docs/Tasks/TaskArchitecture.md` section 7): Edit, exact full rerun, edited full retry, and resume depend on the snapshot for the original authored task input. Scope: in scope, mapped to FR-002, FR-003, FR-007.
- **DESIGN-REQ-008** (`docs/Tasks/TaskArchitecture.md` section 7): Edit and full retry derive their initial browser state from the snapshot, while resume reuses the snapshot without making it editable. Scope: in scope, mapped to FR-002, FR-007.
- **DESIGN-REQ-009** (`docs/Tasks/TaskArchitecture.md` section 7): Reconstruction must not depend on current live preset catalog correctness for already submitted work. Scope: in scope, mapped to FR-005.
- **DESIGN-REQ-010** (`docs/Tasks/TaskArchitecture.md` section 7): Attachment-aware executions without reconstructible snapshots are degraded and must be treated explicitly. Scope: in scope, mapped to FR-006, FR-008.
- **DESIGN-REQ-011** (`docs/Tasks/TaskArchitecture.md` section 11): Resume uses original task inputs unchanged and edits require edited full retry. Scope: in scope, mapped to FR-007.

## Requirements

### Functional Requirements

- **FR-001**: The system MUST persist an authoritative task input snapshot at submission time containing the full authored task objective, steps, step identity and order, runtime selections, publish selections, repository and branch selection, dependency declarations, attachment refs, attachment targets, preset application metadata, pinned preset bindings, include-tree summary, per-step provenance, detachment state, and final submitted order.
- **FR-002**: Edit and full retry flows MUST initialize their browser state from the authoritative task input snapshot rather than execution projections, rendered logs, current preset definitions, or partial task summaries.
- **FR-003**: Exact full rerun MUST reuse the authoritative task input snapshot as the execution input without importing completed execution progress from the source run.
- **FR-004**: Reconstruction MUST preserve objective-scoped and step-scoped attachment target bindings exactly across create, detail, edit, full retry, exact rerun, prepare, and prompt composition surfaces.
- **FR-005**: Reconstruction MUST preserve pinned preset bindings, include-tree summary, per-step provenance, detachment state, and final submitted order without requiring live preset catalog lookup for already submitted work.
- **FR-006**: If an attachment-aware execution lacks a reconstructible authoritative task input snapshot, the system MUST classify the reconstruction as explicitly degraded and MUST NOT silently drop attachments, retarget attachments, or synthesize replacement authoring state.
- **FR-007**: Resume from failed step MUST reuse the original task input snapshot unchanged and MUST NOT expose task input edits; any user change to instructions, steps, attachments, runtime, publish mode, branch, presets, or dependencies MUST require edited full retry.
- **FR-008**: User-facing task detail, edit, rerun, full retry, and resume readiness surfaces MUST expose enough snapshot and degradation evidence for operators to understand whether reconstruction is authoritative or degraded.
- **FR-009**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key `MM-629` and this canonical Jira preset brief for traceability.

### Key Entities

- **Task Input Snapshot**: The durable authoritative representation of the authored task input, including text, steps, selected runtime behavior, publish behavior, source branch, dependencies, attachments, target bindings, preset metadata, and final submitted order.
- **Attachment Target Binding**: The association between an input attachment and either the task objective target or a specific step target that must survive reconstruction and execution preparation.
- **Preset Provenance**: The durable metadata describing pinned preset bindings, include-tree summary, per-step provenance, detachment state, and compiled order for preset-derived task content.
- **Reconstruction State**: The result of loading authoring state from a snapshot for detail, edit, full retry, exact rerun, or resume readiness, including whether the result is authoritative or degraded.

## Success Criteria

- **SC-001**: A task containing objective text, multiple steps, objective-scoped attachments, step-scoped attachments, runtime settings, publish settings, branch selection, dependencies, and preset provenance can be submitted and later reconstructed with every authored field preserved.
- **SC-002**: Edit and full retry entrypoints display initial state from the authoritative snapshot for representative tasks and do not depend on lossy projections or current preset catalog data.
- **SC-003**: Exact full rerun starts from the original snapshot while resume preserves prior completed work only through resume checkpoint semantics and does not modify the original task input.
- **SC-004**: Attachment-aware executions with missing or invalid snapshots are reported as degraded with no silent attachment loss, target retargeting, or synthetic authoring state.
- **SC-005**: Tests cover submission snapshot persistence, snapshot-based edit/full retry reconstruction, preset provenance preservation, attachment target preservation, degraded reconstruction, and resume snapshot immutability.
- **SC-006**: Traceability review confirms `MM-629`, the canonical Jira preset brief, and DESIGN-REQ-001 through DESIGN-REQ-011 remain preserved across MoonSpec artifacts and final verification evidence.
