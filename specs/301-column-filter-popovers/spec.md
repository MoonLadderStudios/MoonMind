# Feature Specification: Column Filter Popovers, Chips, and Selection Semantics

**Feature Branch**: `301-column-filter-popovers`  
**Created**: 2026-05-05  
**Status**: Draft  
**Input**: User description: """
Use the Jira preset brief for MM-588 as the canonical Moon Spec orchestration input.

Additional constraints:

Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-588 MoonSpec Orchestration Input

## Source

- Jira issue: MM-588
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Column filter popovers, chips, and selection semantics
- Trusted fetch tool: `jira.get_issue`
- Trusted response artifact: `artifacts/moonspec-inputs/MM-588-trusted-jira-get-issue-summary.json`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira issue fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.
- Labels: moonmind-workflow-mm-af73ac39-5c56-460e-bd77-712adac541f3

## Canonical MoonSpec Feature Request

Jira issue: MM-588 from MM project
Summary: Column filter popovers, chips, and selection semantics
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-588 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-588: Column filter popovers, chips, and selection semantics

Source Reference
Source Document: docs/UI/TasksListPage.md
Source Title: Tasks List Page
Source Sections:
- 6. Desired page layout after column filters
- 9. Filter popover design
- 10. Selection semantics
- 11. Active filter chips
- 20. Non-goals
Coverage IDs:
- DESIGN-REQ-007
- DESIGN-REQ-012
- DESIGN-REQ-013
- DESIGN-REQ-014
- DESIGN-REQ-015
- DESIGN-REQ-027

As an operator, I want column filters with staged popover editing, include/exclude semantics, blanks, and active chips so I can refine task rows without detached top dropdowns.

Acceptance Criteria
- Checkbox and text changes remain staged until Apply.
- Cancel, Escape, or outside click closes without applying staged changes.
- Status filter uses canonical lifecycle order and maps to lifecycle state display precedence.
- Runtime stores raw identifiers while displaying human-readable labels.
- Repository supports value selection and existing exact text behavior.
- Date filters support bounds and blanks where meaningful.
- Deselecting `canceled` from all statuses creates an exclude filter represented as a chip such as `Status: not canceled`.
- Every active filter has a clickable chip with a remove action, and Clear filters restores the default task-run view.

Requirements
- Replace top Status and Repository controls with equivalent column filters.
- Add Runtime and Skill column filters.
- Keep values rendered as text and long lists bounded through virtualization, pagination, or server search.
- Reset pagination when filters apply.

## Relevant Implementation Notes

- Source design path: `docs/UI/TasksListPage.md`.
- Source sections from Jira brief are treated as runtime source requirements.
- Coverage IDs: DESIGN-REQ-007, DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-027.
- Jira Implementation plan field was empty or unavailable in the trusted response.
- Jira Test plan field was empty or unavailable in the trusted response.

## MoonSpec Classification Input

Classify this as a single-story runtime feature request for the Tasks List page: add column filter popovers, staged filter editing, include/exclude and blank-value semantics, active removable filter chips, and pagination reset behavior while preserving MM-588 traceability and the referenced Tasks List design requirements.

## Orchestration Constraints

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.
"""

## Classification

Input classification: single-story feature request. The Jira brief selects one independently testable runtime UI behavior story for the Tasks List page: operators can refine task rows through column-owned filter popovers, staged filter changes, include/exclude value semantics, blank handling, active chips, and default-view restoration. The source document is a broader design, but the Jira brief narrows the work to one story and does not require MoonSpec breakdown.

## User Story - Column Filter Refinement

**Summary**: As an operator, I want column filters with staged popover editing, include/exclude semantics, blanks, and active chips so I can refine task rows without detached top dropdowns.

**Goal**: Operators can refine the task-run table from column-owned filter popovers, review active filters as removable chips, and recover the default task-run view without accidentally applying unfinished filter edits or exposing non-task workflow browsing.

**Independent Test**: Render the Tasks List page with representative task rows and facet values, then exercise status, runtime, skill, repository, and date filter popovers to verify staged apply/cancel behavior, include and exclude semantics, blank handling, chip reopening/removal, clear-all behavior, URL/query state, and pagination reset.

