# Feature Specification: Show Recent Manifest Runs

**Feature Branch**: `217-show-recent-manifest-runs`
**Created**: 2026-04-21
**Status**: Draft
**Input**: User description: the Jira preset brief for MM-421, preserved verbatim below.

```text
Jira issue: MM-421 from MM project
Summary: Show recent manifest runs below the run form
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-421 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-421: Show recent manifest runs below the run form

Source Reference
- Source Document: docs/UI/ManifestsPage.md
- Source Title: Manifests Page
- Source Sections:
  - Recent Runs section
  - Data source
  - Table purpose
  - Recommended columns
  - Manifest-specific status detail
  - Filters
  - Empty state
  - Responsive behavior
  - Accessibility
- Coverage IDs:
  - DESIGN-REQ-002
  - DESIGN-REQ-010
  - DESIGN-REQ-011
  - DESIGN-REQ-012
  - DESIGN-REQ-013
  - DESIGN-REQ-014
  - DESIGN-REQ-017

User Story
As a dashboard user, I want recent manifest runs visible below the Run Manifest card so I can immediately check start state, current stage, result, timing, and details for manifest executions.

Acceptance Criteria
- Given /tasks/manifests loads, then Recent Runs requests /api/executions?entry=manifest&limit=200.
- Given manifest runs exist, then the history surface shows run ID/details link, manifest label, action, status, current stage when available, started time, duration when available, and supported row actions.
- Given a run is active and stage data is available, then the status display includes manifest-specific stage detail such as Running · fetch.
- Given status, manifest name, or free-text filters are used, then the visible run list updates without a heavy filter-builder flow.
- Given no manifest runs exist, then the empty state says no manifest runs exist and points users to run a registry manifest or submit inline YAML above.
- Given the viewport is narrow, then recent runs remain readable as compact cards or stacked rows with clear action labels.
- Given row actions are icon-based in implementation, then they include accessible names.

Requirements
- Recent Runs must appear below the Run Manifest card on the same page.
- The history request must use the existing manifest execution endpoint for phase 1.
- The history view must help answer whether the run started, whether it is still running, what stage it is in, whether it succeeded or failed, and how to open details/logs.
- Filters must remain lightweight and bounded to status, manifest name, search, and optional action support.
- The implementation must not require a backend redesign or new manifest-centric history API for phase 1.

Implementation Notes
- This story is about showing recent manifest execution history below the existing Run Manifest card, not replacing the existing manifest execution contract.
- Use the existing manifest execution endpoint for the initial history request: /api/executions?entry=manifest&limit=200.
- The UI should make recent runs scannable by exposing run identity/details, manifest label, action, status, current stage, timing, and available row actions.
- Active runs should show manifest-specific stage detail when stage data exists.
- Filters should remain lightweight and focused on status, manifest name, search, and optional action support.
- Preserve MM-421 in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
```

## User Story - Monitor Recent Manifest Runs

**Summary**: As a dashboard user, I want recent manifest runs visible below the Run Manifest card so I can immediately check start state, current stage, result, timing, and details for manifest executions.

**Goal**: Users can open `/tasks/manifests`, scan recent manifest executions directly below the run form, filter the visible history lightly, and open run details without leaving the unified manifest workflow context.

**Independent Test**: Open `/tasks/manifests` with manifest execution history returned by `/api/executions?entry=manifest&limit=200`, verify the Run Manifest form appears before Recent Runs, verify the Recent Runs surface shows run identity/detail link, manifest label, action, status with current stage when present, started time, duration, and row actions, then filter by status, manifest name, and search text and verify the visible list updates.

**Acceptance Scenarios**:

1. **Given** `/tasks/manifests` loads, **When** recent runs are requested, **Then** the page calls `/api/executions?entry=manifest&limit=200` and renders Recent Runs below the Run Manifest card.
2. **Given** manifest runs exist, **When** the Recent Runs surface renders, **Then** each row exposes a run ID/details link, manifest label, action, status, current stage when available, started time, duration when available, and a supported View details action.
3. **Given** a manifest run is active and stage data is available, **When** the status is displayed, **Then** the page includes manifest-specific stage detail such as `Running · fetch`.
4. **Given** a user filters by status, manifest name, or free-text search, **When** filter values change, **Then** the visible run list updates without requiring a complex filter-builder flow.
5. **Given** no manifest runs match the current data or filters, **When** the Recent Runs surface renders, **Then** the empty state says no manifest runs exist and points users to run a registry manifest or submit inline YAML above.
6. **Given** row actions are link-based or icon-based, **When** keyboard or assistive technology users inspect them, **Then** each action has an accessible name.

### Edge Cases

