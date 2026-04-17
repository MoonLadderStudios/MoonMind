# Feature Specification: Create Page Composed Preset Drafts

**Feature Branch**: `197-create-page-composed-drafts`  
**Created**: 2026-04-17  
**Status**: Draft  
**Input**: User description: "Use the Jira preset brief for MM-384 as the canonical Moon Spec orchestration input.

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Canonical Jira Brief: `docs/tmp/jira-orchestration-inputs/MM-384-moonspec-orchestration-input.md`

# MM-384 MoonSpec Orchestration Input

## Source

- Jira issue: MM-384
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Document Create page composed preset drafts
- Labels: `moonmind-workflow-mm-22746271-d34b-494d-bdf8-5c9daefbbdd4`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-384 from MM project
Summary: Document Create page composed preset drafts
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-384 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-384: Document Create page composed preset drafts

Source Reference
- Source Document: docs/Tasks/PresetComposability.md
- Source Title: Preset Composability
- Source Sections:
  - 2. docs/UI/CreatePage.md
  - 8. Cross-document invariants
- Coverage IDs:
  - DESIGN-REQ-011
  - DESIGN-REQ-012
  - DESIGN-REQ-013
  - DESIGN-REQ-014
  - DESIGN-REQ-015
  - DESIGN-REQ-016
  - DESIGN-REQ-010
  - DESIGN-REQ-025
  - DESIGN-REQ-026

User Story
As a Mission Control user, I want the Create page to preserve preset bindings, grouped preview, detachment, reapply, save-as-preset, and edit/rerun reconstruction so composed preset authoring remains understandable and durable.

Acceptance Criteria
- CreatePage describes presets as authoring objects that may include other presets while execution uses flattened resolved steps.
- Draft state includes AppliedPresetBinding and StepDraft.source fields sufficient to track bindings, include paths, blueprint slugs, detachment, and expansion digest.
- Docs use preset-bound terminology instead of template-bound terminology.
- Preset application is server-expanded and receives binding metadata, flat steps, and per-step provenance; selecting a preset alone does not mutate the draft.
- Reapply updates still-bound steps by default, leaves detached steps untouched, and discloses the exact effect to the user.
- Save-as-preset preserves intact composition by default and requires explicit advanced action to flatten before save.
- Edit/rerun reconstruction preserves binding state when possible and clearly warns when only flat reconstruction is available.
- Testing requirements cover preview, apply, error handling, detachment, reapply, save-as-preset, reconstruction, and degraded fallback.

Requirements
- Define browser-side draft bindings as the source of preset authoring truth.
- Define preset grouping and insertion behavior without making the flattened execution order ambiguous.
- Define reapply, detachment, save-as-preset, edit/rerun, and submission boundaries for composed presets.
- Specify UI tests for success, error, and degraded reconstruction paths."

## User Story - Composed Preset Draft Authoring

**Summary**: As a Mission Control user, I want the Create page to preserve composed preset bindings, grouped preview, detachment, reapply, save-as-preset, and edit/rerun reconstruction so composed preset authoring remains understandable and durable.

**Goal**: A task author can understand which draft steps came from composed presets, see and preserve grouped composition state, safely detach or reapply preset-bound steps, and trust save-as-preset plus edit/rerun flows to retain binding state when possible.

**Independent Test**: The story can be tested independently by reviewing the Create page contract and UI behavior evidence for a composed preset draft, confirming the draft model captures preset bindings and step source state, and validating that preview, apply, reapply, detachment, save-as-preset, edit/rerun, and degraded reconstruction outcomes are all specified and test-covered.

**Acceptance Scenarios**:

1. **Given** a composed preset expands into multiple concrete steps, **When** the Create page presents the draft, **Then** users can distinguish the preset authoring object, grouped composition preview, and flattened execution-facing steps without ambiguity.
2. **Given** a user selects a preset, **When** the user has not explicitly applied it, **Then** the draft remains unchanged and no binding metadata or flat steps are inserted.
3. **Given** a preset is applied by the server, **When** the expansion succeeds, **Then** the draft receives binding metadata, flat steps, per-step source/provenance state, include paths, blueprint slugs, detachment state, and expansion digest.
4. **Given** a user edits a preset-bound step, **When** the edited instructions or attachments no longer match the source blueprint input, **Then** that step is treated as detached from the preset-bound source while preserving the authored draft content.
5. **Given** a previously applied preset is reapplied, **When** some steps are still bound and others are detached, **Then** still-bound steps are eligible for update, detached steps are left untouched, and the page discloses the exact effect before the user proceeds.
6. **Given** a user saves the current draft as a preset, **When** intact composition can be preserved, **Then** save-as-preset preserves include composition by default and only flattens through an explicit advanced action.
7. **Given** a user edits or reruns an existing task, **When** the original snapshot includes usable binding state, **Then** the draft reconstruction preserves that state; when only flat reconstruction is possible, the page warns clearly.
8. **Given** expansion, preview, or reconstruction cannot load required preset metadata, **When** the user continues authoring, **Then** the draft is not silently corrupted and the degraded fallback is visible.

### Edge Cases

- The referenced source document `docs/Tasks/PresetComposability.md` is absent in the current checkout; the implementation must use the preserved MM-384 Jira brief plus current Create page and Task Presets docs as the traceable source.
- A composed preset include tree exceeds configured expansion limits or contains unreadable preset references; the Create page must expose a non-mutating error or degraded reconstruction warning rather than inserting partial state.
- A saved draft contains flat steps with no recoverable binding metadata; edit/rerun reconstruction must warn that binding state could not be recovered.
- A user manually reorders or edits a preset-bound step before reapply; detached state must prevent unexpected overwrite.

