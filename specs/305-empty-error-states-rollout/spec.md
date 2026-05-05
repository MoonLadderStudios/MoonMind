# Feature Specification: Empty/Error States and Regression Coverage for Final Rollout

**Feature Branch**: `305-empty-error-states-rollout`  
**Created**: 2026-05-05  
**Status**: Draft  
**Input**: User description: """
Use the Jira preset brief for MM-592 as the canonical Moon Spec orchestration input.

Additional constraints:

Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-592 MoonSpec Orchestration Input

## Source

- Jira issue: MM-592
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Empty/error states and regression coverage for final rollout
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.
- Trusted response artifact: `artifacts/moonspec-inputs/MM-592-trusted-jira-get-issue.json`

## Canonical MoonSpec Feature Request

Jira issue: MM-592 from MM project
Summary: Empty/error states and regression coverage for final rollout
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-592 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-592: Empty/error states and regression coverage for final rollout

Source Reference
Source Document: docs/UI/TasksListPage.md
Source Title: Tasks List Page
Source Sections:
- 5.8 Current empty, loading, error, and pagination states
- 17. Empty and error states after column filters
- 19. Testing contract
- 20. Non-goals
- 21. Desired implementation sequence
Coverage IDs:
- DESIGN-REQ-006
- DESIGN-REQ-024
- DESIGN-REQ-026
- DESIGN-REQ-027
- DESIGN-REQ-028

As a MoonMind maintainer, I want the final column-filter rollout covered by regression tests and recoverable empty/error states so the old filter form can be removed without losing current behavior.

Acceptance Criteria
- Loading state still shows `Loading tasks...`.
- API errors render a visible error notice.
- Empty first pages show `No tasks found for the current filters.` and include Clear filters when filters are active.
- Empty later pages keep previous-page navigation available.
- Facet request failures show an inline retry/fallback path without breaking the table.
- Invalid filter parameters show structured errors and preserve user filter state for editing.
- The old top Scope, Workflow Type, Status, Entry, and Repository controls are absent after column-filter parity.
- The documented regression tests pass before rollout is considered complete.

Requirements
- Treat TDD and regression evidence as required rollout gates.
- Keep non-goals excluded from final UX.
- Use the recommended implementation sequence without encoding a migration diary into canonical docs.

## Relevant Implementation Notes

- Source design path: `docs/UI/TasksListPage.md`.
- Source sections from Jira brief are treated as runtime source requirements.
- Coverage IDs: DESIGN-REQ-006, DESIGN-REQ-024, DESIGN-REQ-026, DESIGN-REQ-027, DESIGN-REQ-028.
- Jira Implementation plan field was empty or unavailable in the trusted response.
- Jira Test plan field was empty or unavailable in the trusted response.

## MoonSpec Classification Input

Classify this as a single-story runtime feature request for the Tasks List page: complete the final column-filter rollout by preserving recoverable loading, API error, empty, pagination, facet-failure, invalid-filter, top-control-removal, non-goal, and regression-test behavior while preserving MM-592 traceability and the referenced Tasks List design requirements.

## Orchestration Constraints

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.
"""

## Classification

Input classification: single-story feature request. The Jira brief selects one independently testable runtime UI rollout story for the Tasks List page: final column-filter parity must keep recoverable loading, error, empty, pagination, facet-failure, invalid-filter, and regression-gate behavior while removing the old top filter form. The source document is a broader desired-state design, but the Jira brief narrows this run to the final rollout stability story and does not require MoonSpec breakdown.

## User Story - Recoverable Final Column-Filter Rollout

**Summary**: As a MoonMind maintainer, I want the final column-filter rollout covered by regression tests and recoverable empty/error states so the old filter form can be removed without losing current behavior.

**Goal**: Operators can use the Tasks List after column-filter parity with clear loading, error, empty, pagination, facet fallback, and invalid-filter recovery behavior, while maintainers have regression evidence that the old top Scope, Workflow Type, Status, Entry, and Repository controls are absent and non-goals remain excluded.

**Independent Test**: Render the Tasks List page with mocked loading, API error, empty first-page, empty later-page, facet-failure, invalid-filter, and populated responses, then verify visible recovery paths, preserved filter state, previous-page navigation, old-control absence, and documented regression coverage.

**Acceptance Scenarios**:

1. **Given** the Tasks List request is loading, **When** the page renders, **Then** it shows `Loading tasks...`.
2. **Given** the list API returns an error, **When** the page renders the failure, **Then** the operator sees a visible error notice with a useful sanitized message.
3. **Given** active filters produce no rows on the first page, **When** the empty state is shown, **Then** it says `No tasks found for the current filters.` and keeps `Clear filters` available when filters are active.
4. **Given** pagination has moved past the first page, **When** the later page returns no rows, **Then** the previous-page action remains available so the operator can recover.
5. **Given** a facet request fails while the table data is still available, **When** a filter editor is opened, **Then** an inline retry or fallback notice appears without breaking the table.
6. **Given** invalid or contradictory filter parameters are present, **When** validation runs locally or the API rejects them, **Then** structured errors are visible and the user's filter state remains editable or clearable.
7. **Given** column-filter parity is active, **When** the control deck renders, **Then** the old top Scope, Workflow Type, Status, Entry, and Repository controls are absent.
8. **Given** rollout validation is reviewed, **When** regression evidence is inspected, **Then** loading, error, empty, pagination, facet fallback, invalid-filter, old-control absence, non-goal safety, and traceability tests are present and passing or have exact blockers recorded.