**Acceptance Scenarios**:

1. **Given** an operator opens a value-list column filter, **When** they toggle checkboxes or type a search value, **Then** table rows, URL state, and pagination remain unchanged until they activate Apply.
2. **Given** an operator has staged changes in a filter popover, **When** they activate Cancel, press Escape, or click outside the popover, **Then** the popover closes and the previously applied filter remains unchanged.
3. **Given** the status filter starts from all lifecycle values selected, **When** the operator deselects `canceled` and applies the change, **Then** the task list excludes canceled rows, preserves future non-canceled statuses, and shows a chip labeled with product language such as `Status: not canceled`.
4. **Given** runtime, skill, repository, scheduled, or finished values may be blank, **When** an operator applies a blank-inclusive or blank-exclusive filter, **Then** the list uses the requested blank semantics and the active chip describes the blank condition.
5. **Given** active filters are applied, **When** the operator clicks a filter chip, **Then** the matching column popover opens with the applied filter state available for editing.
6. **Given** active filters are applied, **When** the operator removes one chip, **Then** only that column filter is cleared and all other active filters remain applied.
7. **Given** active filters or legacy status/repository URL filters are present, **When** the operator activates Clear filters, **Then** all column filters are cleared, pagination resets, and the normal task-run view is restored.
8. **Given** repository filtering was previously available through a top-level exact text input, **When** a legacy repository query is loaded or a repository column filter is applied, **Then** exact repository behavior remains available through the Repository column filter.

### Edge Cases

- A live update that introduces a new value while an exclude filter is active must keep the new value visible unless it matches the excluded set.
- A live update that introduces a new value while an include filter is active must hide the new value until the operator explicitly selects it.
- Long value lists must remain bounded through virtualization, pagination, or server search and must not make the popover unusable.
- Value labels, repository names, runtime names, and skill names must render as text and never as executable markup.
- Deselecting all values must either be prevented with a clear recovery path or require confirmation that the result will be empty.
- Filter changes must reset cursor pagination to the first result page.
- The normal Tasks List page must not expose system workflow browsing through column filters or legacy URL parameters.

## Assumptions

- The previous `MM-587` desktop header story provides or preserves the sortable/filterable header affordance; this story owns the popover behavior, filter state semantics, chips, and parity with legacy status and repository filtering.
- Server-provided facet counts are useful when present but are not required for a valid first implementation if value lists remain bounded and labels are correct.
- Date filter bounds use the existing local display conventions while URL/API state stores canonical date or timestamp values.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Status, Repository, Runtime, Skill, Scheduled, Created, and Finished filters MUST be available through column-owned filter popovers or the equivalent mobile filter surface.
- **FR-002**: The ordinary top Status and Repository filter controls MUST be replaced by equivalent column filter behavior once parity is available.
- **FR-003**: Checkbox, value-search, text, and date-bound edits inside a filter popover MUST remain staged until Apply is activated.
- **FR-004**: Cancel, Escape, and outside-click dismissal MUST close an open filter popover without applying staged edits.
- **FR-005**: Each value-list filter MUST support Select all as the no-filter state for that column.
- **FR-006**: Value-list filters MUST support include mode for explicitly selected values.
- **FR-007**: Value-list filters MUST support exclude mode when the operator deselects unwanted values from an otherwise-all checklist.
- **FR-008**: Status filtering MUST use canonical lifecycle ordering and MUST map each row using the same lifecycle display precedence used by the table.
- **FR-009**: Runtime filtering MUST store raw runtime identifiers while displaying human-readable runtime labels.
- **FR-010**: Skill filtering MUST use skill identifiers or stable skill labels from task row data while displaying readable labels.
- **FR-011**: Repository filtering MUST support value selection and preserve existing exact repository text-filter behavior.
- **FR-012**: Scheduled and Finished date filters MUST support inclusive bounds and blank-value filtering where meaningful.
- **FR-013**: Created date filters MUST support inclusive bounds and MUST not offer blank filtering when created timestamps are always present.
- **FR-014**: Long value lists MUST be bounded through virtualization, pagination, or server search.
- **FR-015**: Filter values and labels MUST render as text and MUST NOT render untrusted HTML.
- **FR-016**: Every active column filter MUST have a visible chip using product labels rather than raw API parameter names.
- **FR-017**: Clicking an active filter chip MUST open the corresponding column filter popover with the applied state available for editing.
- **FR-018**: Each active filter chip MUST provide a remove action that clears only that column filter.
- **FR-019**: Clear filters MUST clear all active column filters and restore the default task-run view.
- **FR-020**: Applying or clearing a filter MUST reset cursor pagination to the first result page.
- **FR-021**: Existing `state=<value>` URLs MUST load as an equivalent Status include filter for one value.
- **FR-022**: Existing `repo=<value>` URLs MUST load as an equivalent exact Repository include filter for one value.
- **FR-023**: After an operator changes filters in the new UI, shareable URL state SHOULD use canonical column filter encoding instead of legacy top-filter parameter names.
- **FR-024**: Normal Tasks List filtering MUST remain task-scoped and MUST NOT expose system workflow browsing through column filters or legacy URL parameters.
- **FR-025**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `MM-588` and this canonical Jira preset brief.

