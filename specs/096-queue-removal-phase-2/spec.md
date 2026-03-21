# Feature Specification: Collapse Dashboard to Single Source

**Feature Branch**: `097-queue-removal-phase-2`  
**Created**: 2026-03-21  
**Status**: Draft  
**Input**: User description: "Implement Phase 2 of Single Substrate Migration"

## Source Document Requirements

- **DOC-REQ-001**: Remove `sources.queue` from `build_runtime_config()` in `task_dashboard_view_model.py`. (Source: SingleSubstrateMigration.md Phase 2.1)
- **DOC-REQ-002**: Remove `sources.manifests` queue-backed endpoint block from `task_dashboard_view_model.py` so manifests use Temporal source. (Source: SingleSubstrateMigration.md Phase 2.2)
- **DOC-REQ-003**: Remove `queue` and `orchestrator` from `_STATUS_MAPS` returning only `proposals` and `temporal`. (Source: SingleSubstrateMigration.md Phase 2.3)
- **DOC-REQ-004**: Simplify `normalize_status()` with a single mapping for Temporal states. (Source: SingleSubstrateMigration.md Phase 2.4)
- **DOC-REQ-005**: Remove orchestrator route matching, form validation stubs, priority normalization, and UI state branches in `dashboard.js`. (Source: SingleSubstrateMigration.md Phase 2.5)
- **DOC-REQ-006**: Remove queue source fetcher/renderer code and point all task list/detail fetching at Temporal endpoints in `dashboard.js`. (Source: SingleSubstrateMigration.md Phase 2.6)
- **DOC-REQ-007**: Remove `source` filter from compatibility APIs or deprecate the parameter. (Source: SingleSubstrateMigration.md Phase 2.7)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Viewing Tasks on Dashboard (Priority: P1)

Users view their tasks on the Mission Control dashboard and see only Temporal-backed tasks, without any legacy orchestrator or queue fetching logic running.

**Why this priority**: Core user flow for the dashboard mapping to Phase 2 goals of eliminating the multiple fetching layers.

**Independent Test**: Can be fully tested by loading the dashboard UI and verifying tasks populate via Temporal endpoints without console errors or layout breaks.

**Acceptance Scenarios**:

1. **Given** a user navigates to the dashboard, **When** the page loads, **Then** all task data is fetched exclusively from Temporal endpoints.
2. **Given** a user filters tasks, **When** they apply the filter, **Then** it filters against the unified Temporal task list without any queue compatibility logic.
3. **Given** a user views task details, **When** the modal displays, **Then** it uses only the Temporal schema.

### Edge Cases

- What happens when a user follows an old link explicitly specifying `?source=queue` or `/tasks/orchestrator/...` dashboard paths? The user should be redirected or gracefully shown the default dashboard.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST configure the frontend runtime to only include Temporal and Proposals as execution sources, entirely omitting `queue` and `orchestrator`. (Maps to DOC-REQ-001, DOC-REQ-002, DOC-REQ-003)
- **FR-002**: System MUST normalize task statuses using only Temporal state definitions across all python routers. (Maps to DOC-REQ-004)
- **FR-003**: Dashboard UI Javascript MUST NOT contain logic specifically targeting orchestrator routes, submission, or validation. (Maps to DOC-REQ-005)
- **FR-004**: Dashboard UI Javascript MUST fetch and render all task lists and task details from Temporal JSON endpoints only. (Maps to DOC-REQ-006)
- **FR-005**: Compatibility layer internal APIs MUST ignore or deprecate the `source` filter, defaulting behavior to Temporal-only retrieval. (Maps to DOC-REQ-007)

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of dashboard executions load via Temporal endpoints without reliance on queue/orchestrator.
- **SC-002**: Total dashboard JavaScript payload size decreases due to the removal of legacy fetcher and renderer methods.
- **SC-003**: Zero UI errors occur during task submission, rendering, and filtering related to state/source mismatches.
