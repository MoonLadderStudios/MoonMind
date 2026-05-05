# Feature Specification: Shareable Filter URL Compatibility

**Feature Branch**: `302-shareable-filter-url`
**Created**: 2026-05-05
**Status**: Draft
**Input**: User description: """
Use the Jira preset brief for MM-589 as the canonical Moon Spec orchestration input.

Additional constraints:


Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-589 MoonSpec Orchestration Input

## Source

- Jira issue: MM-589
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Shareable filter URL compatibility and canonical encoding
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-589 from MM project
Summary: Shareable filter URL compatibility and canonical encoding
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Source Reference
Source Document: docs/UI/TasksListPage.md
Source Title: Tasks List Page
Source Sections:
- 5.3 Current URL state
- 12. URL state in the desired design
- 12.1 Backward compatibility
- 12.2 Canonical filter encoding

Coverage IDs:
- DESIGN-REQ-006
- DESIGN-REQ-016
- DESIGN-REQ-017
- DESIGN-REQ-018

As an operator sharing a Tasks List view, I want old and new URLs to load predictably and fail safe so links keep their task-focused meaning without exposing broader workflow scopes.

Acceptance Criteria
- Existing `state=<value>` loads as a Status include filter.
- Existing `repo=<value>` loads as a Repository exact include filter.
- Existing `scope=system`, `scope=all`, system workflowType, and manifest entry links do not expose system or manifest rows in the normal page.
- New include/exclude filters serialize to canonical params such as `stateNotIn` and `targetRuntimeIn`.
- Contradictory include/exclude filters on the same field produce a clear validation error instead of ambiguous behavior.
- Empty filter lists are normalized away.
- Filter and page-size changes clear `nextPageToken` and the previous cursor stack.

Requirements
- Keep URL state synchronized with `history.replaceState`.
- Support comma-encoded and repeated-value representations where needed.
- Use product labels in chips while preserving raw canonical values in URL/API state.

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-589 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.
"""

<!-- Moon Spec specs contain exactly one independently testable user story. Use /speckit.breakdown for technical designs that contain multiple stories. -->

## User Story - Shareable Task Filter URLs

**Summary**: As an operator sharing a Tasks List view, I want old and new URLs to load predictably and fail safe so links keep their task-focused meaning without exposing broader workflow scopes.

**Goal**: Shared Tasks List URLs preserve task-list filter intent across legacy and canonical encodings, reject ambiguous contradictory filter state, and keep normal users bounded to task-run rows.

**Independent Test**: Can be fully tested by loading `/tasks/list` with legacy and canonical query strings, applying filters and page-size changes, and calling `/api/executions` with canonical filter params to verify URL state, request parameters, visible chips, pagination reset, and validation errors independently of unrelated task workflows.

**Acceptance Scenarios**:

1. **Given** an old shared link containing `state=<value>` and `repo=<value>`, **When** an operator opens Tasks List, **Then** the page loads a Status include filter and Repository exact include filter, fetches only task-run rows, and rewrites shareable state using supported canonical filter parameters.
2. **Given** an old shared link containing `scope=system`, `scope=all`, a system `workflowType`, or manifest entry state, **When** an operator opens Tasks List, **Then** the normal page does not expose system or manifest rows and either stays in the task-run view with a recoverable message or redirects only to an appropriate authorized diagnostic destination.
3. **Given** an operator applies include or exclude filters in the column filter UI, **When** the URL and API request are updated, **Then** the URL/API state uses canonical raw values such as `stateNotIn` and `targetRuntimeIn` while visible chips use product labels.
4. **Given** a shared URL or API call contains both include and exclude filters for the same field, **When** the page or API parses it, **Then** it produces a clear validation error instead of silently choosing one interpretation.
5. **Given** a shared URL contains empty filter lists or repeated value parameters, **When** the page loads and normalizes state, **Then** empty lists are removed and repeated values are treated equivalently to comma-encoded values.
6. **Given** a user changes filters or page size while a cursor is active, **When** the URL is synchronized, **Then** `nextPageToken` and the previous cursor stack are cleared.

### Edge Cases

