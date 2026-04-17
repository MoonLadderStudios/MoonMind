# Feature Specification: Jira Import Into Declared Targets

**Feature Branch**: `mm-381-a453f798`  
**Created**: 2026-04-17  
**Status**: Draft  
**Input**: User description: "Use the Jira preset brief for MM-381 as the canonical Moon Spec orchestration input.

Additional constraints:


Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

**Canonical Jira Brief**: `docs/tmp/jira-orchestration-inputs/MM-381-moonspec-orchestration-input.md`

## Original Jira Preset Brief

Jira issue: MM-381 from MM project
Summary: Jira Import Into Declared Targets
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-381 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-381: Jira Import Into Declared Targets

Source Reference
Source Document: docs/UI/CreatePage.md
Source Title: Create Page
Source Sections:
- 12. Jira integration contract
- 16. Failure and empty-state rules
- 17. Accessibility and interaction rules
- 18. Testing requirements
Coverage IDs:
- DESIGN-REQ-017
- DESIGN-REQ-018
- DESIGN-REQ-003
- DESIGN-REQ-010
- DESIGN-REQ-012
- DESIGN-REQ-015
- DESIGN-REQ-022
- DESIGN-REQ-023
- DESIGN-REQ-024
- DESIGN-REQ-025
As a task author, I can browse Jira as an external instruction source and explicitly import issue text or supported images into a declared Create page target without automatic draft mutation.

Acceptance criteria:
- Given I open Jira from a Create page field, then the browser preselects that matching target and displays the current target explicitly.
- Given I select a Jira issue, then the draft does not mutate until I confirm a text or image import action.
- Given I switch import targets inside the Jira browser, then the selected issue remains selected.
- Given I import text, then I can choose Replace target text or Append to target text for preset objective text or a step instruction target.
- Given I import supported Jira images, then selected images become structured attachments on the selected objective or step attachment target and are not injected as markdown, HTML, or inline data.
- Given Jira is unavailable or the issue fetch fails, then the draft is not mutated and I can continue manual authoring.
- Given import succeeds, then focus returns predictably to the updated field or an explicit success notice.

Requirements:
- Support Jira import targets for preset objective text, preset objective attachments, step text, and step attachments.
- Require explicit confirmation for all Jira text and image imports.
- Preserve selected issue state while switching targets inside the browser.
- Import Jira images only as structured attachments on the declared target.
- Mark already-applied preset state as needing reapply when importing into preset objective text or attachment targets.
- Detach template-bound steps when Jira text or images import into them.
- Keep Jira access behind MoonMind APIs and separate from task execution substrate behavior.
- Cover explicit import, no-mutation-before-confirm, image target mapping, template detachment, focus return, and failure behavior in tests.

<!-- Moon Spec specs contain exactly one independently testable user story. Use /speckit.breakdown for technical designs that contain multiple stories. -->

## Classification

- Input class: single-story feature request.
- Mode: runtime.
- Source design treatment: `docs/UI/CreatePage.md` is an implementation requirements source, not a docs-only target.
- Resume decision: no existing `MM-381` spec artifacts were present, so the workflow starts at Specify.

## User Story - Jira Import Into Declared Targets

**Summary**: As a task author, I can browse Jira as an external instruction source and explicitly import issue text or supported images into the declared Create page target.

**Goal**: Task authors can source text and supported images from Jira without hidden draft mutation, target ambiguity, direct Jira browser access, or attachment target loss.

**Independent Test**: Can be tested by opening the Create page with Jira enabled, opening the browser from preset text, objective attachment, step text, and step attachment entry points, switching targets inside the browser, importing text or images, and verifying that only the declared target changes while manual authoring still works after Jira failures.

**Acceptance Scenarios**:

1. **Given** Jira is opened from a Create page field, **When** the browser opens, **Then** the matching target is preselected and the current target is displayed explicitly.
2. **Given** a Jira issue is available, **When** the task author selects a different import target inside the browser, **Then** the selected issue remains selected.
3. **Given** a Jira issue has text content, **When** the task author imports text, **Then** they can replace the target text or append to it for preset objective text or a step instruction target.
4. **Given** a Jira issue has supported images and attachment policy permits them, **When** the task author imports images, **Then** the images become structured attachments on the selected objective or step attachment target and are not injected into markdown, HTML, inline data, or another target.
5. **Given** Jira is unavailable or issue detail cannot be fetched, **When** the task author closes or leaves the Jira browser, **Then** the draft is not mutated and manual authoring remains available.
6. **Given** a template-bound step receives Jira text or images, **When** the import succeeds, **Then** the step is treated as manually customized and no other preset-derived steps are silently rewritten.
7. **Given** an already-applied preset receives Jira text or objective-scoped images, **When** the import changes the preset objective target, **Then** the preset is marked as needing explicit reapply and already-expanded steps remain unchanged.

