# Feature Specification: Mission Control Layout and Table Composition Patterns

**Feature Branch**: `run-jira-orchestrate-for-mm-426-standard-7f2da784`  
**Created**: 2026-04-21  
**Status**: Draft  
**Input**: Jira Orchestrate for MM-426. Source story: STORY-003. Source summary: "Standardize Mission Control layout and table composition patterns." Source Jira issue: unknown. Original brief reference: not provided.

## Original Jira Preset Brief

Jira issue: MM-426

Source story: STORY-003. Source summary: Standardize Mission Control layout and table composition patterns. Source Jira issue: unknown. Original brief reference: not provided.

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve this Jira issue reference in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

## Classification

Single-story runtime feature request. The brief contains one independently testable UI composition outcome: Mission Control list and table surfaces must expose a consistent control-deck plus data-slab structure without changing task-list behavior.

## User Story - Layout and Table Composition

**Summary**: As a Mission Control operator, I want task-list controls and dense table data to use a consistent composition pattern so I can scan filters, utilities, and results without visual ambiguity.

**Goal**: Mission Control task-list layout separates page controls from dense data, keeps table utilities attached to the result slab, and standardizes shared table styling so dense tables use the same matte, sticky-header posture.

**Independent Test**: Render the task list with Temporal rows. The story passes when filters and live-update utilities live in a control deck, results/pagination/table live in a separate data slab, active filters are visible and clearable, desktop table headers are sticky inside a scrollable slab, and existing task-list request, sorting, pagination, and mobile-card behavior still pass.

**Acceptance Scenarios**:

1. **Given** the task list renders with default filters, **when** the page is inspected, **then** filter controls and live-update utilities are grouped in a control deck distinct from the result table slab.
2. **Given** task rows are available, **when** results render, **then** pagination, page-size controls, desktop table, and mobile card list are contained in the data slab as one connected table system.
3. **Given** filters are applied, **when** the control deck updates, **then** active filters appear as compact chips and a clear action resets filters without changing task fetching semantics.
4. **Given** the desktop table scrolls, **when** the user scans rows, **then** table headers remain sticky and columns retain constrained comparison widths.
5. **Given** shared DataTable consumers render dense tables, **when** their markup is inspected, **then** they use the shared data-table slab and table classes instead of standalone unthemed Tailwind table shells.

### Edge Cases

- Empty paginated pages must keep previous-page navigation available.
- Long workflow IDs and repository names must wrap inside constrained table cells instead of expanding the page.
- Narrow/mobile layouts must keep card actions and filter controls within viewport width.
- Disabled list mode must keep controls disabled and surface the existing configuration error.

## Assumptions

- "Mission Control layout and table composition patterns" refers to the desired-state control deck, data slab, sticky table header, and table-first desktop guidance in `docs/UI/MissionControlDesignSystem.md`.
- The task list is the primary implementation target because it is the canonical table-first operational route.
- Shared `DataTable` styling should be standardized for existing DataTable consumers, but route-specific page redesigns for manifests and schedules are outside this story.
- No backend persistence, API contract, route ownership, or Temporal behavior change is required.

## Requirements *(mandatory)*

- **FR-001**: The task list MUST expose a named control deck for page title, primary filters, active filter state, and live-update utilities.
- **FR-002**: The task list MUST expose a named data slab for result summary, page-size controls, pagination, desktop table, and mobile cards.
- **FR-003**: Active filters MUST be visible as compact chips and MUST be clearable through one control-deck action.
- **FR-004**: Desktop task-list table headers MUST be sticky inside a scrollable matte data slab.
- **FR-005**: Task-list table columns and long identifiers MUST remain constrained and wrap safely without expanding the viewport.
- **FR-006**: Shared DataTable markup MUST use Mission Control table slab/table classes rather than unthemed standalone utility-class shells.
- **FR-007**: Existing task-list behavior for requests, sorting, pagination, dependency summaries, runtime labels, and mobile cards MUST remain unchanged.
- **FR-008**: Automated verification MUST cover the new composition structure, active filter chips, sticky table header posture, and existing task-list behavior.
- **FR-009**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve MM-426 and the original supplied brief.

## Key Entities

- **Control Deck**: The top task-list surface containing page title, primary filters, active-filter state, live-update control, and clear action.
- **Data Slab**: The dense result surface containing result summary, page-size selector, pagination, table, and mobile cards.
- **Shared Data Table**: Reusable table component markup and classes that express the Mission Control matte data slab pattern.

## Success Criteria *(mandatory)*

- **SC-001**: Task-list UI tests confirm the control deck and data slab classes are present and contain the expected controls.
- **SC-002**: Task-list UI tests confirm active filters appear as chips and clear back to default filter values.
- **SC-003**: CSS/computed-style verification confirms the table wrapper scrolls and desktop table headers use sticky positioning.
- **SC-004**: Existing task-list UI tests continue to pass.
- **SC-005**: Traceability verification confirms MM-426 and the supplied source summary are preserved in MoonSpec artifacts and final evidence.
