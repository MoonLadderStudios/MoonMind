# Feature Specification: Desktop Columns and Compound Headers

**Feature Branch**: `300-desktop-columns-headers`  
**Created**: 2026-05-05  
**Status**: Draft  
**Input**: User description: """
Use the Jira preset brief for MM-587 as the canonical Moon Spec orchestration input.

Additional constraints:

Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-587 MoonSpec Orchestration Input

## Source

- Jira issue: MM-587
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Desktop columns and compound headers
- Labels: `moonmind-workflow-mm-af73ac39-5c56-460e-bd77-712adac541f3`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.
- Trusted response artifact: `artifacts/moonspec-inputs/MM-587-trusted-jira-get-issue.json`

## Canonical MoonSpec Feature Request

Jira issue: MM-587 from MM project
Summary: Desktop columns and compound headers
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-587 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-587: Desktop columns and compound headers

Source Reference
Source Document: docs/UI/TasksListPage.md
Source Title: Tasks List Page
Source Sections:
- 5.5 Current row model
- 5.6 Current desktop table
- 6. Desired page layout after column filters
- 7. Column set in the desired design
- 8. Column header interaction model
- 8.1 Sort behavior
- 20. Non-goals
Coverage IDs:
- DESIGN-REQ-006
- DESIGN-REQ-007
- DESIGN-REQ-008
- DESIGN-REQ-010
- DESIGN-REQ-011
- DESIGN-REQ-027

As an operator scanning tasks on desktop, I want each visible task column to own sorting and filtering controls so the table behaves like a compact operational spreadsheet.

Acceptance Criteria
- The default desktop table includes the desired task columns and excludes Kind, Workflow Type, Entry, and Started.
- Clicking a header label toggles sort without opening a filter popover.
- Clicking a filter icon opens the matching filter popover without changing sort.
- Sorted headers show direction and preserve `aria-sort` behavior.
- The initial sort is `scheduledFor` descending with documented timestamp and string sort defaults.
- Only one primary sort is required and multi-column sort controls are absent.

Requirements
- Implement reusable sortable/filterable table-header controls.
- Keep existing row display formatting and dependency summaries where still applicable.
- Do not introduce excluded non-goal behaviors.

## Orchestration Constraints

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.
"""

## Classification

Input classification: single-story feature request. The Jira brief selects one independently testable runtime UI behavior story for the Tasks List desktop table and does not require MoonSpec breakdown.

## User Story - Desktop Compound Table Headers

**Summary**: As an operator scanning tasks on desktop, I want each visible task column to own sorting and filtering controls so the table behaves like a compact operational spreadsheet.

**Goal**: The normal Tasks List desktop table keeps its task-focused column model while each visible header separates sorting from filtering, preserves accessible sort state, and exposes column filter controls for status, repository, and runtime without reintroducing system workflow browsing.

**Independent Test**: Render the Tasks List page with sample Temporal rows, activate header sort labels and filter buttons independently, and verify API requests, URL state, active chips, table columns, accessibility labels, and row formatting.

**Acceptance Scenarios**:

1. **Given** the Tasks List page renders on desktop, **When** the table is shown, **Then** visible columns are ID, Runtime, Skill, Repository, Status, Title, Scheduled, Created, and Finished, while Kind, Workflow Type, Entry, and Started are absent.
2. **Given** the page has loaded, **When** an operator clicks a header label, **Then** that column becomes the only primary sort target, timestamp columns default to descending, non-timestamp columns default to ascending, and no filter popover opens.
3. **Given** the page has loaded, **When** an operator clicks a header filter control, **Then** the matching filter popover opens without changing the current sort.
4. **Given** a column is sorted, **When** the operator reviews the header, **Then** the header preserves `aria-sort` and shows the matching ascending or descending indicator.
5. **Given** status, repository, or runtime filters are active, **When** the operator reviews filter chips, **Then** chips identify active filters and clicking a chip reopens the matching column filter.
6. **Given** rows are blocked on dependencies, **When** the table renders filtered or sorted results, **Then** dependency summaries remain visible under the title where applicable.

### Edge Cases

- Legacy workflow scope, workflow type, and entry URL parameters must not widen the normal Tasks List into system workflow visibility.
- Clearing filters must reset status, repository, and runtime filters and return pagination to the first page.
- Filtering must not clear the current sort unless the sorted column is no longer available.
- Runtime values should remain machine-readable in API/query state while displayed with existing human-readable labels.

