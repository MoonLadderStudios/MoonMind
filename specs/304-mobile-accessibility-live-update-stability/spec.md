# Feature Specification: Mobile, Accessibility, and Live-Update Stability

**Feature Branch**: `304-mobile-accessibility-live-update-stability`
**Created**: 2026-05-05
**Status**: Draft
**Input**: User description: """
Use the Jira preset brief for MM-591 as the canonical Moon Spec orchestration input.

Additional constraints:

Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-591 MoonSpec Orchestration Input

## Source

- Jira issue: MM-591
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Mobile, accessibility, and live-update stability
- Labels: `moonmind-workflow-mm-af73ac39-5c56-460e-bd77-712adac541f3`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-591 from MM project
Summary: Mobile, accessibility, and live-update stability
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Source Reference
Source Document: docs/UI/TasksListPage.md
Source Title: Tasks List Page
Source Sections:
- 5.7 Current mobile cards
- 14. Live updates and filter stability
- 15. Accessibility requirements
- 16. Mobile behavior
Coverage IDs:
- DESIGN-REQ-006
- DESIGN-REQ-021
- DESIGN-REQ-022
- DESIGN-REQ-023

As an operator on any device or assistive technology, I want equivalent filters, accessible controls, and stable staged selections while live updates run so the Tasks List remains usable during active monitoring.

Acceptance Criteria
- Mobile users can reach status, runtime, skill, repository, title, ID, and date filters without the removed top dropdowns.
- Mobile filter changes reset pagination just like desktop.
- Focus moves into filter UI on open and returns to the originating control on close.
- Escape cancels staged changes and Enter on Apply commits them.
- Checkbox labels include value label and count when shown.
- Color is not the only active sort/filter/status indicator.
- When a popover or sheet editor is open, live updates do not overwrite staged selections.

Requirements
- Keep desktop and mobile filter semantics equivalent.
- Show subtle update notices when facet values change after a filter editor opens if implemented.
- Keep system workflows unavailable from mobile task-card views.

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-591 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.
"""

## Classification

Input classification: single-story feature request. The Jira brief selects one independently testable runtime UI behavior story from `docs/UI/TasksListPage.md`: Tasks List filters must remain reachable and accessible across desktop and mobile while live updates run.

## User Story - Stable Accessible Task Filters

**Summary**: As an operator on any device or assistive technology, I want equivalent filters, accessible controls, and stable staged selections while live updates run so that the Tasks List remains usable during active monitoring.

**Goal**: The Tasks List page exposes the same task filter capabilities to mobile card users and desktop table users, keeps filter dialogs keyboard accessible, preserves task-only visibility, and prevents live polling from disrupting staged filter edits.

**Independent Test**: Render the Tasks List page with mocked execution data, open and operate desktop filter dialogs by keyboard, operate the mobile filter controls for ID, title, status, runtime, skill, repository, and dates, and verify the resulting task-scoped API URLs and focus behavior without exposing workflow-kind controls.

**Acceptance Scenarios**:

1. **Given** a mobile-width operator uses the Tasks List card view, **When** they need to filter tasks, **Then** status, runtime, skill, repository, title, ID, and date filters are reachable without the removed top dropdowns.
2. **Given** any mobile filter changes, **When** the filter is applied, **Then** pagination resets to the first page and the API request remains bounded to task executions only.
3. **Given** a desktop column filter is opened, **When** the dialog appears, **Then** focus moves into the filter UI and returns to the originating filter control when the dialog closes.
4. **Given** a desktop filter has staged but unapplied changes, **When** the operator presses Escape or clicks outside, **Then** the staged changes are discarded and no filtered list request is sent.
5. **Given** a desktop text filter has staged changes, **When** the operator presses Enter from inside the filter dialog, **Then** the changes are applied, pagination resets, and focus returns to the originating filter control.
6. **Given** live updates are enabled while a filter editor is open, **When** the polling interval elapses, **Then** live polling does not overwrite the staged filter editor state.
7. **Given** ordinary Tasks List users inspect mobile cards or column filters, **When** URL parameters or controls are manipulated, **Then** system workflows remain unavailable from the ordinary task-card view.

