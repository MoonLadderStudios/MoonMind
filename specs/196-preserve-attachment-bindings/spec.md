# Feature Specification: Preserve Attachment Bindings in Snapshots and Reruns

**Feature Branch**: `196-preserve-attachment-bindings`  
**Created**: 2026-04-17  
**Status**: Draft  
**Input**: User description: "Use the Jira preset brief for MM-369 as the canonical Moon Spec orchestration input.

Additional constraints:

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

**Canonical Jira Brief**: `spec.md` (Input)

## Original Jira Preset Brief

Jira issue: MM-369 from MM project
Summary: Preserve attachment bindings in snapshots and reruns
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-369 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-369: Preserve attachment bindings in snapshots and reruns

Source Reference
- Source Document: `docs/Tasks/ImageSystem.md`
- Source Title: Task Image Input System
- Source Sections:
  - 5.3 Authoritative snapshot contract
  - 11. UI preview and detail contract
  - 13. Edit and rerun durability contract
- Coverage IDs:
  - DESIGN-REQ-007
  - DESIGN-REQ-015
  - DESIGN-REQ-018

User Story
As a user editing or rerunning a task, I need MoonMind to reconstruct attachments from the authoritative task input snapshot so unchanged bindings survive and changes are always explicit.

Acceptance Criteria
- The snapshot preserves text fields, target attachment refs, step identity/order, runtime, publish, repository settings, and applied preset metadata.
- Attachment target binding is reconstructed from the snapshot, not inferred from artifact links or filenames.
- Unchanged attachment refs survive edit and rerun unchanged.
- Removing an attachment and adding a new attachment are explicit user actions.
- A text-only draft reconstruction cannot silently drop attachments.
- The system fails explicitly if attachment bindings cannot be reconstructed.
- Historical artifacts may remain according to retention even after an edited draft stops referencing them.

Requirements
- Persist target attachment refs in the task input snapshot.
- Use the same attachment contract for create, edit, and rerun.
- Keep step identity and ordering stable enough to bind step-scoped attachments.
- Distinguish persisted attachment refs from new local files in edit/rerun flows.
- Preserve objective-scoped attachments in `task.inputAttachments`.
- Preserve step-scoped attachments in `task.steps[n].inputAttachments`.
- Normalize attachment refs before workflow start without changing target binding semantics.
- Reconstruct attachment target binding from the authoritative task input snapshot, not from artifact links, filenames, or UI-only heuristics.
- Preserve runtime, publish, repository settings, and applied preset metadata alongside text fields and attachment refs in the snapshot.
- Fail explicitly if edit or rerun reconstruction cannot preserve attachment bindings.

Relevant Implementation Notes
- The original task input snapshot is the source of truth for edit and rerun reconstruction.
- Task detail, edit, and rerun previews should be organized by explicit attachment target: objective-scoped or step-scoped.
- Preview and download surfaces must not infer target binding from filenames.
- Preview failure must not remove access to metadata or download actions.
- Edit and rerun surfaces must distinguish persisted attachments from new local files that have not yet been uploaded.
- Create, edit, and rerun should use the same authoritative attachment contract.
- Unchanged attachment refs should survive edit and rerun unchanged.
- Removing an existing attachment and adding a new attachment should remain explicit user actions.
- Historical source artifacts may remain according to retention policy even after an edited draft stops referencing them.

Suggested Implementation Areas
- Task input snapshot persistence for objective and step attachment refs.
- Edit draft reconstruction from persisted task snapshots.
- Rerun draft reconstruction from persisted task snapshots.
- Create-page or task-detail attachment preview grouping by target.
- Validation and error handling for missing or unreconstructable attachment bindings.
- Tests for create, edit, and rerun attachment binding preservation.

