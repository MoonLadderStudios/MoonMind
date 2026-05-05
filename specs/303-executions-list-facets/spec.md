# Feature Specification: Executions List and Facet API Support for Column Filters

**Feature Branch**: `303-executions-list-facets`
**Created**: 2026-05-05
**Status**: Draft
**Input**: User description: """
Use the Jira preset brief for MM-590 as the canonical Moon Spec orchestration input.

Additional constraints:

Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-590 MoonSpec Orchestration Input

## Source

- Jira issue: MM-590
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Executions list and facet API support for column filters
- Labels: moonmind-workflow-mm-af73ac39-5c56-460e-bd77-712adac541f3
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Implement Jira issue MM-590: Executions list and facet API support for column filters.

As the Tasks List UI, I need server-authoritative list and facet APIs for multi-column filtering so results, counts, pagination, and popover values reflect the authorized task universe beyond the current page.

## Source Reference

- Jira issue: MM-590
- Issue type: Story
- Summary: Executions list and facet API support for column filters
- Status: In Progress
- Source document: docs/UI/TasksListPage.md
- Source title: Tasks List Page
- Source sections:
  - 5.4 Current API request
  - 13. API and data requirements for column filtering
  - 18. Security and privacy
- Coverage IDs:
  - DESIGN-REQ-006
  - DESIGN-REQ-019
  - DESIGN-REQ-020
  - DESIGN-REQ-025

## Acceptance Criteria

- List results are sorted and filtered by the server for supported canonical fields.
- Pagination remains deterministic under active filters.
- Count and countMode describe the fully filtered result when available.
- Facet requests include all active filters except the requested facet by default.
- Static facets such as Status can use frontend enums plus server counts, while dynamic facets come from server data.
- Facet failure does not break the table and can fall back to current-page values with a visible notice.
- System-only values and unauthorized values never appear in list rows, facets, or counts for `/tasks/list`.

## Requirements

- Use raw canonical values rather than display labels for API filters.
- Reject contradictory filters clearly.
- Bound text lengths and value-list sizes.

## Traceability Requirement

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve Jira issue key MM-590 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.
"""

<!-- Moon Spec specs contain exactly one independently testable user story. Use /speckit.breakdown for technical designs that contain multiple stories. -->

## User Story - Server-Authoritative Column Filter Data

**Summary**: As the Tasks List UI, I want server-authoritative list and facet data for task column filters so operators see accurate results, counts, pagination, and popover values across the authorized task universe rather than only the current page.

**Goal**: Operators can apply supported column filters to the Tasks List and receive stable task rows, counts, pagination behavior, and facet values that are scoped to their authorized task executions.

**Independent Test**: Can be fully tested by calling the task execution list and facet request surfaces with sort, include, exclude, text, date, blank, and contradictory filter combinations, then verifying returned rows, counts, pagination tokens, facet values, fallback behavior, and authorization boundaries.

**Acceptance Scenarios**:

1. **Given** task executions with different canonical field values, **When** a list request includes supported column filters and sort options, **Then** results are filtered and sorted by the server using raw canonical values rather than display labels.
2. **Given** active filters and multiple result pages, **When** the list request is paginated, **Then** pagination remains deterministic and `count` plus `countMode` describe the fully filtered result when that count is available.
3. **Given** an operator opens a filter popover for a facet, **When** the facet request is made, **Then** it applies all active filters except the requested facet by default and returns scoped facet values with counts beyond the current page where available.
4. **Given** static and dynamic facet fields, **When** facet data is requested, **Then** static status facets can use the canonical status set with server counts while dynamic facets are derived from authorized server data.
5. **Given** a facet request fails or cannot produce authoritative values, **When** the Tasks List uses fallback values from the current page, **Then** the table remains usable and exposes that fallback state to the user.
6. **Given** filters reference system-only or unauthorized values, **When** list or facet requests are evaluated, **Then** unauthorized rows, values, and counts are never returned for the normal Tasks List.
7. **Given** contradictory filters, unsupported values, overlong text, or excessive value lists, **When** the request is validated, **Then** the system returns a clear structured validation error without leaking raw backend query failures.

### Edge Cases

- Include and exclude filters for the same field are submitted together.
- Text filters contain leading or trailing whitespace, empty strings, or values over the configured length limit.
- Value-list filters contain duplicates, unknown values, comma-containing values, or more values than the request limit allows.
- Date range filters have only one bound, reversed bounds, blank inclusion, or invalid date formats.
- Facet requests are made for a field that is not facet-capable.
- Active filters match no rows on the first page or on a later page.
- System workflow scopes, workflow types, entries, or system-only facet values are attempted through normal task-list filters.

