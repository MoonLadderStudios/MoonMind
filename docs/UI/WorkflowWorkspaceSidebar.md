# Workflow Workspace Sidebar

Status: Proposed desired-state contract
Owners: MoonMind Engineering
Last updated: 2026-06-28
Canonical for: desktop workflow workspace layout, workflow sidebar navigation, list-to-detail transitions, sidebar collapse/expand controls, and mobile workflow list/detail behavior

**Implementation tracking:** Rollout and backlog notes live under `docs/tmp/` or in gitignored local-only handoffs. This document defines the product and UI contract for the workspace/sidebar behavior that connects the Workflows list and Workflow Details page.

---

## 1. Purpose

This document defines the desired desktop workflow workspace behavior for MoonMind.

On desktop, selecting a workflow from the Workflows page should minimize the workflow list into a compact left-hand sidebar and render the existing Workflow Details page in the center of the page. The sidebar remains available for switching to another workflow without returning to the full-width table.

On mobile, the split workspace is not used. Tapping a workflow card opens the standalone Workflow Details page.

This document amends the existing UI contracts without replacing them:

- `docs/UI/WorkflowsListPage.md` remains canonical for the full Workflows list, filters, table, pagination, and mobile cards.
- `docs/UI/WorkflowDetailsPage.md` remains canonical for the detail page content and actions.
- `docs/UI/WorkflowConsoleArchitecture.md` remains canonical for route ownership, API boundaries, and dashboard shell architecture.
- `docs/UI/DashboardDesignSystem.md` remains canonical for visual language, motion, and component styling rules.

---

## 2. Product stance

The desktop workflow workspace should feel similar to a modern conversation/work item console: a compact navigation rail on the left, selected detail in the center, and quick switching between related items.

Core rules:

1. The change is a **presentation mode**, not a new product entity.
2. `/workflows` remains the full-width workflow list route.
3. `/workflows/{workflowId}` remains the canonical workflow detail route.
4. The center content in desktop workspace mode reuses the existing Workflow Details page.
5. The sidebar is a minimized workflow navigation list, not a full table.
6. Mobile uses card-to-detail navigation rather than a split workspace.
7. The browser still calls only MoonMind APIs.
8. The UI must not create a second detail implementation or a second detail data model.

---

## 3. Route and presentation model

Routes remain canonical and workflow-oriented:

| Route | Purpose | Desktop presentation | Mobile presentation |
| --- | --- | --- | --- |
| `/workflows` | Full workflow list | Full-width table/list with filters, columns, pagination, and cards hidden | Card list |
| `/workflows/{workflowId}` | Workflow detail | Workflow sidebar plus center detail, unless sidebar is collapsed | Standalone detail page |
| `/workflows/{workflowId}/steps` | Detail Steps subroute | Same workspace shell, center detail opens Steps | Standalone detail page on Steps |
| `/workflows/{workflowId}/artifacts` | Detail Artifacts subroute | Same workspace shell, center detail opens Artifacts | Standalone detail page on Artifacts |
| `/workflows/{workflowId}/runs` | Detail Runs subroute | Same workspace shell, center detail opens Runs | Standalone detail page on Runs |

Rules:

1. The selected workflow identity belongs in the path, not in a `selectedWorkflowId` query parameter.
2. Full list mode and detail mode should be linkable and reloadable.
3. The sidebar layout is driven by viewport and user sidebar state.
4. Detail subroutes stay inside the same desktop workspace shell.
5. Direct visits to `/workflows/{workflowId}` on desktop show the sidebar by default unless a persisted user preference says it was collapsed.
6. Direct visits to `/workflows/{workflowId}` on mobile show the standalone detail page.

---

## 4. Desktop workspace states

The desktop workflow surface has three primary states:

| State | Route | Layout |
| --- | --- | --- |
| Full list | `/workflows` | Full-width workflow list with table columns, filters, toolbar, pagination, and desktop table behavior. |
| Split detail | `/workflows/{workflowId}` | Compact workflow sidebar on the left; existing Workflow Details page in the center. |
| Collapsed-sidebar detail | `/workflows/{workflowId}` | Workflow Details page expands horizontally; a small `Open workflow sidebar` control remains available. |

### 4.1 Full list state

The full list state is the existing Workflows List page.

Rules:

1. The page keeps the full desktop table, filter controls, active filter chips, pagination, and list toolbar defined in `docs/UI/WorkflowsListPage.md`.
2. Clicking a workflow row or title navigates to `/workflows/{workflowId}`.
3. Navigation should preserve list context so the user can return to the same list state with `Expand to full list` or browser back.
4. Full list mode must remain useful on wide screens for comparing workflows across columns.

