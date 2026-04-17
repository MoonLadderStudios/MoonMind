# Feature Specification: Edit and Rerun Attachment Reconstruction

**Feature ID**: `202-edit-rerun-attachment-reconstruction`  
**Managed PR Branch**: `mm-382-8aa2c304`  
**Created**: 2026-04-17  
**Status**: Draft  
**Input**: User description: "Use the Jira preset brief for MM-382 as the canonical Moon Spec orchestration input.

Additional constraints:


Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

**Canonical Jira Brief**: `docs/tmp/jira-orchestration-inputs/MM-382-moonspec-orchestration-input.md`

## Original Jira Preset Brief

Jira issue: MM-382 from MM project
Summary: Edit and Rerun Attachment Reconstruction
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-382 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-382: Edit and Rerun Attachment Reconstruction

Source Reference
- Source Document: docs/UI/CreatePage.md
- Source Title: Create Page
- Source Sections:
  - 13. Edit and rerun contract
  - 14. Submission contract
  - 16. Failure and empty-state rules
  - 18. Testing requirements
- Coverage IDs:
  - DESIGN-REQ-019
  - DESIGN-REQ-020
  - DESIGN-REQ-021
  - DESIGN-REQ-005
  - DESIGN-REQ-006
  - DESIGN-REQ-023
  - DESIGN-REQ-025

User Story
As a task author, I can edit or rerun an existing MoonMind.Run and get a reconstructed draft that preserves objective text, attachments, templates, dependencies, runtime options, and untouched attachment refs unless I change them.

Acceptance Criteria
- Given I edit an existing MoonMind.Run, then the draft is reconstructed from the authoritative task input snapshot.
- Given I rerun an existing MoonMind.Run, then objective text, objective attachments, step instructions, step attachments, runtime and publish settings, applied templates and dirty state, and dependencies are reconstructed when they remain editable.
- Given persisted attachments exist, then they render distinctly from newly selected local files.
- Given I do not touch persisted attachments during rerun, then their refs survive the round trip without being dropped or duplicated.
- Given I remove, add, or replace an attachment, then only the authored target changes and unrelated draft state remains intact.
- Given one or more attachment bindings cannot be reconstructed, then edit or rerun fails explicitly rather than silently dropping attachments.

Requirements
- Use the authoritative task input snapshot as the source for edit and rerun draft reconstruction.
- Reconstruct objective text, objective-scoped attachments, step instructions, step-scoped attachments, runtime settings, publish settings, templates, dirty state, and dependencies.
- Differentiate existing persisted attachments from new local files in state and UI.
- Support keep, remove, add, and replace flows for persisted attachments.
- Preserve untouched attachment refs by default during rerun.
- Fail explicitly if attachment targeting or bindings cannot be reconstructed.
- Cover edit reconstruction, rerun preservation, and no silent drop/duplicate behavior in tests.

Relevant Implementation Notes
- Treat `docs/UI/CreatePage.md` as the source design for edit and rerun reconstruction, submission, failure, empty-state, and testing behavior.
- Preserve the Jira issue key MM-382 anywhere downstream artifacts summarize or verify the work.
- Reconstruct drafts from the authoritative task input snapshot rather than from lossy projected execution state.
- Preserve objective text, objective-scoped attachments, step instructions, step-scoped attachments, runtime settings, publish settings, template state, dirty state, and dependencies when those fields remain editable.
- Keep persisted attachment refs distinct from newly selected local files in draft state and UI.
- Preserve untouched attachment refs during rerun and only change the specific authored target when a user removes, adds, or replaces an attachment.
- Fail edit or rerun reconstruction explicitly when attachment targeting or bindings cannot be reconstructed; do not silently drop or duplicate attachments.

Verification
- Confirm edit reconstruction uses the authoritative task input snapshot.
- Confirm rerun reconstruction preserves objective text, objective attachments, step instructions, step attachments, runtime settings, publish settings, applied templates, dirty state, and dependencies when editable.
- Confirm persisted attachments render distinctly from newly selected local files.
- Confirm untouched persisted attachment refs survive the rerun round trip without being dropped or duplicated.
- Confirm remove, add, and replace actions only affect the authored target and leave unrelated draft state intact.
- Confirm reconstruction fails explicitly when one or more attachment bindings cannot be reconstructed.
- Confirm tests cover edit reconstruction, rerun preservation, and no silent drop/duplicate behavior.