## Assumptions

- This story covers backend task-list and facet support plus minimal UI error/fallback integration needed for the existing Tasks List to consume those responses; the full column popover interaction model remains covered by separate UI stories.
- The normal Tasks List remains bounded to user-visible task executions even if lower-level execution services can query broader workflow scopes.
- Exact count may be unavailable for some backend sources, but the response must communicate count confidence through `countMode`.

## Source Design Requirements

- **DESIGN-REQ-006** *(docs/UI/TasksListPage.md sections 5.4 and 13.1)*: The list request must support server-side filtering, sorting, deterministic pagination, and filtered count metadata for the normal task list. Scope: in scope. Mapped to FR-001, FR-002, FR-003.
- **DESIGN-REQ-019** *(docs/UI/TasksListPage.md section 13.2)*: Facet requests must return scoped values and counts beyond the current page, applying all active filters except the requested facet by default. Scope: in scope. Mapped to FR-004, FR-005, FR-006.
- **DESIGN-REQ-020** *(docs/UI/TasksListPage.md sections 12.2, 13.1, 13.2)*: API and URL-facing filter values must use raw canonical values, support include/exclude semantics, and reject contradictory filters clearly. Scope: in scope. Mapped to FR-002, FR-007.
- **DESIGN-REQ-025** *(docs/UI/TasksListPage.md section 18)*: List and facet behavior must preserve security and privacy by scoping values to authorization, bounding untrusted input, rejecting invalid requests with structured errors, and rendering labels as text. Scope: in scope. Mapped to FR-006, FR-007.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST apply server-authoritative sorting and filtering for supported normal task-list canonical fields without relying on display labels for query behavior.
- **FR-002**: The system MUST support multi-value include filters, multi-value exclude filters, text filters, date range filters, and meaningful blank/null filters with AND semantics across columns and OR semantics within a column.
- **FR-003**: The system MUST keep pagination deterministic under active filters and return `count` plus `countMode` metadata that describes the fully filtered result when a count is available.
- **FR-004**: The system MUST provide a facet request surface that returns authorized facet values, labels, counts when available, blank counts when meaningful, truncation state, count confidence, and pagination state for facet-capable fields.
- **FR-005**: The Tasks List client-facing behavior MUST tolerate facet failures by keeping the table usable and exposing a visible fallback or error state when values are limited to the current page.
- **FR-006**: The system MUST prevent system-only or unauthorized task values from appearing in normal task-list rows, facets, or counts regardless of submitted filters.
- **FR-007**: The system MUST reject contradictory filters, unsupported fields or values, overlong text filters, excessive value lists, and invalid date ranges with clear structured validation errors.
- **FR-008**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `MM-590` and this canonical Jira preset brief for traceability.

### Key Entities

- **Execution List Query**: The task-list request state, including source, page size, pagination token, sort field, sort direction, canonical field filters, text filters, date ranges, and blank/null options.
- **Execution List Result**: A page of authorized task rows plus pagination token, filtered count, and count confidence metadata.
- **Facet Query**: A request for one facet field with the active filter context excluding the requested facet unless explicitly scoped otherwise.
- **Facet Result**: Facet field identity, authorized facet values with labels and counts, blank count when meaningful, count confidence, truncation state, and facet pagination token.
- **Filter Validation Error**: A structured failure describing invalid, contradictory, or oversized filter input without exposing raw backend query internals.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A list request with at least two active column filters and a supported sort returns only matching authorized task rows in deterministic order across repeated calls.
- **SC-002**: Paginated filtered list requests preserve stable page traversal and report count metadata for the same filtered universe rather than the current page only.
- **SC-003**: A facet request for a dynamic field returns values and counts from the authorized filtered result universe beyond the current page when matching rows exist outside the loaded page.
- **SC-004**: A facet request applies all active filters except the requested facet by default, so changing another filter changes the returned facet values and counts predictably.
- **SC-005**: Invalid or contradictory filter requests return structured validation errors instead of unhandled backend exceptions or raw query failures.
- **SC-006**: Attempts to reveal system-only or unauthorized values through list or facet filters return no unauthorized rows, values, or counts.
- **SC-007**: Verification evidence preserves `MM-590`, the canonical Jira preset brief, and DESIGN-REQ-006, DESIGN-REQ-019, DESIGN-REQ-020, and DESIGN-REQ-025 in MoonSpec artifacts.
