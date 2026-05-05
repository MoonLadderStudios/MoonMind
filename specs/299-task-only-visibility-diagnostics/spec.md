# Feature Specification: Task-only Visibility and Diagnostics Boundary

**Feature Branch**: `299-task-only-visibility-diagnostics`
**Created**: 2026-05-05
**Status**: Draft
**Input**:

```text
# MM-586 MoonSpec Orchestration Input

## Source

- Jira issue: MM-586
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Task-only visibility and diagnostics boundary
- Trusted fetch tool: `jira.get_issue`
- Trusted response artifact: `artifacts/moonspec-inputs/MM-586-trusted-jira-get-issue-summary.json`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira issue fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.
- Labels: moonmind-workflow-mm-af73ac39-5c56-460e-bd77-712adac541f3

## Canonical MoonSpec Feature Request

Jira issue: MM-586 from MM project
Summary: Task-only visibility and diagnostics boundary
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-586 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-586: Task-only visibility and diagnostics boundary

Source Reference
Source Document: docs/UI/TasksListPage.md
Source Title: Tasks List Page
Source Sections:
- 4. Product stance
- 7. Column set in the desired design
- 7.1 Admin diagnostics escape hatch
- 12.1 Backward compatibility
- 18. Security and privacy
Coverage IDs:
- DESIGN-REQ-005
- DESIGN-REQ-008
- DESIGN-REQ-009
- DESIGN-REQ-017
- DESIGN-REQ-025

As an ordinary Tasks List user, I want the page to always show user-visible task runs and never become a workflow-kind browser so system workflows remain confined to an explicit admin diagnostics surface.

Acceptance Criteria
- The normal list request is always bounded to task-run visibility regardless of editable URL parameters.
- The default table exposes no Kind, Workflow Type, or Entry column.
- System and all workflow scopes are ignored safely, redirected to diagnostics when authorized, or shown in a recoverable message without revealing rows.
- Manifest ingest URLs do not add Workflow Type or Entry columns to the task table.
- Diagnostics access, if linked, is permission-gated and visually separate from `/tasks/list`.

Requirements
- Enforce task-oriented visibility at the backend authorization/query boundary.
- Remove the ordinary workflow-kind browsing UX from Tasks List.
- Fail safe for old URLs without losing task-list availability.

## Relevant Implementation Notes

- Source design path: `docs/UI/TasksListPage.md`.
- Section 4 Product stance: the Tasks List page is an operator scanning surface for ordinary user-created task executions, not a generic Temporal namespace browser; system, provider-profile, monitor, maintenance, and manifest-ingest workflows belong outside the normal task table.
- Section 7 Column set in the desired design: the default table includes task fields such as ID, Runtime, Skill, Repository, Status, Title, Scheduled, Created, Finished, and optional Integration, while excluding `Kind`, `Workflow Type`, and `Entry`; the default query remains the task-run list equivalent to `scope=tasks`, `WorkflowType=MoonMind.Run`, and `mm_entry=run`.
- Section 7.1 Admin diagnostics escape hatch: system and all-workflow browsing may exist only in an explicit permission-gated diagnostics surface; ordinary users must not widen `/tasks/list` into system workflow visibility by editing URL parameters.
- Section 12.1 Backward compatibility: old `scope`, `workflowType`, `entry`, `state`, and `repo` URL parameters must fail safe, preserve valid task-list meanings where appropriate, redirect to a better page when authorized, or show recoverable messages without revealing system workflows.
- Section 18 Security and privacy: browser requests go through MoonMind APIs, facet values share list authorization and owner scoping, hidden or unauthorized values must not appear in facet data, filter parameters must not bypass backend authorization, URL state must not contain secrets, labels render as text, and invalid filters should return structured validation errors.

## MoonSpec Classification Input

Classify this as a single-story runtime feature request for the Tasks List page: enforce task-only visibility and a separate permission-gated diagnostics boundary while preserving MM-586 traceability and the referenced Tasks List design requirements.

## Orchestration Constraints

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.
```

<!-- Moon Spec specs contain exactly one independently testable user story. Use /speckit.breakdown for technical designs that contain multiple stories. -->

## User Story - Task-only Tasks List Visibility

**Summary**: As an ordinary Tasks List user, I want `/tasks/list` to show only user-visible task runs and not become a workflow-kind browser.

**Goal**: Operators can trust the normal Tasks List page to remain task-oriented even when old or edited URLs request system, all-workflow, manifest, workflow-type, or entry browsing.

**Independent Test**: Render `/tasks/list` with default and legacy workflow-scope URLs, inspect the browser request and visible controls, and query the execution list boundary to confirm only task-run visibility is requested or returned while recoverable messaging appears for ignored workflow-scope URL state.

**Acceptance Scenarios**:

1. **Given** an ordinary user opens `/tasks/list`, **When** the list loads, **Then** the data request is bounded to task runs and no system/all workflow rows can be requested from the normal page controls.
2. **Given** an old URL contains `scope=system`, `scope=all`, a system `workflowType`, or `entry=manifest`, **When** `/tasks/list` loads, **Then** the page fails safe by showing task runs only and providing recoverable notice without revealing system or manifest rows.
3. **Given** an old URL contains task-compatible filters such as `state=<value>` or `repo=<value>`, **When** `/tasks/list` loads, **Then** those task-list filters remain available without reintroducing workflow-kind browsing.
4. **Given** the default desktop table renders, **When** column headers are inspected, **Then** no `Kind`, `Workflow Type`, or `Entry` column is present.
5. **Given** diagnostics access is not part of `/tasks/list`, **When** a user interacts with the normal page, **Then** system/all workflow browsing controls are absent from the task table surface.

### Edge Cases

- `scope=user` and `workflowType=MoonMind.ManifestIngest` are treated as workflow browsing state and must not expose manifest rows in `/tasks/list`.
- `workflowType=MoonMind.Run` and `entry=run` are task-compatible legacy parameters, but the page should normalize them away because task-run visibility is the default.
- Clearing filters must not restore ignored workflow-scope URL parameters.
- Unauthorized or hidden workflow categories must not appear in filter chips, table columns, facet-style values, or request URLs.

## Assumptions

- A separate diagnostics route is not part of this story; old workflow-scope URLs fail safe on `/tasks/list` with task-run visibility and recoverable messaging.
- Existing authenticated user scoping remains the owner boundary for ordinary task rows.
- This story preserves the current Status and Repository controls until later column-filter stories replace them.

## Source Design Requirements

- **DESIGN-REQ-005**: Source `docs/UI/TasksListPage.md` section 4. The normal Tasks List page must be task-oriented and must not act as a generic Temporal namespace or workflow-kind browser. Scope: in scope. Maps to FR-001, FR-002, FR-005, and FR-006.
- **DESIGN-REQ-008**: Source `docs/UI/TasksListPage.md` section 7. The normal table must not include `Kind`, `Workflow Type`, or `Entry` columns and must default to task-run list semantics. Scope: in scope. Maps to FR-001 and FR-004.
- **DESIGN-REQ-009**: Source `docs/UI/TasksListPage.md` section 7. System workflow rows and manifest ingest rows must not appear in the normal task table through filters or old URL parameters. Scope: in scope. Maps to FR-002, FR-003, and FR-007.
- **DESIGN-REQ-017**: Source `docs/UI/TasksListPage.md` section 12.1. Existing workflow-scope URL parameters must fail safe, preserve task-compatible filters where possible, or show recoverable messaging without exposing broader workflow scopes. Scope: in scope. Maps to FR-003 and FR-008.
- **DESIGN-REQ-025**: Source `docs/UI/TasksListPage.md` section 18. Filter parameters must not bypass backend authorization, unauthorized values must not appear in list/facet outputs, URL filter state must not contain secrets, and labels must render as text. Scope: in scope. Maps to FR-007, FR-009, and FR-010.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The normal Tasks List data request MUST be bounded to task-run visibility equivalent to task scope, `MoonMind.Run`, and run entries.
- **FR-002**: The Tasks List UI MUST NOT expose ordinary controls for system, all-workflow, workflow-type, or entry browsing.
- **FR-003**: The Tasks List UI MUST ignore or normalize old workflow-scope URL parameters without revealing system, all-workflow, or manifest rows.
- **FR-004**: The default Tasks List table MUST NOT expose `Kind`, `Workflow Type`, or `Entry` columns.
- **FR-005**: The Tasks List UI MUST preserve task-compatible Status and Repository filtering while keeping active filter chips task-oriented.
- **FR-006**: The execution-list request boundary used by the normal Tasks List MUST fail safe to task-run visibility when scope, workflow type, or entry parameters would widen the result set.
- **FR-007**: Non-admin ordinary users MUST NOT be able to use normal Tasks List query parameters to list system workflows, manifest ingest workflows, or all workflow scopes.
- **FR-008**: Old URLs that contain unsupported workflow-scope state MUST produce a recoverable user-visible notice or equivalent safe handling while preserving task-list availability.
- **FR-009**: URL state emitted by the Tasks List page MUST remove ignored workflow-scope parameters and MUST NOT include secrets.
- **FR-010**: Labels and filter values rendered by the Tasks List MUST be displayed as text and must not render untrusted HTML.
- **FR-011**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `MM-586` and this canonical Jira preset brief for traceability.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: UI tests prove the normal Tasks List request includes task scope and excludes `workflowType`, `entry`, `scope=system`, and `scope=all` when loaded from default and old workflow-scope URLs.
- **SC-002**: UI tests prove `Scope`, `Workflow Type`, and `Entry` controls are absent while Status and Repository task filters remain usable.
- **SC-003**: UI tests prove old system/all/manifest URL parameters are normalized away and a recoverable notice is visible without revealing workflow-kind rows or chips.
- **SC-004**: UI tests prove the table headers do not include `Kind`, `Workflow Type`, or `Entry`.
- **SC-005**: Backend route tests prove source-temporal execution listing fails safe to task-run query semantics when broad workflow-scope parameters are supplied by an ordinary user.
- **SC-006**: Final verification confirms `MM-586`, DESIGN-REQ-005, DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-017, and DESIGN-REQ-025 are preserved and covered by implementation evidence.