Validation
- Verify a task snapshot preserves text fields, target attachment refs, step identity/order, runtime, publish, repository settings, and applied preset metadata.
- Verify edit reconstruction uses persisted snapshot attachment refs and does not infer target binding from artifact links or filenames.
- Verify rerun reconstruction preserves unchanged objective-scoped and step-scoped attachment refs.
- Verify removing an attachment and adding a new attachment are explicit user actions.
- Verify a text-only draft reconstruction cannot silently drop existing attachments.
- Verify the system fails explicitly when attachment bindings cannot be reconstructed.
- Verify historical artifacts can remain under retention when an edited draft no longer references them.

Non-Goals
- Inferring attachment target bindings from filenames, artifact links, or attachment metadata.
- Silently dropping attachments during edit or rerun reconstruction.
- Rewriting attachment refs through hidden compatibility transforms.
- Changing artifact retention semantics beyond preserving references correctly in edit and rerun flows.
- Adding generic non-image attachment support beyond this story's image attachment binding durability contract.

Needs Clarification
- None

<!-- Moon Spec specs contain exactly one independently testable user story. Use /moonspec-breakdown for technical designs that contain multiple stories. -->

## User Story - Preserve Attachment Bindings in Snapshots and Reruns

**Summary**: As a user editing or rerunning a task, I need MoonMind to reconstruct attachments from the authoritative task input snapshot so unchanged bindings survive and changes are always explicit.

**Goal**: Users can open existing task drafts for edit or rerun and see every objective-scoped and step-scoped attachment binding preserved exactly unless they explicitly remove or replace it.

**Independent Test**: Create a task with objective-scoped and step-scoped attachments, then reconstruct it for edit and rerun; the reconstructed drafts must preserve the original attachment refs, target bindings, text, step order, runtime, publish, repository, and preset metadata, while explicit remove/add actions are reflected without silently dropping unchanged attachments.

**Acceptance Scenarios**:

1. **Given** a saved task snapshot contains text fields, runtime, publish, repository settings, applied preset metadata, step identity/order, and objective-scoped plus step-scoped attachment refs, **When** the user opens the task for edit, **Then** the draft is reconstructed from the snapshot with every unchanged attachment ref still bound to its original target.
2. **Given** a saved task snapshot contains objective-scoped and step-scoped attachment refs, **When** the user starts a rerun, **Then** the rerun draft preserves unchanged attachment refs and target bindings without using filenames or artifact-link metadata as the source of truth.
3. **Given** a user removes an existing attachment or adds a new uploaded attachment during edit or rerun, **When** the draft is saved or submitted, **Then** the change is recorded as an explicit user action while all other unchanged attachment refs remain intact.
4. **Given** a reconstruction path cannot recover attachment target bindings from the authoritative snapshot, **When** the edit or rerun draft is requested, **Then** the system fails explicitly instead of presenting a text-only draft that silently drops attachments.
5. **Given** historical source artifacts remain available under retention after an edited draft no longer references them, **When** the edited task is inspected, **Then** the current draft references only selected attachments while historical artifacts remain governed by retention and are not treated as active bindings.

### Edge Cases

- A task has both objective-scoped and multiple step-scoped attachments with similar filenames.
- A step is reordered or edited while its unchanged attachment refs should remain tied to the same logical step identity.
- A browser or detail view can show attachment metadata, but preview generation fails.
- An edit or rerun flow contains a mix of persisted attachment refs and new local files that are not uploaded yet.
- A snapshot is missing the fields needed to reconstruct attachment target bindings.
- Artifact link metadata is stale, incomplete, or inconsistent with the task snapshot.

## Assumptions

- The story is runtime implementation work, not documentation-only work.
- `docs/Tasks/ImageSystem.md` is treated as source requirements for runtime behavior.
- The canonical attachment contract remains `task.inputAttachments` for objective-scoped attachments and `task.steps[n].inputAttachments` for step-scoped attachments.
- Existing artifact retention behavior is preserved; this story only governs whether current edit/rerun drafts retain active attachment bindings.