### Edge Cases

- Jira endpoints are disabled, incomplete, unreachable, or return structured errors.
- The selected Jira issue has no recommended preset brief, step instructions, description, or acceptance criteria.
- The selected Jira issue has no supported image attachments.
- The browser target is switched after an issue has been selected.
- Importing into a target would exceed attachment policy limits.
- The selected step is template-bound by instructions, by attachments, or already manually customized.
- The Jira project key contains a hyphen.

## Assumptions

- Existing MoonMind-owned Jira browser endpoints remain the only browser path to Jira data.
- Existing artifact upload behavior remains responsible for converting imported images into structured attachment refs before task submission.
- Selecting a Jira issue in the current browser flow is the import confirmation action; the UI still exposes declared target and text write-mode controls before selection.

## Source Design Requirements

- **DESIGN-REQ-017** (Source: `docs/UI/CreatePage.md`, section 12.2; MM-381 brief): The Jira browser MUST support preset objective text, preset objective attachments, step instruction, and step attachment targets; opening from a field MUST preselect the matching target; the browser MUST display the current target; and switching targets MUST NOT clear the selected issue. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004, FR-005.
- **DESIGN-REQ-018** (Source: `docs/UI/CreatePage.md`, section 12.3; MM-381 brief): Selecting Jira issue content MUST avoid hidden draft mutation; text import MUST be explicit and support replace or append; image import MUST be explicit, use supported images only, create structured attachments on the selected target, avoid markdown/HTML/inline data injection, and count imports into preset-bound steps as manual customization. Scope: in scope. Maps to FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, FR-012.
- **DESIGN-REQ-003** (Source: `docs/UI/CreatePage.md`, section 12.1; MM-381 brief): Jira exists to source task inputs into the Create page and MUST NOT create tasks automatically, replace the step editor, replace presets, change submission API shape to Jira-native workflow types, or make the browser talk directly to Jira. Scope: in scope. Maps to FR-013, FR-014.
- **DESIGN-REQ-010** (Source: `docs/UI/CreatePage.md`, section 12.4; MM-381 brief): Importing Jira text or images into the preset objective target MUST mark an already-applied preset as needing reapply, while importing into a step target MUST NOT mutate preset objective state. Scope: in scope. Maps to FR-015, FR-016, FR-017.
- **DESIGN-REQ-012** (Source: `docs/UI/CreatePage.md`, sections 14 and 15; MM-381 brief): The meaning of imported attachments MUST be defined by their objective or step target, not filename conventions, and non-primary step attachments MUST NOT become task-level objective inputs unless a runtime explicitly promotes them. Scope: in scope. Maps to FR-009, FR-018, FR-019.
- **DESIGN-REQ-015** (Source: `docs/UI/CreatePage.md`, section 15; MM-381 brief): Imported Jira text MUST follow objective resolution rules: preset objective text overrides primary-step text, primary-step import affects resolved objective only when preset objective text is empty, and non-primary step text does not change resolved objective text. Scope: in scope. Maps to FR-020.
- **DESIGN-REQ-022** (Source: `docs/UI/CreatePage.md`, section 16; MM-381 brief): Jira unavailability or issue fetch failure MUST remain local to the browser, avoid draft mutation, and allow manual task authoring. Scope: in scope. Maps to FR-021, FR-022.
- **DESIGN-REQ-023** (Source: `docs/UI/CreatePage.md`, section 17; MM-381 brief): Jira open, close, target, and import actions MUST be keyboard accessible; the browser title or context MUST identify the current import target; focus MUST return predictably after import; and validation errors MUST stay associated with the failed target. Scope: in scope. Maps to FR-023, FR-024, FR-025.
- **DESIGN-REQ-024** (Source: `docs/UI/CreatePage.md`, section 18; MM-381 brief): Tests SHOULD cover no hidden Jira mutation before import confirmation, structured image target mapping, template detachment, focus/failure behavior, and unrelated-draft preservation. Scope: in scope. Maps to FR-026.
- **DESIGN-REQ-025** (Source: `docs/UI/CreatePage.md`, section 16; MM-381 brief): Drafts MUST NOT proceed or appear successful after silently discarded Jira or attachment data. Scope: in scope. Maps to FR-022, FR-027.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST expose a Jira import target model that distinguishes preset objective text, preset objective attachments, step text, and step attachments.
- **FR-002**: Opening Jira from preset objective text MUST preselect and display the preset objective text target.
- **FR-003**: Opening Jira from objective attachments MUST preselect and display the objective attachment target.
- **FR-004**: Opening Jira from a step instruction or step attachment control MUST preselect and display that exact step target.
- **FR-005**: Switching the Jira import target inside the browser MUST preserve the currently selected issue.
- **FR-006**: Jira text import MUST support append-to-target and replace-target semantics for text targets.
- **FR-007**: Jira image import MUST be available for attachment targets when attachment policy permits supported images.
- **FR-008**: Jira image import MUST add images only as structured attachment candidates on the selected target.
- **FR-009**: Jira images MUST NOT be injected into instruction text as markdown, HTML, inline data, or filename-derived references.
- **FR-010**: Jira import into a template-bound step's text target MUST detach that step from template-bound instruction identity.
- **FR-011**: Jira image import into a template-bound step's attachment target MUST detach that step from template-bound attachment identity.
- **FR-012**: Jira import into one step target MUST NOT change other steps.
- **FR-013**: Jira browsing MUST use MoonMind-owned API endpoints and MUST NOT call Atlassian directly from browser code.
- **FR-014**: Jira import MUST NOT create a MoonMind task, change the task submission API shape, or bypass existing Create page validation by itself.
- **FR-015**: Jira import into preset objective text after preset application MUST mark preset state as needing explicit reapply.
- **FR-016**: Jira image import into preset objective attachments after preset application MUST mark preset state as needing explicit reapply.
- **FR-017**: Jira import into a step target MUST NOT mark preset objective text as changed.
- **FR-018**: Objective-scoped Jira images MUST submit only through `task.inputAttachments` after normal artifact upload.
- **FR-019**: Step-scoped Jira images MUST submit only through the owning `task.steps[n].inputAttachments` after normal artifact upload.
- **FR-020**: Jira text imports MUST preserve existing objective-resolution behavior for preset objective text, primary step text, and non-primary step text.
- **FR-021**: Jira provider, project, board, issue-list, and issue-detail failures MUST be visible in the browser and scoped to Jira.
- **FR-022**: Jira failures MUST leave existing draft text, steps, attachments, runtime, repository, and publish settings unchanged.
- **FR-023**: Jira browser open, close, target selection, issue selection, and import controls MUST be keyboard accessible.
- **FR-024**: The Jira browser MUST identify the current import target while it is open.
- **FR-025**: After a successful Jira import, focus or success context MUST return predictably to the updated field or visible result.
- **FR-026**: Automated coverage MUST prove target preselection, target switching with selected issue preservation, append/replace text import, structured image target mapping, template detachment, Jira failure isolation, and manual authoring continuity.
- **FR-027**: System MUST preserve Jira issue key MM-381 in MoonSpec artifacts and verification evidence for traceability.