## Requirements

- **FR-001**: The normal Tasks List desktop table MUST show ID, Runtime, Skill, Repository, Status, Title, Scheduled, Created, and Finished columns by default.
- **FR-002**: The normal Tasks List desktop table MUST NOT show Kind, Workflow Type, Entry, or Started columns.
- **FR-003**: Each visible desktop table header MUST present the sort target and filter target as separate controls.
- **FR-004**: Activating a header sort target MUST update only the single primary sort field and direction and MUST NOT open a filter popover.
- **FR-005**: Activating a header filter target MUST open the matching filter popover and MUST NOT change the current sort field or direction.
- **FR-006**: Sorted headers MUST preserve `aria-sort` behavior and visible ascending or descending direction.
- **FR-007**: The initial sort MUST be `scheduledFor` descending, with timestamp columns defaulting to descending when first sorted and non-timestamp columns defaulting to ascending.
- **FR-008**: Status, Repository, and Runtime filters MUST be available through column header popovers instead of top-of-page filter controls.
- **FR-009**: Active filter chips MUST summarize status, repository, and runtime filters and clicking a chip MUST reopen the corresponding column filter.
- **FR-010**: Clearing filters MUST clear status, repository, and runtime filters and reset pagination state.
- **FR-011**: Existing row display formatting, status pills, task links, date formatting, and dependency summaries MUST remain intact.
- **FR-012**: Normal Tasks List filtering MUST remain task-scoped and MUST NOT expose system workflow browsing through ordinary column filters or legacy URL parameters.
- **FR-013**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `MM-587` and this canonical Jira preset brief.

## Source Design Requirements

| ID | Source | Requirement Summary | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-006 | `docs/UI/TasksListPage.md` section 5.5 | The table row model includes task id, runtime, skills, repository, status, title, timestamps, dependency identifiers, and dependency blocking metadata. | In scope | FR-001, FR-011 |
| DESIGN-REQ-007 | `docs/UI/TasksListPage.md` section 5.6 | The current desktop table uses task-focused columns, scheduled descending default sort, timestamp/string/status sort rules, status pills, and dependency summaries. | In scope | FR-001, FR-002, FR-006, FR-007, FR-011 |
| DESIGN-REQ-008 | `docs/UI/TasksListPage.md` section 6 | Top filter dropdowns are replaced by column header filters; active filters remain summarized as chips; live updates, page size, and pagination stay outside table filters. | In scope | FR-008, FR-009, FR-010, FR-012 |
| DESIGN-REQ-010 | `docs/UI/TasksListPage.md` section 7 | The desired desktop table uses task columns and excludes Kind, Workflow Type, and Entry from the normal page. | In scope | FR-001, FR-002, FR-012 |
| DESIGN-REQ-011 | `docs/UI/TasksListPage.md` sections 8 and 8.1 | Header controls separate sort and filter targets, preserve `aria-sort`, show sorted/filter-active indicators, and keep single-column sort behavior. | In scope | FR-003, FR-004, FR-005, FR-006, FR-007 |
| DESIGN-REQ-027 | `docs/UI/TasksListPage.md` section 20 | Excluded non-goal behaviors, including ordinary system workflow browsing and multi-column sort controls, must not be introduced. | In scope | FR-002, FR-007, FR-012 |

## Success Criteria

- **SC-001**: UI tests prove all default desktop columns are present and excluded columns remain absent.
- **SC-002**: UI tests prove sort target activation changes sort state without opening a filter popover.
- **SC-003**: UI tests prove filter target activation opens the matching popover without changing sort state.
- **SC-004**: UI tests prove active status, repository, and runtime chips are clickable and reopen matching filters.
- **SC-005**: API or integration-boundary tests prove runtime filtering is sent through the normal task-scoped Temporal list request.
- **SC-006**: Final verification preserves `MM-587` and all in-scope source design requirement IDs.

## Assumptions

- Runtime filtering can use the existing `targetRuntime` identifier values already displayed in row data.
- Date range and advanced checklist filters remain outside this first story unless already present; this story requires the compound header and basic status, repository, and runtime filter migration.