- Blank or whitespace-only filter params are treated as empty lists and removed from normalized URL/API state.
- Duplicate values across comma-encoded or repeated parameters are de-duplicated without changing the first-seen order.
- Product labels such as `Codex CLI` are never serialized in place of raw runtime values such as `codex_cli`.
- Existing task-safe parameters such as `scope=tasks`, `workflowType=MoonMind.Run`, and `entry=run` do not create visible filters.

## Assumptions

- A contradictory include/exclude filter means both canonical include and exclude params are non-empty for the same field after trimming and empty-list normalization.
- Repository exact text remains the UI's existing exact repository filter representation while legacy `repo=<value>` is accepted as that exact include filter.

## Source Design Requirements

- **DESIGN-REQ-006** (Source: `docs/UI/TasksListPage.md` section 5.3 Current URL state): The Tasks List page MUST synchronize client-visible query state with `history.replaceState`, persist sorting, and reset pagination when filters or page size change. Scope: in scope. Maps to FR-001, FR-007.
- **DESIGN-REQ-016** (Source: `docs/UI/TasksListPage.md` section 12 URL state in the desired design): The URL MUST remain the shareable source of client-visible query state, including page size, current cursor, sort state, and column filter params. Scope: in scope. Maps to FR-001, FR-003, FR-007.
- **DESIGN-REQ-017** (Source: `docs/UI/TasksListPage.md` section 12.1 Backward compatibility): Existing URLs MUST fail safe, preserve task-safe meaning for `state` and `repo`, and MUST NOT reveal system or manifest workflows in the normal Tasks List page. Scope: in scope. Maps to FR-002, FR-006.
- **DESIGN-REQ-018** (Source: `docs/UI/TasksListPage.md` section 12.2 Canonical filter encoding): Canonical URL/API filters MUST support include/exclude lists, comma-encoded and repeated-value representations where needed, clear validation for contradictory include/exclude filters, and normalization of empty filter lists. Scope: in scope. Maps to FR-003, FR-004, FR-005, FR-006.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The Tasks List page MUST synchronize active filter, page size, cursor, and sort state into the browser URL using `history.replaceState`.
- **FR-002**: The Tasks List page MUST load legacy `state=<value>` as a Status include filter and legacy `repo=<value>` as a Repository exact include filter without broadening beyond task-run rows.
- **FR-003**: The Tasks List page and `/api/executions` request state MUST serialize new column filters with canonical raw-value params for include and exclude filters.
- **FR-004**: The Tasks List page and `/api/executions` MUST support comma-encoded and repeated-value representations for canonical multi-value filters.
- **FR-005**: The Tasks List page and `/api/executions` MUST reject contradictory include and exclude filters on the same field with a clear validation error.
- **FR-006**: The normal Tasks List page MUST fail safe for `scope=system`, `scope=all`, system workflow types, and manifest entry links by preventing system or manifest rows from being exposed.
- **FR-007**: Filter and page-size changes MUST clear `nextPageToken` and the previous cursor stack before the URL and request state are updated.
- **FR-008**: Visible filter chips MUST use product labels where available while URL and API state preserve raw canonical values.
- **FR-009**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `MM-589` and this canonical Jira preset brief.

### Key Entities

- **Task List URL State**: Shareable query parameters representing page size, cursor, sort, legacy compatibility inputs, and canonical column filters.
- **Column Filter**: A field-specific include, exclude, exact, text, date, or blank filter represented with raw values in URL/API state and product labels in UI chips.
- **Execution List Query**: The backend list request constructed from task scope, canonical filters, pagination, and authorization context.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Legacy `state` and `repo` shared links load with equivalent visible filter state and task-scoped API requests in automated UI tests.
- **SC-002**: System or manifest legacy links produce no system or manifest rows in automated UI/API tests.
- **SC-003**: Canonical include/exclude filter URLs and API requests preserve raw values and show human labels for at least runtime filters in automated UI tests.
- **SC-004**: Contradictory include/exclude filters produce deterministic validation errors in automated UI and API tests.
- **SC-005**: Empty lists are removed, repeated-value filters are accepted equivalently to comma values, and cursor state resets on filter or page-size changes in automated tests.