### 4.2 Split detail state

In split detail state, the page renders:

1. workflow sidebar on the left;
2. selected Workflow Details page in the center;
3. any right rail already owned by the Workflow Details page, if available and if width allows.

Rules:

1. The center region uses the same detail component, data contract, and action logic as the standalone Workflow Details page.
2. Sidebar scrolling and detail scrolling are independent.
3. The active workflow is highlighted in the sidebar.
4. Clicking another workflow in the sidebar navigates to that workflow detail route and loads it in the center region.
5. The detail route should remain stable when the sidebar list refetches or changes.
6. The selected detail remains usable if the sidebar list fails to load.

### 4.3 Collapsed-sidebar detail state

Collapsed-sidebar detail keeps the current workflow detail route and hides the sidebar.

Rules:

1. Collapsing the sidebar does not navigate away from the selected workflow.
2. A compact `Open workflow sidebar` control appears near the top-left of the workspace or detail header area.
3. Reopening the sidebar restores the split detail state.
4. Collapsed state may persist for the session or user preference, but it must not be encoded as the selected workflow identity.
5. The detail page should use the newly available width without large horizontal animation.

---

## 5. Workflow sidebar contract

The workflow sidebar is a compact desktop navigation rail derived from the current workflow list context.

Required regions:

1. top utility row;
2. `Close sidebar` button;
3. `Expand to full list` button;
4. compact workflow rows;
5. active workflow highlight;
6. optional loading, empty, and error states;
7. optional lightweight pagination or `Load more` when needed.

Recommended row contents:

| Element | Requirement |
| --- | --- |
| Workflow title | Required; single or two-line clamp. |
| Status | Required; compact pill, dot, or status label. |
| Updated/created timing | Recommended; use compact relative time when available. |
| Workflow ID | Optional; hidden or secondary by default. |
| Next action | Optional; one-line summary when space allows. |
| Repository/runtime | Optional; show only if it improves scanability. |

Rules:

1. The sidebar is not a table and does not show the full column set.
2. Sidebar rows should prioritize title, status, and recency.
3. The sidebar uses the same authorization and visibility rules as `/workflows`.
4. The sidebar should use the current list filters and ordering when the user entered from `/workflows`.
5. If the selected workflow is not present in the current sidebar result window, show a pinned `Current workflow` row above the filtered list.
6. The pinned row should be visually distinct from normal list rows but must not imply the workflow matches the active filters.
7. `aria-current="page"` marks the active workflow row/link.
8. The sidebar must support keyboard navigation between workflow rows.
9. Sidebar state must not mutate detail state except by explicit workflow navigation.

---

## 6. Sidebar top controls

The sidebar top utility row contains two distinct controls.

| Button | Behavior |
| --- | --- |
| `Close sidebar` | Hides the workflow sidebar and lets the detail page use the available width. A small `Open workflow sidebar` control appears in collapsed detail mode. |
| `Expand to full list` | Navigates to `/workflows` and restores the full-width workflow table/list view with preserved list context where possible. |

Rules:

1. `Close sidebar` changes only layout state; it does not navigate away from the current workflow detail route.
2. `Expand to full list` exits detail mode and navigates to the full Workflows list route.
3. The two controls must not be visually or semantically interchangeable.
4. Both controls must have visible labels, tooltips, or clear icon affordances plus accessible names.
5. Keyboard focus after `Close sidebar` moves to the `Open workflow sidebar` control or the first meaningful detail heading.
6. Keyboard focus after reopening the sidebar moves to the sidebar utility row or active workflow row.
7. Keyboard focus after `Expand to full list` should land on the Workflows page title or list container.

---

## 7. Mobile behavior

Mobile does not use the desktop split workspace.

Rules:

1. `/workflows` renders the mobile workflow card list.
2. Tapping a workflow card title or `View details` action opens `/workflows/{workflowId}` as a standalone detail page.
3. The workflow sidebar is not shown on mobile.
4. `Close sidebar`, `Open workflow sidebar`, and `Expand to full list` sidebar controls are not shown on mobile.
5. Mobile detail pages keep a normal `Back to workflows` or breadcrumb affordance.
6. Browser back should return users to the prior card-list position when possible.
7. Mobile filtering remains the responsibility of the mobile filter sheet or equivalent list filter surface.
8. Mobile users must not need the desktop sidebar to switch workflows; they return to the card list to choose another workflow.

Breakpoint guidance:

| Viewport class | Behavior |
| --- | --- |
| Desktop / wide tablet | Split workspace may be enabled. |
| Narrow tablet | Prefer standalone detail unless there is enough width for both usable sidebar and detail content. |
| Mobile | Always standalone detail. |

---

## 8. URL and list context state

The workspace should preserve list context without making detail routing ambiguous.

List context may include:

- active column/filter drawer state that is already URL-safe;
- page size;
- pagination cursor when safe;
- sort state when the list design supports URL-persisted sort;
- debug/source hints only when needed by compatibility code.

Rules:

1. The workflow ID remains the path parameter.
2. The detail route may carry list-context query parameters only when those parameters already belong to list state or compatibility state.
3. The UI must not add `selectedWorkflowId` to `/workflows` as the primary selection model.
4. `Expand to full list` reconstructs `/workflows` from the preserved list context.
5. If no list context exists, `Expand to full list` navigates to plain `/workflows`.
6. If a preserved pagination cursor is stale, the list should recover to the first page with a visible but non-blocking reset.

---

## 9. Data fetching and resilience

Desktop workspace detail mode may fetch sidebar list context and selected workflow detail data in parallel.

Minimum fetches:

1. Sidebar list context: `GET /api/executions` using preserved list filters where available.
2. Selected detail: `GET /api/executions/{workflowId}`.
3. Existing detail subresources such as steps, artifacts, logs, events, and related runs according to the Workflow Details page contract.

Rules:

1. Detail data must not depend on sidebar list success.
2. Sidebar list failure shows a recoverable sidebar error while leaving the detail page usable.
3. Detail failure shows the existing Workflow Details page error or not-found state.
4. If the selected workflow is absent from the sidebar list response but detail succeeds, show a pinned `Current workflow` row.
5. Polling or live updates must not steal focus from sidebar or detail interactions.
6. Sidebar list refetches must preserve the active workflow highlight when the selected workflow still exists.
7. Sidebar rows must never expose workflows outside the current user's authorization scope.

---

## 10. Component boundaries

Recommended frontend structure:

```text
WorkflowWorkspaceShell
├── WorkflowSidebar
│   ├── WorkflowSidebarControls
│   └── WorkflowSidebarList
└── WorkflowDetailPage
```

Rules:

1. `WorkflowWorkspaceShell` owns responsive layout and sidebar open/collapsed state.
2. `WorkflowSidebar` owns compact list navigation only.
3. `WorkflowDetailPage` continues to own detail header, actions, evidence sections, logs, artifacts, related runs, and detail subroutes.
4. Workflow row actions that mutate executions should remain with existing list/detail action components unless explicitly redesigned for the sidebar.
5. The sidebar must not duplicate the primary action bar from the Workflow Details page.
6. The sidebar must not independently infer capabilities or next actions beyond compact list summaries returned by the list model.

---

## 11. Visual design contract

The sidebar should fit the Dashboard Design System.

Visual posture:

- desktop-only left rail;
- glass control shell where performance allows;
- matte or satin row interiors for legibility;
- strong active-row state;
- restrained hover brightening;
- compact status treatment;
- independent scroll container;
- clear edge separation from the center detail content.

Rules:

1. Dense detail evidence remains matte and readable.
2. The sidebar may use glass styling because it is an elevated navigation/control surface.
3. Do not use large refraction or liquid effects on the scrollable row list if it reduces readability or performance.
4. Active workflow state must be visible without relying on color alone.
5. The sidebar should have enough width for readable workflow titles, but it should not consume table-like horizontal space.
6. Recommended initial desktop width is approximately `280-340px`, with final values controlled by CSS tokens and responsive testing.
7. Avoid nested chrome: the sidebar should feel like one coherent rail, not a stack of unrelated cards.

---

## 12. Motion and reduced motion

Sidebar transitions should be restrained.

Rules:

1. Opening and closing the sidebar may use a short width/opacity transition in the standard dashboard timing range.
2. Do not animate the detail page with large horizontal translations.
3. In reduced-motion mode, sidebar open/close should snap or use near-instant opacity changes.
4. Hover and focus effects should brighten or clarify the target rather than darkening it.
5. Loading states should avoid shimmer if reduced motion is requested.

---

## 13. Accessibility requirements

Rules:

1. Sidebar controls are keyboard reachable.
2. `Close sidebar`, `Open workflow sidebar`, and `Expand to full list` have descriptive accessible names.
3. Active workflow row/link uses `aria-current="page"`.
4. Sidebar list has an accessible name such as `Workflow navigation`.
5. Workflow row links include enough text for screen-reader users to identify the workflow.
6. Collapsing the sidebar moves focus deterministically.
7. Reopening the sidebar moves focus deterministically.
8. `Expand to full list` lands focus on a meaningful element in the full list page.
9. The sidebar and detail regions should be distinguishable by landmarks or labelled regions.
10. Color is not the only active, hover, or selected-state indicator.
11. Mobile users do not encounter hidden desktop-only sidebar controls in the accessibility tree.

