# Feature Specification: Tasks List Canonical Route and Shell

**Feature Branch**: `298-tasks-list-canonical-route-shell`
**Created**: 2026-05-05
**Status**: Draft
**Input**:

```text
# MM-585 MoonSpec Orchestration Input

## Source

- Jira issue: MM-585
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Tasks List canonical route and shell
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Implement Jira issue MM-585: Tasks List canonical route and shell.

As a MoonMind operator, I want `/tasks/list` to render the canonical Tasks List shell with redirected legacy routes and server-provided runtime configuration so I always land on the supported task list experience.

## Source Reference

- Jira issue: MM-585
- Issue type: Story
- Summary: Tasks List canonical route and shell
- Status: In Progress
- Source document: docs/UI/TasksListPage.md
- Source title: Tasks List Page
- Source sections:
  - 1. Purpose
  - 2. Related docs and implementation surfaces
  - 3. Route and hosting model
  - 5.1 Page shell
- Coverage IDs:
  - DESIGN-REQ-001
  - DESIGN-REQ-002
  - DESIGN-REQ-003
  - DESIGN-REQ-004
  - DESIGN-REQ-006

## Acceptance Criteria

- Given a request to `/tasks/list`, the response hosts the Mission Control React page with page key `tasks-list`.
- Given requests to `/tasks` or `/tasks/tasks-list`, the server redirects to `/tasks/list`.
- The rendered page contains one header control deck and one data slab using the wide data-panel layout.
- Live updates state, polling copy, feature-disabled notice, and page-size/pagination surfaces remain available.
- The frontend uses boot payload dashboard configuration and MoonMind API routes only.

## Requirements

- Preserve current shell behavior while making `/tasks/list` canonical.
- Keep task-list route hosting inside FastAPI and the shared React/Vite frontend.
- Keep current loading, polling, disabled, and data-panel surfaces available for later stories.

## Traceability Requirement

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve Jira issue key MM-585 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.
```

<!-- Moon Spec specs contain exactly one independently testable user story. Use /speckit.breakdown for technical designs that contain multiple stories. -->

## User Story - Canonical Tasks List Shell

**Summary**: As a MoonMind operator, I want `/tasks/list` to be the canonical Tasks List route with legacy redirects and a server-configured Mission Control shell so I always land on the supported task list experience.

**Goal**: Operators can open the canonical Tasks List route, reach it from legacy task-list paths through redirects, and see the supported shell composition with server-provided runtime configuration and MoonMind API-only data loading.

**Independent Test**: Can be fully tested by requesting `/tasks/list`, `/tasks`, and `/tasks/tasks-list`; inspecting the server-rendered boot payload and layout metadata; and rendering the Tasks List React entrypoint to confirm one control deck, one data slab, live update controls, polling/disabled state, page-size/pagination surfaces, and MoonMind API requests.

**Acceptance Scenarios**:

1. **Given** an authenticated operator requests `/tasks/list`, **When** the response is rendered, **Then** it hosts the Mission Control React page with page key `tasks-list` and server-provided dashboard configuration.
2. **Given** an authenticated operator requests `/tasks` or `/tasks/tasks-list`, **When** the request completes without following redirects, **Then** the server returns a redirect to `/tasks/list`.
3. **Given** the Tasks List React entrypoint renders with list data, **When** the page is inspected, **Then** exactly one header control deck and one wide data slab are present.
4. **Given** list runtime configuration is enabled or disabled, **When** the Tasks List page renders, **Then** live updates, polling copy, feature-disabled notice, page-size selection, and pagination surfaces remain available according to the configuration and result state.
5. **Given** the Tasks List page fetches data, **When** browser requests are inspected, **Then** the frontend uses the boot payload API base and MoonMind API routes only.

### Edge Cases

- `/tasks/tasks-list/` with a trailing slash must not be treated as a task detail page.
- The list route must retain the wide data-panel layout while other non-wide Mission Control routes remain unchanged.
- Disabled list configuration must preserve a recoverable shell instead of hiding the page.
- Legacy route redirects must not embed hardcoded external service URLs.

## Assumptions

- Existing authentication dependencies remain the authorization boundary for dashboard routes.
- This story is limited to the current page shell and route-hosting model from `docs/UI/TasksListPage.md` sections 1, 2, 3, and 5.1; later column-filter redesign requirements in the same document are out of scope.

## Source Design Requirements

- **DESIGN-REQ-001**: Source `docs/UI/TasksListPage.md` sections 1 and 3. `/tasks/list` must be the canonical Tasks List route for the task-oriented Mission Control list experience. Scope: in scope. Maps to FR-001.
- **DESIGN-REQ-002**: Source `docs/UI/TasksListPage.md` section 3. `/tasks` and `/tasks/tasks-list` must redirect to `/tasks/list`. Scope: in scope. Maps to FR-002.
- **DESIGN-REQ-003**: Source `docs/UI/TasksListPage.md` section 3. The Tasks List page must be server-hosted by FastAPI and rendered by the shared Mission Control React/Vite frontend. Scope: in scope. Maps to FR-003.
- **DESIGN-REQ-004**: Source `docs/UI/TasksListPage.md` section 3. The server must render the `tasks-list` page key, wide data-panel layout, and runtime dashboard configuration through the boot payload. Scope: in scope. Maps to FR-004 and FR-005.
- **DESIGN-REQ-006**: Source `docs/UI/TasksListPage.md` sections 3 and 5.1. The browser must call MoonMind APIs only, while the shell keeps live updates, polling copy, disabled notice, page-size, pagination, control deck, and data slab surfaces available. Scope: in scope. Maps to FR-006 through FR-010.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST serve `/tasks/list` as the canonical Tasks List route for authenticated operators.
- **FR-002**: System MUST redirect `/tasks` and `/tasks/tasks-list` to `/tasks/list`.
- **FR-003**: System MUST host the Tasks List page through FastAPI and the shared Mission Control React/Vite shell.
- **FR-004**: System MUST render the boot payload with page key `tasks-list` for `/tasks/list`.
- **FR-005**: System MUST pass server-generated dashboard configuration and wide data-panel layout metadata to the `/tasks/list` shell.
- **FR-006**: System MUST render exactly one Tasks List control deck and one data slab for the current page shell.
- **FR-007**: System MUST preserve live updates controls and polling status copy in the Tasks List shell.
- **FR-008**: System MUST preserve the feature-disabled notice when Temporal list configuration disables the list.
- **FR-009**: System MUST preserve page-size and pagination surfaces in the Tasks List shell.
- **FR-010**: The Tasks List frontend MUST use the boot payload API base and MoonMind API routes for data loading, without direct browser calls to Temporal, GitHub, Jira, object storage, or runtime providers.
- **FR-011**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `MM-585` and this canonical Jira preset brief for traceability.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Route tests prove `/tasks/list` renders a Mission Control React shell whose boot payload includes page key `tasks-list`, dashboard configuration, and wide data-panel layout metadata.
- **SC-002**: Route tests prove `/tasks` and `/tasks/tasks-list` return redirects to `/tasks/list`.
- **SC-003**: UI render tests prove exactly one control deck and one data slab are present.
- **SC-004**: UI render tests prove live updates, polling copy, disabled notice, page-size, and pagination surfaces remain available.
- **SC-005**: UI data-loading tests prove browser data requests use the boot payload API base and MoonMind `/api/executions` route.
- **SC-006**: Final verification confirms `MM-585`, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, and DESIGN-REQ-006 are preserved and covered by implementation evidence.