## Source Design Requirements

- **DESIGN-REQ-007** (Source: `docs/Tasks/ImageSystem.md`, section 5.3; MM-369 brief): The task input snapshot MUST preserve text fields, target attachment refs, step identity/order, runtime, publish, repository settings, and applied preset metadata, and MUST be the source of truth for edit and rerun reconstruction. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004.
- **DESIGN-REQ-015** (Source: `docs/Tasks/ImageSystem.md`, section 11; MM-369 brief): Task detail, edit, and rerun surfaces MUST organize previews by explicit target, MUST NOT infer target binding from filenames, MUST keep metadata/download actions available when preview fails, and MUST distinguish persisted refs from new local files. Scope: in scope. Maps to FR-005, FR-006, FR-007.
- **DESIGN-REQ-018** (Source: `docs/Tasks/ImageSystem.md`, section 13; MM-369 brief): Create, edit, and rerun MUST use the same authoritative attachment contract; unchanged refs MUST survive unchanged; adding and removing attachments MUST be explicit; and text-only reconstruction MUST NOT silently discard attachments. Scope: in scope. Maps to FR-001, FR-003, FR-004, FR-006, FR-008, FR-009.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST persist objective-scoped and step-scoped attachment refs in the authoritative task input snapshot using the existing attachment contract.
- **FR-002**: System MUST preserve text fields, step identity/order, runtime, publish, repository settings, and applied preset metadata alongside attachment refs in the task snapshot.
- **FR-003**: Edit draft reconstruction MUST use the authoritative task input snapshot as the source of truth for attachment target binding.
- **FR-004**: Rerun draft reconstruction MUST use the authoritative task input snapshot as the source of truth for attachment target binding.
- **FR-005**: Task detail, edit, and rerun surfaces MUST present attachment previews grouped by objective-scoped and step-scoped targets without inferring target binding from filenames.
- **FR-006**: System MUST distinguish persisted attachment refs from new local files in edit and rerun flows.
- **FR-007**: Preview failures MUST NOT remove access to attachment metadata or available download actions.
- **FR-008**: Removing an attachment and adding a new attachment MUST be represented as explicit user actions while unchanged refs survive unchanged.
- **FR-009**: System MUST fail explicitly when attachment target bindings cannot be reconstructed instead of silently presenting a text-only draft or dropping attachments.
- **FR-010**: System MUST NOT treat artifact links, filenames, or attachment metadata as the authoritative source for current target binding.
- **FR-011**: Historical source artifacts MAY remain under retention after an edited draft stops referencing them, but they MUST NOT be treated as active bindings unless the current snapshot references them.

### Key Entities

- **Task Input Snapshot**: The authoritative saved task input state, including text, runtime, publish, repository, preset metadata, step identity/order, and objective/step attachment refs.
- **Attachment Ref**: A persisted reference to an uploaded input attachment, scoped to either the task objective or a specific step target.
- **Draft Reconstruction**: The edit or rerun process that turns a saved task input snapshot back into a user-editable draft.
- **Attachment Target Binding**: The relationship between an attachment ref and its objective-scoped or step-scoped destination.
- **Persisted Attachment Ref vs New Local File**: A distinction between an already uploaded attachment reference and a not-yet-uploaded local browser file selected during edit or rerun.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Automated coverage verifies that edit reconstruction preserves all unchanged objective-scoped and step-scoped attachment refs from the saved snapshot.
- **SC-002**: Automated coverage verifies that rerun reconstruction preserves all unchanged objective-scoped and step-scoped attachment refs from the saved snapshot.
- **SC-003**: Automated coverage verifies at least one remove action and one add action without losing unrelated unchanged attachment refs.
- **SC-004**: Automated coverage verifies that reconstruction fails explicitly when a snapshot lacks required attachment binding data.
- **SC-005**: Automated coverage verifies that attachment target binding is not inferred from filenames, artifact links, or metadata.