---

## 14. Empty, loading, and error states

Sidebar states:

| State | Behavior |
| --- | --- |
| Loading | Show compact sidebar skeleton rows or `Loading workflows...`. |
| Empty list | Show `No workflows match the current list filters.` and keep `Expand to full list` available. |
| Selected workflow outside filters | Show pinned `Current workflow` row plus an empty/filtered list message when appropriate. |
| Sidebar list error | Show recoverable error and retry action; center detail remains usable. |
| Detail not found | Use existing Workflow Details not-found state; sidebar remains available when it can load. |

Rules:

1. Empty sidebar state must not clear active list filters automatically.
2. Sidebar retry should retry only sidebar list data, not forcibly reload the detail page.
3. If both sidebar and detail fail, each region should show its own appropriate recovery path.

---

## 15. Security and privacy

Rules:

1. The sidebar calls only MoonMind APIs.
2. Sidebar rows are subject to the same authorization and owner scoping as `/workflows`.
3. Filter params must not widen workflow visibility.
4. Sidebar labels, titles, repository values, runtime labels, and status text render as text, never trusted HTML.
5. URL state must not include secrets.
6. Direct detail access remains backend-authorized even when the workflow is not visible in the current sidebar list.

---

## 16. Testing contract

Implementation should add or preserve tests for these behaviors:

1. Desktop `/workflows` renders the full-width workflow list with table columns.
2. Desktop clicking a workflow row navigates to `/workflows/{workflowId}`.
3. Desktop `/workflows/{workflowId}` renders the workflow sidebar and existing detail page content.
4. Desktop detail subroutes render inside the workspace shell.
5. The active workflow is highlighted in the sidebar.
6. The active sidebar item exposes `aria-current="page"`.
7. Clicking another sidebar workflow loads that workflow in the center detail region.
8. `Close sidebar` hides the sidebar without changing the workflow route.
9. `Open workflow sidebar` restores the sidebar after it has been closed.
10. `Expand to full list` navigates back to `/workflows`.
11. `Expand to full list` preserves list query state where possible.
12. If the selected workflow is outside the current sidebar result window, a pinned current-workflow row appears.
13. Sidebar list failure does not prevent selected detail content from rendering.
14. Detail failure does not erase a successfully loaded sidebar.
15. Mobile `/workflows` renders cards rather than the desktop sidebar layout.
16. Mobile tapping a card opens standalone `/workflows/{workflowId}` detail.
17. Sidebar controls are absent from mobile rendering and from the mobile accessibility tree.
18. Keyboard focus returns to the correct control after sidebar close and reopen.
19. Reduced-motion settings disable large sidebar transition effects.
20. Unauthorized workflows never appear in sidebar rows, counts, pinned rows, or fallback states.

---

## 17. Non-goals

This design does not require:

1. replacing the canonical `/workflows/{workflowId}` detail route;
2. implementing workflow detail as an embedded modal;
3. duplicating the Workflow Details page component;
4. showing all workflow table columns in the sidebar;
5. adding a mobile split view;
6. exposing system workflow browsing through the sidebar;
7. direct browser calls to Temporal, GitHub, Jira, object storage, or runtime providers;
8. implementing draggable/resizable sidebars in the first version;
9. adding raw Temporal query syntax to sidebar navigation;
10. moving Workflow Details primary actions into the sidebar.

---

## 18. Desired implementation sequence

Recommended sequence:

1. Add the desktop `WorkflowWorkspaceShell` around detail routes behind a feature flag if needed.
2. Reuse existing list query parsing to preserve list context from `/workflows`.
3. Add `WorkflowSidebar` with read-only compact workflow navigation.
4. Render existing `WorkflowDetailPage` in the center region.
5. Add `Close sidebar`, `Open workflow sidebar`, and `Expand to full list` controls.
6. Add pinned current-workflow behavior when the selected workflow is outside the sidebar list.
7. Add desktop tests for route transitions, sidebar collapse/expand, active workflow state, and list-context restoration.
8. Verify mobile still uses card-to-standalone-detail navigation.
9. Remove the feature flag after desktop and mobile acceptance tests pass.

Final desired state: desktop Workflows feels like a compact workflow workspace where users can scan, select, inspect, and switch workflows quickly, while mobile remains a straightforward card-list-to-detail experience.
