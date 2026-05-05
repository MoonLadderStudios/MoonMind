# Feature Specification: Canonical Task Run List Route

**Feature Branch**: `298-canonical-task-run-list`  
**Created**: 2026-05-05  
**Status**: Draft  
**Input**: User description: """
Use the Jira preset brief for THOR-370 as the canonical Moon Spec orchestration input.

Additional constraints:


Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# THOR-370 MoonSpec Orchestration Input

## Source

- Jira issue: THOR-370
- Jira project key: THOR
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Canonical task-run list route with fail-safe workflow visibility
- Trusted fetch tool: `jira.get_issue`
- Canonical source: synthesized from trusted Jira issue fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: THOR-370 from THOR project
Summary: Canonical task-run list route with fail-safe workflow visibility
Issue type: Story
Current Jira status: In Progress
Jira project key: THOR

Source Reference
Source Document: docs/UI/TasksListPage.md
Source Title: Tasks List Page
Source Sections:
- 3. Route and hosting model
- 4. Product stance
- 7. Column set in the desired design
- 7.1 Admin diagnostics escape hatch
- 12.1 Backward compatibility
- 18. Security and privacy

Coverage IDs:
- DESIGN-REQ-002
- DESIGN-REQ-003
- DESIGN-REQ-004
- DESIGN-REQ-008
- DESIGN-REQ-015
- DESIGN-REQ-022

As a Mission Control operator, I want /tasks/list to always load the task-oriented execution list and keep broad workflow browsing out of the ordinary page so that shared links and manual URL edits cannot expose system workflow rows.

Acceptance Criteria
- /tasks/list is the canonical route and /tasks plus /tasks/tasks-list redirect to it.
- The server renders the tasks-list page key, wide data-panel layout configuration, and runtime dashboard config through the boot payload.
- The browser calls MoonMind APIs only and never calls Temporal, GitHub, Jira, object storage, or runtime providers directly.
- The normal page always requests user-visible task runs, equivalent to scope=tasks / WorkflowType=MoonMind.Run / mm_entry=run in current API terms.
- System workflows, provider-profile managers, internal monitors, maintenance workflows, and manifest-ingest workflows do not appear in the ordinary task table.
- Old scope=system, scope=all, system workflowType, and entry=manifest URLs fail safe by preserving task-run visibility, redirecting authorized admins to diagnostics, redirecting manifest links to the Manifests page, or showing a recoverable message.
- No Kind, Workflow Type, or Entry column is introduced to make broad workflow browsing available from /tasks/list.

Requirements
- Implement canonical route redirects and boot payload page-key behavior for the Tasks List page.
- Bound normal list requests and compatibility parsing to user-visible task executions.
- Route or message broad workflow and manifest compatibility URLs without leaking unauthorized rows.
- Keep broad workflow diagnostics permission-gated and separate from the normal task table.

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key THOR-370 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.
"""

## User Story - Task-Focused Execution List

**Summary**: As a Mission Control operator, I want `/tasks/list` to always open the task-focused execution list and keep broad workflow browsing out of the ordinary page.

**Goal**: Operators can rely on `/tasks/list` and its compatibility URLs to show ordinary task-run executions only, with system workflow visibility handled by an explicit diagnostics path rather than by normal list controls or manual URL edits.

**Independent Test**: Load the canonical and legacy task-list routes with ordinary and broad-workflow query parameters, then verify route behavior, page identity, visible rows, compatibility handling, and diagnostics separation without granting ordinary users access to system workflow rows.

**Acceptance Scenarios**:

1. **Given** an operator opens `/tasks/list`, **When** the page loads, **Then** the task-list page identity, wide list layout, and runtime dashboard configuration are present.
2. **Given** an operator opens `/tasks` or `/tasks/tasks-list`, **When** the request completes, **Then** the operator lands on `/tasks/list`.
3. **Given** ordinary task runs and system workflows both exist, **When** an ordinary operator views `/tasks/list`, **Then** only user-visible task runs appear.
4. **Given** an ordinary operator opens an old broad-workflow URL such as `scope=system`, `scope=all`, a system workflow type, or `entry=manifest`, **When** the page handles the URL, **Then** the ordinary task-run view remains protected, the operator is sent to the appropriate non-list surface, or a recoverable message explains why the broad workflow view is unavailable.
5. **Given** an authorized administrator opens a broad-workflow compatibility URL, **When** diagnostics access is allowed, **Then** broad workflow visibility is available only through a permission-gated diagnostics surface, not the ordinary task table.
6. **Given** the ordinary task-list table renders, **When** the operator scans available columns, **Then** no `Kind`, `Workflow Type`, or `Entry` column exposes broad workflow browsing.

**Edge Cases**:

- Legacy URLs that name manifest entries should not convert the task table into a manifest browser.
- System workflow filters supplied by manual URL edits must not reveal unauthorized rows or facet values.
- Unknown or unsupported workflow scope parameters should fail toward the ordinary task-run list or a recoverable explanation.
- Browser-side loading must not contact external provider systems directly.
- Diagnostics access must remain separate from the ordinary list even when the underlying execution source can represent broader workflow scopes.