### Edge Cases

- Empty later pages can occur after stale cursors or live updates and must not strand the operator without previous-page navigation.
- Facet service failures must not hide already-loaded task rows or disable text/date filters that do not require facet values.
- API validation responses may contain structured detail objects or simple strings; the UI must show a sanitized useful message.
- Legacy URLs that attempt system or manifest browsing must fail safe without reintroducing old workflow-kind controls.
- Empty states should not guess which filter is wrong when multiple active chips exist.

## Assumptions

- Prior column-filter stories already provide the column popovers, URL mapping, mobile parity, task-scoped visibility, and most active-chip behavior; this story owns final rollout recovery states and regression evidence.
- Compose-backed integration is not required for this frontend-focused story unless backend filter validation behavior changes.
- API error messages must be sanitized and must not expose credentials or raw provider details.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The Tasks List page MUST show `Loading tasks...` while the list request is loading.
- **FR-002**: List API failures MUST render a visible error notice with a sanitized message derived from structured API detail when available.
- **FR-003**: Empty first pages MUST show `No tasks found for the current filters.`.
- **FR-004**: Empty first pages with active filters MUST keep `Clear filters` available and enabled.
- **FR-005**: Empty later pages MUST keep previous-page navigation available.
- **FR-006**: Pagination MUST continue to use the opaque next-page token and client-side previous cursor stack behavior.
- **FR-007**: Facet request failures MUST show an inline fallback, retry, or current-page-values notice inside the filter UI without breaking the table.
- **FR-008**: Invalid or contradictory filter parameters detected before a list request MUST show structured validation errors and preserve recovery through editable filter state or Clear filters.
- **FR-009**: Invalid filter parameters rejected by the list API MUST show structured validation detail when provided and preserve active filter state for editing or clearing.
- **FR-010**: The old top Scope, Workflow Type, Status, Entry, and Repository controls MUST be absent after column-filter parity.
- **FR-011**: Non-goals from the Tasks List design MUST remain excluded from the final rollout, including spreadsheet editing, pivot tables, multi-column sort, raw Temporal query authoring, direct browser calls to Temporal, saved views, pagination replacement, Live updates removal, and ordinary system workflow browsing.
- **FR-012**: Regression tests MUST cover loading, API error, empty first-page, empty later-page, facet-failure, invalid-filter, old-control absence, and non-goal safety behavior before rollout is considered complete.
- **FR-013**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `MM-592` and this canonical Jira preset brief.

## Source Design Requirements

| ID | Source | Requirement Summary | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-006 | `docs/UI/TasksListPage.md` section 5.8 | Current loading, error, empty, pagination, page summary, and page-size behavior must remain recoverable after the rollout. | In scope | FR-001 through FR-006 |
| DESIGN-REQ-024 | `docs/UI/TasksListPage.md` section 17 | Empty states, facet failures, list validation failures, and unsupported old URL combinations must provide recoverable user-visible paths. | In scope | FR-003, FR-004, FR-007, FR-008, FR-009 |
| DESIGN-REQ-026 | `docs/UI/TasksListPage.md` section 19 | Regression coverage must prove final Tasks List behaviors, including old-control absence and recovery states, before rollout is complete. | In scope | FR-010, FR-012 |
| DESIGN-REQ-027 | `docs/UI/TasksListPage.md` section 20 | The final rollout must not introduce non-goals such as spreadsheet editing, raw Temporal query authoring, direct Temporal browser calls, saved views, pagination replacement, Live updates removal, or system workflow browsing. | In scope | FR-011 |
| DESIGN-REQ-028 | `docs/UI/TasksListPage.md` section 21 | The recommended sequence requires removing old top filter controls only after parity tests pass and preserving runtime source requirements in feature-local artifacts rather than canonical migration notes. | In scope | FR-010, FR-012, FR-013 |

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Automated UI tests verify `Loading tasks...` appears while the list request is pending.
- **SC-002**: Automated UI tests verify list API errors render a visible sanitized error notice.
- **SC-003**: Automated UI tests verify an empty first page with active filters shows `No tasks found for the current filters.` and an enabled `Clear filters` recovery action.
- **SC-004**: Automated UI tests verify an empty later page keeps the previous-page button enabled.
- **SC-005**: Automated UI tests verify facet failures show an inline fallback or current-page-values notice while table rows remain usable.
- **SC-006**: Automated UI tests or API boundary tests verify invalid filter parameters produce structured errors while preserving recovery through editing or Clear filters.
- **SC-007**: Automated UI tests verify the old top Scope, Workflow Type, Status, Entry, and Repository controls remain absent and non-goal workflow-kind browsing controls are unavailable.
- **SC-008**: Traceability review confirms `MM-592`, the canonical Jira preset brief, and DESIGN-REQ-006, DESIGN-REQ-024, DESIGN-REQ-026, DESIGN-REQ-027, and DESIGN-REQ-028 remain preserved in MoonSpec artifacts and verification output.