### Key Entities

- **Jira Import Target**: The declared destination for a Jira import, including preset objective text, preset objective attachments, one step's instructions, or one step's attachments.
- **Jira Issue Detail**: Normalized issue text, recommended import strings, status metadata, and supported image attachments returned through MoonMind APIs.
- **Jira Import Provenance**: UI-only metadata that remembers which Jira issue last populated a declared text target.
- **Draft Attachment**: A local or persisted attachment associated with one explicit objective or step target.
- **Applied Preset State**: The Create page record of applied preset objective text, objective attachments, and template-derived step identity.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Automated coverage verifies each Jira entry point preselects the intended target and the browser displays it.
- **SC-002**: Automated coverage verifies switching targets inside the browser preserves the selected Jira issue.
- **SC-003**: Automated coverage verifies Jira text can append to or replace preset objective text and step text.
- **SC-004**: Automated coverage verifies imported Jira images become structured attachments on the selected objective or step target and do not alter instruction text.
- **SC-005**: Automated coverage verifies Jira import into template-bound step text or attachments detaches only the targeted step.
- **SC-006**: Automated coverage verifies Jira failures leave manual authoring and task submission payload shape intact.
- **SC-007**: Source design coverage for MM-381 and in-scope DESIGN-REQ-017 through DESIGN-REQ-025 is mapped to passing verification evidence.