- Runs without manifest labels fall back to available title, source label, or run ID.
- Runs without explicit action values display a neutral placeholder instead of hiding the column.
- Running statuses with no stage display status only.
- Invalid or absent timestamps and durations display a neutral placeholder.
- Filters that remove all rows show the same manifest-specific empty state.

## Assumptions

- The existing `/api/executions?entry=manifest&limit=200` response can carry optional execution fields such as title, status, current stage, created or started time, duration, and detail links; the UI should tolerate absent optional fields.
- Phase 1 uses the existing execution history endpoint and does not require a backend redesign or new manifest-specific history API.

## Source Design Requirements

The preserved Jira brief lists source coverage IDs `DESIGN-REQ-002`, `DESIGN-REQ-010`, `DESIGN-REQ-011`, `DESIGN-REQ-012`, `DESIGN-REQ-013`, `DESIGN-REQ-014`, and `DESIGN-REQ-017`. The mappings below are this single-story spec's extracted requirements from `docs/UI/ManifestsPage.md`; they keep the story traceable without requiring unrelated source sections to become in-scope work.

- **DESIGN-REQ-001** (`docs/UI/ManifestsPage.md`, Page structure): Recent Runs appears below the Run Manifest card in the unified Manifests page. Scope: in scope. Maps to FR-001.
- **DESIGN-REQ-002** (`docs/UI/ManifestsPage.md`, Recent Runs data source): Recent Runs uses `/api/executions?entry=manifest&limit=200`. Scope: in scope. Maps to FR-002.
- **DESIGN-REQ-003** (`docs/UI/ManifestsPage.md`, Table purpose): The history surface helps users identify whether a run started, whether it is running, what stage it is in, whether it succeeded or failed, and how to open details or logs. Scope: in scope. Maps to FR-003, FR-004, FR-005.
- **DESIGN-REQ-004** (`docs/UI/ManifestsPage.md`, Recommended columns): Recent Runs exposes run ID, manifest, action, status, stage, started time, duration, and actions when those values are available. Scope: in scope. Maps to FR-003, FR-004, FR-005.
- **DESIGN-REQ-005** (`docs/UI/ManifestsPage.md`, Manifest-specific status detail): Running manifest jobs show current stage detail inline when available. Scope: in scope. Maps to FR-004.
- **DESIGN-REQ-006** (`docs/UI/ManifestsPage.md`, Filters): Filters remain lightweight and bounded to status, manifest name, search, and optional action support. Scope: in scope. Maps to FR-006.
- **DESIGN-REQ-007** (`docs/UI/ManifestsPage.md`, Empty state): Empty Recent Runs states tell users there are no manifest runs and point them to run a registry manifest or submit inline YAML above. Scope: in scope. Maps to FR-007.
- **DESIGN-REQ-008** (`docs/UI/ManifestsPage.md`, Responsive behavior): Recent runs remain readable on narrow viewports as compact cards or stacked rows. Scope: in scope. Maps to FR-008.
- **DESIGN-REQ-009** (`docs/UI/ManifestsPage.md`, Accessibility): Row actions and filter controls have accessible names or labels. Scope: in scope. Maps to FR-009.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST show Recent Runs below the Run Manifest form on `/tasks/manifests`.
- **FR-002**: The system MUST request recent manifest runs from `/api/executions?entry=manifest&limit=200`.
- **FR-003**: The Recent Runs surface MUST expose a run ID/details link, manifest label, action, status, started time, duration, and a View details action for each manifest run when source data is available.
- **FR-004**: The Recent Runs surface MUST include current manifest stage detail inline with status for active runs when stage data is available.
- **FR-005**: The Recent Runs surface MUST tolerate missing optional manifest label, action, stage, timestamp, duration, or detail-link fields with clear fallback values.
- **FR-006**: The system MUST provide lightweight client-side filters for status, manifest name, and free-text search that update the visible run list.
- **FR-007**: The Recent Runs empty state MUST say no manifest runs exist and direct users to run a registry manifest or submit inline YAML above.
- **FR-008**: The Recent Runs surface MUST remain readable on narrow viewports without hiding run identity, status, or the detail action.
- **FR-009**: Recent Runs filters and row actions MUST expose accessible labels or names.
- **FR-010**: The implementation MUST preserve Jira issue key MM-421 in Moon Spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Frontend tests show the page requests `/api/executions?entry=manifest&limit=200` and renders Recent Runs below Run Manifest.
- **SC-002**: Frontend tests show manifest run rows expose run detail link, manifest label, action, status with stage detail, started time, duration, and View details action.
- **SC-003**: Frontend tests show status, manifest name, and search filters update the visible list and preserve a manifest-specific empty state.
- **SC-004**: Accessibility-oriented tests can locate filters and row actions by accessible label or role.
- **SC-005**: The final verification report traces implementation evidence back to MM-421 and all in-scope DESIGN-REQ mappings.