## Source Design Requirements

| ID | Source | Requirement Summary | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-007 | `docs/UI/TasksListPage.md` section 6 | Top status and repository controls move into column filters; runtime filtering is added; active filters are summarized as clickable chips; Clear filters remains available; normal task scope is preserved. | In scope | FR-001, FR-002, FR-016, FR-017, FR-019, FR-024 |
| DESIGN-REQ-012 | `docs/UI/TasksListPage.md` section 9 | Filter popovers are anchored, keyboard-accessible panels with staged edits, search, select all, clear/cancel/apply actions, display labels, bounded long lists, and safe text rendering. | In scope | FR-003, FR-004, FR-005, FR-014, FR-015 |
| DESIGN-REQ-013 | `docs/UI/TasksListPage.md` section 10 | Column filters combine AND semantics across columns with OR semantics within a column and distinguish include from exclude mode. | In scope | FR-006, FR-007, FR-020 |
| DESIGN-REQ-014 | `docs/UI/TasksListPage.md` section 11 | Active filters are summarized as chips; chips reopen matching popovers, clear individual filters, use product labels, and Clear filters restores the default task-run view. | In scope | FR-016, FR-017, FR-018, FR-019 |
| DESIGN-REQ-015 | `docs/UI/TasksListPage.md` sections 9.2 through 9.5 and 12.1 | Status, runtime, repository, and date filters have field-specific behavior, including lifecycle ordering, raw runtime identifiers with display labels, repository value and exact text support, date bounds, blanks, and legacy `state`/`repo` mapping. | In scope | FR-008, FR-009, FR-011, FR-012, FR-013, FR-021, FR-022, FR-023 |
| DESIGN-REQ-027 | `docs/UI/TasksListPage.md` section 20 | Non-goals such as spreadsheet editing, pivot tables, multi-column sort, raw Temporal query authoring, direct browser calls to Temporal, saved views, pagination replacement, Live updates removal, and ordinary system workflow browsing must not be introduced. | In scope | FR-014, FR-019, FR-024 |

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: UI tests prove staged checkbox/text/date edits do not alter rows, URL state, or pagination until Apply is activated.
- **SC-002**: UI tests prove Cancel, Escape, and outside-click dismissal preserve the previously applied filter state.
- **SC-003**: UI tests prove deselecting `canceled` from an otherwise-all Status checklist creates an exclude filter and a `Status: not canceled`-style chip.
- **SC-004**: UI tests prove Runtime stores raw identifiers while displaying human-readable labels in popovers and chips.
- **SC-005**: UI tests prove Repository supports both selectable values and legacy exact repository text mapping.
- **SC-006**: UI tests prove active chips reopen their column popovers, remove only their own filter, and Clear filters restores the default task-run view.
- **SC-007**: Boundary tests prove filter application resets pagination and preserves task-scoped list semantics.
- **SC-008**: Traceability evidence preserves `MM-588`, the canonical Jira preset brief, and DESIGN-REQ-007, DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-015, and DESIGN-REQ-027 in MoonSpec artifacts.
