# Feature Specification: Create Page Authoring Validation

**Feature Branch**: `340-create-page-authoring-validation`
**Created**: 2026-05-12
**Status**: Draft
**Input**: User description: """
Use the Jira preset brief for MM-641 as the canonical Moon Spec orchestration input.

Additional constraints:

Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-641 MoonSpec Orchestration Input

## Source

- Jira issue: MM-641
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Create page authoring & validation with Steps-card branch/publish placement
- Priority: Medium
- Trusted fetch tool: `jira.get_issue`
- Trusted response artifact: `/work/agent_jobs/mm:8b6e11c5-c4b6-4f5e-80a5-c5c38990639b/artifacts/moonspec-inputs/MM-641-trusted-jira-get-issue.json`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira issue fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, `presetInstructions`, or `recommendedPresetInstructions`; potentially related custom fields were absent, empty, or non-brief metadata.
- Labels: moonmind-workflow-mm-a1fb7aa8-954b-4c59-acc2-c0a2c5339282

## Canonical MoonSpec Feature Request

Jira issue: MM-641 from MM project
Summary: Create page authoring & validation with Steps-card branch/publish placement
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-641 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-641: Create page authoring & validation with Steps-card branch/publish placement

Source Reference
Source Document: docs/Tasks/TaskArchitecture.md
Source Title: Task Architecture (Control Plane)
Source Sections:
- 3.1 Task-first control plane
- 5.1 Authoring and validation
Coverage IDs:
- DESIGN-REQ-001
- DESIGN-REQ-007
As a Mission Control user, I want the Create page to gather and validate my draft (text fields, preset state, Jira imports, attachments, runtime/publish, repository, single authored branch, dependencies) and to render Repository, Branch, and Publish Mode together inside the Steps card so that I author tasks in task terms while my submission remains a coherent, validated task-shaped payload.
Acceptance Criteria
- Repository, Branch, and Publish Mode controls are visually rendered together inside the Steps card.
- Publish Mode remains submission data; only its visual placement changes.
- Validation rejects invalid repository, runtime, publish-mode/branch combinations, and attachment-policy violations before submission.
- Valid drafts produce a canonical TaskPayload with task.git.branch (no targetBranch).
- User authoring intent (text, presets, Jira imports, attachments, dependencies) round-trips into the normalized payload.
Requirements
Implement Create page layout/validation; route validated drafts through the canonical contract from STORY-001.

## Orchestration Notes

- Jira Orchestrate always runs as a runtime implementation workflow.
- Treat source design references in this brief as runtime source requirements.
- Classify this as a single-story feature request unless later MoonSpec analysis finds the issue description spans multiple independently testable stories.
- Inspect existing MoonSpec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.
"""

Preserved source Jira preset brief: `MM-641` from the trusted Jira preset brief handoff, reproduced verbatim in `## Original Preset Brief` below for downstream verification.

Original brief reference: trusted `jira.get_issue` MCP response synthesized into `/work/agent_jobs/mm:8b6e11c5-c4b6-4f5e-80a5-c5c38990639b/artifacts/moonspec-inputs/MM-641-canonical-moonspec-input.md`.
Classification: single-story runtime feature request.
Resume decision: no existing Moon Spec feature directory or later-stage artifacts matched `MM-641` under `specs/`, so `Specify` was the first incomplete stage.
Runtime intent: Jira Orchestrate always runs as a runtime implementation workflow. Source design references in the brief are treated as runtime source requirements.

## Original Preset Brief

````text
# MM-641 MoonSpec Orchestration Input

## Source

- Jira issue: MM-641
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Create page authoring & validation with Steps-card branch/publish placement
- Priority: Medium
- Trusted fetch tool: `jira.get_issue`
- Trusted response artifact: `/work/agent_jobs/mm:8b6e11c5-c4b6-4f5e-80a5-c5c38990639b/artifacts/moonspec-inputs/MM-641-trusted-jira-get-issue.json`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira issue fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, `presetInstructions`, or `recommendedPresetInstructions`; potentially related custom fields were absent, empty, or non-brief metadata.
- Labels: moonmind-workflow-mm-a1fb7aa8-954b-4c59-acc2-c0a2c5339282

## Canonical MoonSpec Feature Request

Jira issue: MM-641 from MM project
Summary: Create page authoring & validation with Steps-card branch/publish placement
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-641 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-641: Create page authoring & validation with Steps-card branch/publish placement