## Assumptions

- The missing `docs/Tasks/PresetComposability.md` source reference maps to desired-state preset composition requirements that are currently represented in `docs/Tasks/TaskPresetsSystem.md` and in the MM-384 Jira brief.
- This story is runtime mode because it defines product behavior for Create page draft state and user interactions; the immediate implementation may update canonical product docs without changing executable UI code if the existing UI is not yet implementing this contract.

## Source Design Requirements

- **DESIGN-REQ-010**: Source `docs/UI/CreatePage.md` and `docs/Tasks/TaskPresetsSystem.md` cross-document invariants. The Create page MUST keep runtime execution semantics flattened while preserving preset authoring metadata in the draft. Scope: in scope. Mapped to FR-001, FR-004, FR-009.
- **DESIGN-REQ-011**: Source MM-384 brief. The Create page MUST describe presets as authoring objects that may include other presets while execution uses flattened resolved steps. Scope: in scope. Mapped to FR-001, FR-009.
- **DESIGN-REQ-012**: Source MM-384 brief. Browser draft state MUST include `AppliedPresetBinding` and `StepDraft.source` fields sufficient to track bindings, include paths, blueprint slugs, detachment, and expansion digest. Scope: in scope. Mapped to FR-002, FR-003.
- **DESIGN-REQ-013**: Source MM-384 brief. Create page terminology MUST use preset-bound language instead of template-bound language for composed preset state. Scope: in scope. Mapped to FR-010.
- **DESIGN-REQ-014**: Source MM-384 brief. Preset application MUST be server-expanded and return binding metadata, flat steps, and per-step provenance; selecting a preset alone MUST NOT mutate the draft. Scope: in scope. Mapped to FR-004, FR-005.
- **DESIGN-REQ-015**: Source MM-384 brief. Reapply MUST update still-bound steps by default, leave detached steps untouched, and disclose the exact effect to the user. Scope: in scope. Mapped to FR-006, FR-007.
- **DESIGN-REQ-016**: Source MM-384 brief. Save-as-preset MUST preserve intact composition by default and require an explicit advanced action to flatten before save. Scope: in scope. Mapped to FR-008.
- **DESIGN-REQ-025**: Source MM-384 brief. Edit/rerun reconstruction MUST preserve binding state when possible and clearly warn when only flat reconstruction is available. Scope: in scope. Mapped to FR-011.
- **DESIGN-REQ-026**: Source MM-384 brief. Testing requirements MUST cover preview, apply, error handling, detachment, reapply, save-as-preset, reconstruction, and degraded fallback. Scope: in scope. Mapped to FR-012.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The Create page contract MUST distinguish preset authoring objects, composed include groups, and flattened execution-facing steps.
- **FR-002**: Browser draft state MUST include an `AppliedPresetBinding` concept that captures applied preset identity, include path, expansion digest, dirty/reapply state, and recoverability.
- **FR-003**: Each preset-expanded `StepDraft` MUST include a `source` concept that can represent local steps, preset-bound steps, detached steps, and flat reconstructed steps.
- **FR-004**: Preset application MUST be described as a server-expanded action that returns binding metadata, grouped composition state, flattened steps, and per-step provenance.
- **FR-005**: Selecting a preset without applying it MUST NOT mutate the authored draft.
- **FR-006**: Reapply MUST identify which still-bound steps will update and which detached steps will remain unchanged before the user confirms the operation.
- **FR-007**: Manual edits to preset-bound instructions or attachments MUST detach the affected source relationship without deleting authored content.
- **FR-008**: Save-as-preset MUST preserve intact composition by default and require an explicit advanced flatten action when a user wants to serialize concrete steps instead.
- **FR-009**: Submission and runtime execution semantics MUST remain based on flattened resolved steps; nested preset semantics MUST NOT cross the execution boundary.
- **FR-010**: Create page documentation and user-facing terminology MUST use preset-bound terminology rather than template-bound terminology for composed preset state.
- **FR-011**: Edit and rerun reconstruction MUST preserve binding state when recoverable and clearly warn when only flat reconstruction is available.
- **FR-012**: Validation coverage MUST include preview, apply, error handling, detachment, reapply, save-as-preset, reconstruction, and degraded fallback behavior.
- **FR-013**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key MM-384 and the original Jira preset brief for traceability.

### Key Entities

- **AppliedPresetBinding**: A browser draft record for an applied preset composition, including preset identity, include path, expansion digest, grouped preview metadata, dirty/reapply state, and whether binding reconstruction is complete.
- **StepDraft.source**: A per-step source record identifying local authored steps, preset-bound steps, detached steps, or flat reconstructed steps.
- **Grouped Composition Preview**: The user-facing representation that groups expanded steps by preset/include provenance while keeping the execution order flattened.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A reviewer can trace every MM-384 acceptance criterion to at least one functional requirement and one implementation or validation task.
- **SC-002**: The Create page contract explicitly names `AppliedPresetBinding`, `StepDraft.source`, preset-bound state, detached state, expansion digest, include paths, grouped preview, save-as-preset preservation, and flat reconstruction warning behavior.
- **SC-003**: A search of `docs/UI/CreatePage.md` finds no remaining `template-bound`, `appliedTemplates`, or `AppliedTemplateState` terminology for composed preset draft state.
- **SC-004**: Validation evidence covers the non-mutating select flow, server-expanded apply flow, detachment, reapply disclosure, save-as-preset preservation, edit/rerun reconstruction, and degraded fallback.