### Edge Cases

- Legacy task-list URLs that include workflow-kind parameters continue to fail safe into task-only visibility.
- Empty text filters are normalized away instead of sending no-op filter parameters.
- Active filters remain visible as chips so color is not the only indicator of filter state.
- Facet values may be unavailable; the page can continue using current page values without blocking text/date filters.

## Assumptions

- The existing Tasks List page already owns the normal task-only query and desktop column-filter model; this story completes mobile parity and stability gaps rather than introducing a new diagnostics route.
- The source brief says subtle update notices are optional, so the required behavior is stable staged selection; notices are not mandatory for this story.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Mobile task-card users MUST be able to reach ID, runtime, skill, repository, status, title, scheduled, created, and finished filters without the removed top dropdown controls.
- **FR-002**: Mobile filter changes MUST reset pagination to the first page using the same task-scoped query semantics as desktop filters.
- **FR-003**: Desktop filter controls MUST be separately reachable from sort controls and expose accessible names that distinguish active and inactive filter state.
- **FR-004**: Opening a desktop filter dialog MUST move focus into the dialog, and closing it through cancel, Escape, outside click, Enter apply, or apply button MUST return focus to the originating filter control.
- **FR-005**: Escape and outside-click dismissal MUST discard staged filter changes without issuing a filtered list request.
- **FR-006**: Enter from inside a desktop filter dialog MUST apply staged changes when the target is not a multiline text input.
- **FR-007**: Live polling MUST NOT overwrite staged filter selections while a desktop filter editor is open.
- **FR-008**: Active sort, active filter, and status state MUST remain communicated through text, accessible names, icons/glyphs, or chips rather than color alone.
- **FR-009**: Ordinary mobile task-card views MUST NOT expose system workflow visibility or workflow-kind browsing controls.
- **FR-010**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `MM-591` and this canonical Jira preset brief.

## Source Design Requirements

- **DESIGN-REQ-006** (`docs/UI/TasksListPage.md` section 5.7): Mobile cards show task title, ID, runtime, skill, workflow metadata, status, repository, dates, dependency summary, and one full-width details action. Scope: in scope, mapped to FR-001 and FR-009.
- **DESIGN-REQ-021** (`docs/UI/TasksListPage.md` section 14): Live updates must not disrupt staged filter choices; polling can continue only when it does not overwrite open editor state. Scope: in scope, mapped to FR-005 and FR-007.
- **DESIGN-REQ-022** (`docs/UI/TasksListPage.md` section 15): Sort and filter controls must be separately keyboard reachable, expose correct ARIA state, move focus into and out of filter UI, support Escape/Enter behavior, and avoid color-only indicators. Scope: in scope, mapped to FR-003 through FR-006 and FR-008.
- **DESIGN-REQ-023** (`docs/UI/TasksListPage.md` section 16): Mobile filter behavior must expose the same filterable task columns as desktop, reset pagination on changes, and keep system workflows out of ordinary task cards. Scope: in scope, mapped to FR-001, FR-002, and FR-009.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Automated UI tests verify mobile controls for ID, runtime, skill, repository, status, title, scheduled, created, and finished filters are present.
- **SC-002**: Automated UI tests verify mobile filter changes produce task-scoped execution list requests and omit stale pagination cursors.
- **SC-003**: Automated UI tests verify a desktop filter dialog receives focus on open and returns focus to its originating control after keyboard apply.
- **SC-004**: Automated UI tests verify staged changes are not requested until Apply or Enter commits them.
- **SC-005**: Automated UI tests verify ordinary Tasks List controls do not expose Scope, Workflow Type, Entry, Kind, or system workflow filters.
- **SC-006**: Traceability review confirms `MM-591`, the canonical Jira preset brief, and DESIGN-REQ-006, DESIGN-REQ-021, DESIGN-REQ-022, and DESIGN-REQ-023 remain preserved in MoonSpec artifacts and verification evidence.