Source Reference
Source Document: docs/Tasks/TaskArchitecture.md
Source Title: Task Architecture (Control Plane)
Source Sections:
- 3.1 Task-first control plane
- 5.1 Authoring and validation
Coverage IDs:
- DESIGN-REQ-001
- DESIGN-REQ-007
As a Mission Control user, I want the Create page to gather and validate my draft (text fields, preset state, Jira imports, attachments, runtime/publish, repository, single authored branch, dependencies) and to render Repository, Branch, and Publish Mode together inside the Steps card so that I author tasks in task terms while my submission remains a coherent, validated task-shaped payload.
Acceptance Criteria
- Repository, Branch, and Publish Mode controls are visually rendered together inside the Steps card.
- Publish Mode remains submission data; only its visual placement changes.
- Validation rejects invalid repository, runtime, publish-mode/branch combinations, and attachment-policy violations before submission.
- Valid drafts produce a canonical TaskPayload with task.git.branch (no targetBranch).
- User authoring intent (text, presets, Jira imports, attachments, dependencies) round-trips into the normalized payload.
Requirements
Implement Create page layout/validation; route validated drafts through the canonical contract from STORY-001.

## Orchestration Notes

- Jira Orchestrate always runs as a runtime implementation workflow.
- Treat source design references in this brief as runtime source requirements.
- Classify this as a single-story feature request unless later MoonSpec analysis finds the issue description spans multiple independently testable stories.
- Inspect existing MoonSpec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.
````

## User Story - Create Page Authoring and Validation

**Summary**: As a Mission Control user, I want the Create page to collect and validate my full task draft while rendering Repository, Branch, and Publish Mode together inside the Steps card so that I can author tasks in task terms and submit a coherent task-shaped payload.

**Goal**: Users can prepare a task draft with objective text, preset state, Jira imports, attachments, runtime settings, repository selection, branch intent, publish mode, and dependencies, then receive clear validation before the draft is submitted.

**Independent Test**: This story can be tested independently by authoring Create page drafts that vary repository, branch, publish mode, runtime, dependencies, preset/Jira-import state, and attachments, then confirming invalid combinations are rejected and valid drafts produce one normalized task-shaped submission preserving the user's intent.

**Acceptance Scenarios**:

1. **Given** a user is authoring a task on the Create page, **When** they configure repository, branch, and publish mode, **Then** those controls are visually grouped inside the Steps card while Publish Mode remains part of the submitted task data.
2. **Given** a draft has an invalid repository, runtime, publish-mode and branch combination, dependency state, or attachment policy state, **When** the user attempts to submit it, **Then** submission is blocked with validation feedback that identifies the invalid authoring input.
3. **Given** a draft contains valid text, preset state, Jira-imported content, attachments, dependencies, repository, branch, runtime, and publish mode, **When** the user submits it, **Then** the submitted payload is task-shaped and preserves the authored branch as `task.git.branch` without emitting `targetBranch`.
4. **Given** a valid draft includes preset-derived or Jira-imported authoring state, **When** the draft is normalized for submission, **Then** that authoring intent remains traceable in the task payload rather than being flattened into unrelated workflow-only fields.
5. **Given** a valid draft includes input attachments, **When** the draft is submitted, **Then** attachment references remain bound to their intended task or step targets and participate in validation before submission.

### Edge Cases

- Repository is missing, unavailable, or incompatible with the selected branch/publish mode.
- Publish Mode is changed after a branch has been selected.
- The draft includes a branch value but no repository selection.
- Attachment policy validation fails for a task-level or step-level attachment.
- Jira-imported or preset-derived authoring state is present alongside manual edits.
- Dependencies are present but incomplete or invalid for submission.

## Assumptions

- Existing Create page concepts for task objective text, steps, presets, Jira imports, attachments, dependencies, runtime, repository, branch, and publish mode remain available to users.
- Existing attachment policy rules define which attachment combinations are valid; this story requires Create page validation to enforce them before submission.
- Existing task-shaped submission terminology is product-visible enough to describe expected outcomes without specifying internal implementation details.

## Source Design Requirements