Out of Scope
- Reconstructing edit or rerun drafts from lossy projected execution state instead of the authoritative task input snapshot.
- Silently dropping persisted attachments when bindings cannot be reconstructed.
- Duplicating untouched attachment refs during rerun.

<!-- Moon Spec specs contain exactly one independently testable user story. Use /speckit.breakdown for technical designs that contain multiple stories. -->

## Classification

Single-story runtime feature request. The brief contains one actor, one goal, and one independently testable behavior: reconstruct edit and rerun drafts for a MoonMind.Run from the authoritative task input snapshot without losing objective or step attachment bindings.

## User Story - Edit and Rerun Attachment Reconstruction

**Summary**: As a task author, I can edit or rerun an existing MoonMind.Run and get a reconstructed draft that preserves objective text, attachments, templates, dependencies, runtime options, and untouched attachment refs unless I change them.

**Goal**: Task authors can safely edit or rerun existing runs without losing persisted objective-scoped or step-scoped attachment refs, while explicit attachment changes affect only the intended target.

**Independent Test**: Create an execution snapshot with objective attachments, step attachments, runtime settings, publish settings, template state, and dependencies; reconstruct it for edit and rerun; verify unchanged attachment refs and editable fields survive, explicit add/remove changes stay target-scoped, and incomplete binding data fails explicitly.

**Acceptance Scenarios**:

1. **Given** an existing MoonMind.Run has an authoritative task input snapshot, **When** a task author opens it for edit, **Then** the draft is reconstructed from that snapshot and preserves objective text, objective attachments, step instructions, step attachments, runtime settings, publish settings, templates, dirty state, and editable dependencies.
2. **Given** an existing MoonMind.Run has objective-scoped and step-scoped persisted attachment refs, **When** a task author starts a rerun and does not modify those attachments, **Then** the rerun draft and submitted payload preserve those refs without dropping or duplicating them.
3. **Given** a reconstructed edit or rerun draft contains persisted attachments and newly selected local files, **When** the draft is displayed, **Then** persisted refs are distinguishable from local files and attachment totals account for both.
4. **Given** a task author removes, adds, or replaces one attachment on a reconstructed draft, **When** the draft is submitted, **Then** only that authored target changes and unrelated objective or step attachment refs remain unchanged.
5. **Given** a reconstruction path cannot recover attachment target bindings from the authoritative snapshot, **When** edit or rerun reconstruction is requested, **Then** the system fails explicitly instead of presenting a text-only draft or silently dropping attachments.

### Edge Cases

- The objective and multiple steps contain attachments with similar filenames.
- A reconstructed draft mixes persisted attachment refs with new local files that have not been uploaded yet.
- A step has no attachments while another step has multiple persisted refs.
- Attachment preview generation fails but metadata and target binding are still available.
- The snapshot has compact attachment references but no structured target binding in `task.inputAttachments` or `task.steps[n].inputAttachments`.
- Only flat reconstruction is available for preset state; the system must not claim preset-bound state remains recoverable.

## Assumptions

- MM-382 is runtime implementation work, not documentation-only work.
- `docs/UI/CreatePage.md` is treated as a runtime source requirements document for Create page edit, rerun, and submission behavior.
- The existing structured attachment contract remains `task.inputAttachments` for objective-scoped attachments and `task.steps[n].inputAttachments` for step-scoped attachments.
- Historical artifact retention is unchanged; this story governs current reconstructed drafts and submissions.

## Source Design Requirements