## Requirements

- **FR-001**: The system MUST expose `/tasks/list` as the canonical Tasks List route.
- **FR-002**: The system MUST route `/tasks` and `/tasks/tasks-list` to `/tasks/list`.
- **FR-003**: The Tasks List page MUST identify itself as the task-list page and use the product layout intended for a wide execution table.
- **FR-004**: The browser-facing Tasks List experience MUST use MoonMind-owned surfaces only and MUST NOT call Temporal, GitHub, Jira, object storage, or runtime providers directly.
- **FR-005**: The ordinary Tasks List query MUST be bounded to user-visible task-run executions.
- **FR-006**: System workflows, provider-profile managers, internal monitors, maintenance workflows, and manifest-ingest workflows MUST NOT appear in the ordinary task table.
- **FR-007**: Compatibility handling for old `scope=system`, `scope=all`, system workflow type, and `entry=manifest` URLs MUST fail safe by preserving task-run visibility, routing authorized administrators to diagnostics, routing manifest views to the manifest surface, or showing a recoverable message.
- **FR-008**: Broad workflow diagnostics MUST remain permission-gated and separate from the normal Tasks List table.
- **FR-009**: The normal Tasks List table MUST NOT introduce `Kind`, `Workflow Type`, or `Entry` columns for ordinary broad workflow browsing.
- **FR-010**: URL parameters and filters MUST NOT bypass authorization or reveal hidden system workflow rows, values, or counts.
- **FR-011**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `THOR-370` and the original Jira preset brief.

## Key Entities

- **Task Run List View**: The ordinary Mission Control list surface for user-visible task executions.
- **Compatibility URL**: A legacy or manually edited Tasks List URL that may contain old scope, workflow type, entry, or repository parameters.
- **Diagnostics Surface**: A permission-gated workflow browsing surface for administrators that is separate from the ordinary task table.
- **Visible Task Run**: A task execution row that the current operator is authorized to see in the ordinary list.

## Assumptions

- Authorized administrator diagnostics may use an existing or future diagnostics route, as long as ordinary `/tasks/list` does not expose broad workflow browsing.
- Manifest-oriented compatibility URLs may route to the existing Manifests page or show a recoverable message when a direct route is unavailable.
- Existing product terminology for task-run visibility and lifecycle states remains valid.

## Source Design Requirements

| ID | Source | Requirement Summary | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-002 | `docs/UI/TasksListPage.md` lines 46-63 | `/tasks/list` is canonical, legacy task-list routes redirect to it, and the page receives task-list identity, wide layout, runtime dashboard configuration, and MoonMind-owned browser data access. | In scope | FR-001, FR-002, FR-003, FR-004 |
| DESIGN-REQ-003 | `docs/UI/TasksListPage.md` lines 67-81 | The Tasks List page is task-oriented rather than a generic workflow browser, hides system workflows from ordinary users, and keeps broad workflow browsing out of the default list. | In scope | FR-005, FR-006, FR-008, FR-009 |
| DESIGN-REQ-004 | `docs/UI/TasksListPage.md` lines 316-342 | The normal table uses task columns and excludes `Kind`, `Workflow Type`, `Entry`, raw workflow syntax, and system rows from ordinary table access. | In scope | FR-005, FR-006, FR-009 |
| DESIGN-REQ-008 | `docs/UI/TasksListPage.md` lines 344-354 | System and all-workflow browsing belongs in a permission-gated diagnostics surface and compatibility broad-workflow URLs must not widen ordinary `/tasks/list` visibility. | In scope | FR-007, FR-008, FR-010 |
| DESIGN-REQ-015 | `docs/UI/TasksListPage.md` lines 592-616 | Existing broad-scope, manifest, workflow type, state, entry, and repository URL parameters must fail safe, preserve relevant task-list meaning, redirect to the right surface, or explain unavailable broad workflow views. | In scope | FR-007, FR-010 |
| DESIGN-REQ-022 | `docs/UI/TasksListPage.md` lines 785-795 | Browser access, facet values, filter parameters, URL state, labels, and filter sizes must preserve authorization, privacy, and safe rendering. | In scope | FR-004, FR-010 |

## Success Criteria

- **SC-001**: 100% of canonical and legacy task-list route checks land on `/tasks/list` or render the task-list page identity as appropriate.
- **SC-002**: In test data containing at least one ordinary task run and at least one system workflow, ordinary `/tasks/list` results include zero system workflow rows.
- **SC-003**: At least four broad-workflow compatibility URL cases are verified to fail safe without exposing system workflow rows to ordinary users.
- **SC-004**: The ordinary task table exposes zero `Kind`, `Workflow Type`, or `Entry` columns.
- **SC-005**: Browser-visible task-list behavior is verified to use MoonMind-owned surfaces only for list data.
- **SC-006**: Verification evidence preserves `THOR-370`, the original Jira preset brief, and the mapped source design requirement IDs.