- **DESIGN-REQ-001**: Source `docs/Tasks/TaskArchitecture.md`, section 3.1 Task-first control plane. The Create page defines user intent in task terms and the control plane translates that task intent into execution-plane contracts. Scope: in scope. Maps to FR-001, FR-007, and FR-008.
- **DESIGN-REQ-002**: Source `docs/Tasks/TaskArchitecture.md`, section 5.1 Authoring and validation. The Create page renders the authoring experience, validates repository, runtime, publish mode, dependencies, and attachment policy, and collects text fields, preset state, Jira imports, and input attachments into a coherent draft. Scope: in scope. Maps to FR-002, FR-003, FR-005, FR-006, and FR-007.
- **DESIGN-REQ-003**: Source `docs/Tasks/TaskArchitecture.md`, section 5.1 Authoring and validation. Repository, Branch, and Publish Mode are rendered together in the Steps card, while Publish Mode remains submission data. Scope: in scope. Maps to FR-004 and FR-009.
- **DESIGN-REQ-004**: Source `docs/Tasks/TaskArchitecture.md`, task contract invariants. The submitted task uses `task.git.branch` as the single authored branch field, excludes `targetBranch`, and preserves Publish Mode semantics separately from visual placement. Scope: in scope. Maps to FR-008, FR-009, and FR-010.
- **DESIGN-REQ-005**: Source `docs/Tasks/TaskArchitecture.md`, task contract invariants. Authored preset metadata, step source metadata, and attachment references are task contract data used for reconstruction, audit, diagnostics, and rerun behavior. Scope: in scope. Maps to FR-005, FR-007, and FR-011.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The Create page MUST present task authoring in task terms rather than exposing workflow-internal concepts as the primary user model.
- **FR-002**: The Create page MUST collect task objective text, step content, preset state, Jira-imported content, input attachments, dependencies, runtime selection, repository selection, branch selection, and publish mode into one coherent draft.
- **FR-003**: The Create page MUST validate repository, runtime, publish mode, branch, dependency, and attachment policy state before allowing submission.
- **FR-004**: The Create page MUST render Repository, Branch, and Publish Mode controls together inside the Steps card.
- **FR-005**: The Create page MUST preserve task-level and step-level attachment target bindings when validating and submitting the draft.
- **FR-006**: The Create page MUST reject invalid drafts before submission and identify the authoring input that prevents submission.
- **FR-007**: Valid drafts MUST normalize into a canonical task-shaped payload that preserves user authoring intent from text, presets, Jira imports, attachments, and dependencies.
- **FR-008**: Valid submissions MUST represent the authored branch as `task.git.branch` and MUST NOT include `targetBranch`.
- **FR-009**: Moving Publish Mode into the Steps card MUST NOT change Publish Mode submission semantics.
- **FR-010**: Branch behavior MUST remain coherent for pull-request publishing and branch publishing, with the selected authored branch retaining its task-level meaning.
- **FR-011**: Preset-derived, Jira-imported, included, detached, or manually edited step provenance that is part of user authoring intent MUST remain traceable through draft normalization and submission.
- **FR-012**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `MM-641` and the original Jira preset brief for traceability.

### Key Entities

- **Task Draft**: The in-progress Create page authoring state, including objective text, steps, presets, Jira imports, attachments, dependencies, runtime, repository, branch, and publish mode.
- **Steps Card**: The Create page authoring area where step content is managed and where Repository, Branch, and Publish Mode are visually grouped for this story.
- **Task Payload**: The normalized task-shaped submission created from a valid draft, preserving authored intent and the single authored branch field.
- **Attachment Target Binding**: The relationship between an uploaded or selected attachment and its intended task objective or step target.
- **Authoring Provenance**: User-visible source metadata for manual, preset-derived, included, detached, or Jira-imported task content.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In validation, Repository, Branch, and Publish Mode are all visible inside the Steps card on supported Create page layouts.
- **SC-002**: In validation, 100% of tested invalid repository, runtime, publish-mode/branch, dependency, and attachment-policy drafts are blocked before submission with actionable feedback.
- **SC-003**: In validation, 100% of tested valid drafts produce a task-shaped payload containing `task.git.branch` and no `targetBranch`.
- **SC-004**: In validation, valid drafts containing presets, Jira imports, attachments, and dependencies preserve those authoring inputs through normalization and submission.
- **SC-005**: Final verification can trace `MM-641`, the original Jira preset brief, and DESIGN-REQ-001 through DESIGN-REQ-005 through the MoonSpec artifacts and implementation evidence.