- **DESIGN-REQ-019** (Source: `docs/UI/CreatePage.md`, section 13; MM-382 brief): Edit and rerun MUST reconstruct drafts from the authoritative task input snapshot. Scope: in scope. Maps to FR-001, FR-002, FR-003.
- **DESIGN-REQ-020** (Source: `docs/UI/CreatePage.md`, section 13; MM-382 brief): Reconstructed drafts MUST include objective text, objective attachments, step instructions, step attachments, runtime and publish settings, applied template or preset state, per-step source state when recoverable, and editable dependencies. Scope: in scope. Maps to FR-002, FR-004, FR-005.
- **DESIGN-REQ-021** (Source: `docs/UI/CreatePage.md`, sections 13-14; MM-382 brief): Existing persisted attachments MUST remain distinct from newly selected local files and local images MUST upload before create, edit, or rerun submission. Scope: in scope. Maps to FR-006, FR-007.
- **DESIGN-REQ-005** (Source: `docs/UI/CreatePage.md`, section 14; MM-382 brief): Submitted payloads MUST use structured attachment refs and preserve target semantics through `task.inputAttachments` and `task.steps[n].inputAttachments`. Scope: in scope. Maps to FR-008, FR-009.
- **DESIGN-REQ-006** (Source: `docs/UI/CreatePage.md`, section 14; MM-382 brief): Attachment meaning MUST be defined by explicit target, not filename conventions or artifact metadata. Scope: in scope. Maps to FR-010.
- **DESIGN-REQ-023** (Source: `docs/UI/CreatePage.md`, section 16; MM-382 brief): Failure and degraded states MUST be explicit and must not corrupt or silently drop draft state. Scope: in scope. Maps to FR-011.
- **DESIGN-REQ-025** (Source: `docs/UI/CreatePage.md`, section 18; MM-382 brief): Tests MUST cover edit reconstruction, rerun preservation, attachment round-trips, explicit add/remove behavior, and failure handling. Scope: in scope. Maps to FR-012.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST use the authoritative task input snapshot as the source for edit draft reconstruction.
- **FR-002**: System MUST use the authoritative task input snapshot as the source for rerun draft reconstruction.
- **FR-003**: System MUST fail explicitly when the authoritative snapshot does not contain enough structured data to reconstruct attachment target bindings.
- **FR-004**: Reconstructed drafts MUST preserve objective text, step instructions, runtime settings, publish settings, template or preset state, dirty state, and editable dependencies when those values remain recoverable.
- **FR-005**: Reconstructed drafts MUST preserve objective-scoped and step-scoped attachment refs using their original target bindings.
- **FR-006**: Edit and rerun UI state MUST distinguish existing persisted attachment refs from newly selected local files.
- **FR-007**: Create, edit, and rerun submissions MUST upload new local images before sending the execution payload.
- **FR-008**: Submitted edit and rerun payloads MUST preserve unchanged objective attachment refs under `task.inputAttachments`.
- **FR-009**: Submitted edit and rerun payloads MUST preserve unchanged step attachment refs under `task.steps[n].inputAttachments`.
- **FR-010**: System MUST NOT infer current attachment target binding from filenames, artifact links, or attachment metadata.
- **FR-011**: Add, remove, and replace actions MUST affect only the authored objective or step target and leave unrelated draft state unchanged.
- **FR-012**: Automated coverage MUST verify edit reconstruction, rerun preservation, explicit add/remove behavior, and no silent drop or duplicate behavior for attachments.

### Key Entities

- **Authoritative Task Input Snapshot**: The saved task input state used to reconstruct edit and rerun drafts.
- **Reconstructed Draft**: The editable Create page state produced from an existing MoonMind.Run.
- **Attachment Ref**: A structured persisted input reference attached to either the objective or a step target.
- **Attachment Target Binding**: The relationship between an attachment ref and its objective-scoped or step-scoped destination.
- **Persisted Attachment vs Local File**: The distinction between an already uploaded attachment ref and a newly selected local browser file awaiting upload.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Tests verify edit reconstruction preserves unchanged objective-scoped and step-scoped attachment refs from the authoritative snapshot.
- **SC-002**: Tests verify rerun or edit submission preserves untouched persisted attachment refs without dropping or duplicating them.
- **SC-003**: Tests verify persisted attachment refs are represented distinctly from new local files in reconstructed state.
- **SC-004**: Tests verify removing or adding an attachment changes only the authored target.
- **SC-005**: Tests verify reconstruction fails explicitly when structured attachment target bindings cannot be recovered from the snapshot.
