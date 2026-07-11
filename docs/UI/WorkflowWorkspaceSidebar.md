# Workflow Workspace Sidebar

Status: Proposed SPA desired-state contract
Owners: MoonMind Engineering
Last updated: 2026-07-10
Canonical for: desktop workflow workspace layout, SPA list-to-detail transitions, workflow sidebar navigation, and mobile workflow list/detail behavior where not superseded by the workflow list display mode contract

**Implementation tracking:** Rollout notes, task breakdowns, and local-only handoffs belong under `docs/tmp/`, Jira, or gitignored implementation notes. This document defines the durable product and UI contract for the workspace/sidebar behavior that unifies the Workflows list and Workflow Details surfaces.

**SPA architecture note:** This design assumes the MoonMind dashboard is a persistent single-page application. Internal workflow navigation changes the client route and visible workspace state without reloading the document, remounting the dashboard shell, or asking FastAPI for a route-specific HTML page. FastAPI still serves direct deep links to the SPA shell and still owns APIs, auth, and authorization.

**Supersedes earlier detail-owned shell posture:** Any implementation that mounts the workflow sidebar only inside the standalone workflow detail page is a transitional shape, not the target. The target is a parent Workflows workspace route that owns both the full list surface and the selected-detail workspace. That parent ownership is what prevents the sidebar/detail UI from being trapped inside a centered detail-page container with excessive left margin.

---

## 1. Purpose

MoonMind should make desktop workflow browsing feel like one continuous workspace.

In the default desktop state, `/workflows` shows the full Workflows list with the table, filters, toolbar, pagination, and list-level scanning affordances. When a user clicks a workflow title, the same workspace should transition in place: the list compresses into the workspace's single contextual sidebar, and the selected Workflow Details surface loads into the space that opens to the right. The browser URL changes to the canonical workflow detail URL, but the SPA shell does not reload.

When the user chooses to expand the workflow list back to full screen, the selected detail view closes. The route returns to `/workflows`, the full list occupies the workspace again, and no hidden selected detail remains active in the UI.

On mobile, the split workspace is not used. Mobile keeps the straightforward card-list-to-standalone-detail model, also through SPA navigation when already inside the dashboard.

This document amends the existing UI contracts without replacing them:

- `docs/UI/CollectionWorkspaceLayout.md` is canonical for the masthead navigation, shared collection-sidebar component, and shared entity-detail frame.
- `docs/UI/DashboardSPAArchitecture.md` is canonical for the persistent SPA shell, client routing authority, route fallback, providers, and same-origin API model.
- `docs/UI/WorkflowsListPage.md` remains canonical for full Workflows list content, filters, table columns, pagination, and mobile cards.
- `docs/UI/WorkflowDetailsPage.md` remains canonical for detail content, tabs, actions, evidence sections, logs, artifacts, and recovery flows.
- `docs/UI/WorkflowConsoleArchitecture.md` remains canonical for API boundaries, workflow concepts, and backend ownership.
- `docs/UI/DashboardDesignSystem.md` remains canonical for visual language, motion, and component styling rules.
- `docs/UI/WorkflowListDisplayModes.md` is canonical for the three-mode list display system, the shell/workspace list-mode control, and the sidebar-as-table-slice visual contract on participating Workflows and Create surfaces.

---

## 2. Product stance

The desktop workflow surface should behave like a modern work-item or conversation console: a scan-first list can become a compact navigation rail, the selected item opens in the main pane, and users can switch related workflows without returning to a full table every time.

Core rules:

1. The workspace is a **presentation and routing composition**, not a new workflow entity.
2. The Workflows list and Workflow Details page are unified under one SPA workspace parent on desktop.
3. `/workflows` remains the canonical full-list URL.
4. `/workflows/{workflowId}` remains the canonical selected-detail URL.
5. Detail subroutes remain canonical child URLs of the selected workflow.
6. Selecting a workflow uses client-side routing and data loading, not a document reload.
7. Expanding the list to full screen exits selected-detail mode and returns to `/workflows`.
8. For the superseding three-mode system, the sidebar is the one-column table-slice form defined by `docs/UI/WorkflowListDisplayModes.md`; outside that system, it remains a compact projection of the list rather than the full workflow table squeezed into a rail.
9. The UI must not create a second workflow detail implementation or a second detail data model.
10. The browser still calls only same-origin MoonMind APIs.

---

## 3. Desired desktop interaction in one flow

The primary desktop flow is:

1. User lands on `/workflows`.
2. The full Workflows list renders across the available dashboard content width.
3. User clicks a workflow title.
4. The SPA router pushes `/workflows/{workflowId}`.
5. The dashboard shell stays mounted.
6. The Workflows workspace parent stays mounted.
7. The full list surface compresses into a far-left compact workflow sidebar.
8. The selected detail pane appears to the right of the sidebar.
9. The detail pane shows its route-level loading state until selected workflow data is ready.
10. The sidebar remains interactive for switching workflows.
11. User clicks `Expand workflow list`.
12. The SPA router pushes or replaces `/workflows` according to the history policy.
13. The detail pane unmounts and the full Workflows list expands back into the available workspace.

This should feel like resizing and reusing the same workspace, not like leaving the list page and loading a separate detail page.

---

## 4. Route and SPA state model

Routes remain canonical and workflow-oriented:

| Route | Purpose | Desktop SPA presentation | Mobile SPA presentation |
| --- | --- | --- | --- |
| `/workflows` | Full workflow list | Full list fills the Workflows workspace. No selected detail pane is visible. | Card list. |
| `/workflows/{workflowId}` | Selected workflow detail | Far-left compact workflow sidebar plus selected detail pane. | Standalone detail page. |
| `/workflows/{workflowId}/steps` | Detail Steps subroute | Same desktop workspace; detail pane opens Steps. | Standalone detail page on Steps. |
| `/workflows/{workflowId}/artifacts` | Detail Artifacts subroute | Same desktop workspace; detail pane opens Artifacts. | Standalone detail page on Artifacts. |
| `/workflows/{workflowId}/runs` | Detail Runs subroute | Same desktop workspace; detail pane opens Runs. | Standalone detail page on Runs. |
| `/workflows/{workflowId}/debug` | Detail Debug subroute when enabled | Same desktop workspace; detail pane opens Debug. | Standalone detail page on Debug when enabled. |

Rules:

1. The selected workflow identity belongs in the path, not in a `selectedWorkflowId` query parameter.
2. The desktop workspace mode is derived primarily from the route:
   - `/workflows` means full list mode.
   - `/workflows/{workflowId}` and detail subroutes mean selected-detail workspace mode.
3. Sidebar hidden/open preference is UI state. It must not replace route-derived selected workflow state.
4. Full list mode and detail mode must be linkable, reloadable, and shareable.
5. Direct visits to workflow detail URLs on desktop render the workspace with the sidebar visible by default unless a user preference intentionally hides it.
6. Direct visits to workflow detail URLs on mobile render the standalone mobile detail presentation.
7. Internal workflow navigation uses router-native links or `navigate()`, not `window.location`, full-page `<form>` navigation, or route-specific HTML reloads.
8. Hard browser navigation is reserved for external URLs, downloads, auth redirects, and non-dashboard resources.

---

## 5. Desktop workspace states

The desktop workflow surface has two primary states and one optional focus state.

| State | Route | Layout |
| --- | --- | --- |
| Full list | `/workflows` | Full Workflows list across the available dashboard content width. |
| Selected-detail workspace | `/workflows/{workflowId}` and subroutes | Compact workflow sidebar pinned to the far left; selected Workflow Details content in the main pane. |
| Detail focus with sidebar hidden | `/workflows/{workflowId}` and subroutes | Optional focus mode. Detail uses the sidebar's width; a small `Open workflow sidebar` control remains available. |

### 5.1 Full list state

The full list state is the Workflows List page rendered inside the Workflows workspace parent.

Rules:

1. The page keeps the full desktop table, filter controls, active filter chips, toolbar, pagination, and list behavior defined in `docs/UI/WorkflowsListPage.md`.
2. The list should use the full available dashboard content width. Do not constrain it to the detail-page max-width container.
3. A workflow title is the primary link to the selected-detail workspace.
4. The row may also be clickable if the existing list contract allows it, but the title must remain a normal accessible link.
5. Clicking a workflow title performs a SPA route transition to `/workflows/{workflowId}`.
6. The full list component should not unmount the entire dashboard shell.
7. The Workflows workspace parent should preserve list query state, scroll restoration hints, focus return intent, and cached list data where practical.
8. Full list mode remains optimized for comparing workflows across columns.

### 5.2 Selected-detail workspace state

In selected-detail workspace state, the workspace renders:

1. a compact workflow sidebar at the workspace left edge;
2. the selected Workflow Details surface in the main pane;
3. any right rail already owned by the Workflow Details page, if available and if width allows.

Rules:

1. The sidebar and detail pane are siblings owned by the Workflows workspace parent.
2. The sidebar must not be rendered inside the detail page's centered content container.
3. The detail pane uses the existing Workflow Details component, data contract, action logic, tab model, and evidence sections.
4. The detail pane may have its own internal readable content width for dense evidence, but the workspace grid itself must not create a large empty margin to the left of the sidebar.
5. Sidebar scrolling and detail scrolling are independent.
6. The active workflow is highlighted in the sidebar.
7. Clicking another workflow in the sidebar uses SPA navigation to load that workflow's detail in the same detail pane.
8. The detail route remains stable when the sidebar list refetches or filters change.
9. The selected detail remains usable if the sidebar list fails to load.
10. The sidebar remains usable if an optional detail subresource fails.

### 5.3 Expanding back to the full list

`Expand workflow list` exits selected-detail workspace mode.

Rules:

1. The control navigates to `/workflows` with preserved list context where possible.
2. The selected detail pane unmounts and is not visibly retained.
3. The full Workflows list expands to occupy the workspace.
4. Cached detail data may remain in TanStack Query, but no hidden selected detail should be considered active UI state.
5. If the user presses browser Back after expanding, the previous detail route may reopen because it is a normal history entry. That is browser history, not a hidden selected workflow in full list mode.
6. Focus after expansion lands on the Workflows page title, the previously activated workflow row, or the list container according to the focus restoration policy.

### 5.4 Optional detail focus with sidebar hidden

Hiding the sidebar while staying on a workflow detail route is optional and separate from expanding the list in this older workspace contract. On surfaces covered by `docs/UI/WorkflowListDisplayModes.md`, these separate controls are superseded by the shell/workspace list display radio group.

Rules:

1. For the three-mode system, `hidden` in the shell/workspace radio group replaces `Hide workflow sidebar`.
2. For the three-mode system, `sidebar` in the shell/workspace radio group replaces `Open workflow sidebar`.
3. For the three-mode system, `table` in the shell/workspace radio group replaces `Expand workflow list`.
4. Outside the three-mode system, a `Hide workflow sidebar` or equivalent control may hide the sidebar without leaving `/workflows/{workflowId}`.
5. Hiding the sidebar does not close the selected detail.
6. A compact `Open workflow sidebar` control remains available only when this older optional focus mode is used.
7. Reopening the sidebar restores selected-detail workspace state.
8. This state exists for detail focus only. It must not be confused with full list mode.
9. If this optional state causes product confusion, omit it and keep only full list plus selected-detail workspace.

---

## 6. Component and route ownership

The SPA should model Workflows as one parent workspace route with list and detail children.

Representative route tree:

```tsx
const router = createBrowserRouter([
  {
    path: '/',
    element: <DashboardShell />,
    children: [
      {
        path: 'workflows',
        element: <WorkflowsWorkspacePage />,
        children: [
          { index: true, element: <WorkflowListSurface mode="full" /> },
          { path: 'new', element: <StartWorkflowSurface /> },
          { path: ':workflowId', element: <WorkflowDetailSurface tab="overview" /> },
          { path: ':workflowId/steps', element: <WorkflowDetailSurface tab="steps" /> },
          { path: ':workflowId/artifacts', element: <WorkflowDetailSurface tab="artifacts" /> },
          { path: ':workflowId/runs', element: <WorkflowDetailSurface tab="runs" /> },
          { path: ':workflowId/debug', element: <WorkflowDetailSurface tab="debug" /> },
        ],
      },
    ],
  },
]);
```

Recommended frontend structure:

```text
DashboardShell
└── WorkflowsWorkspacePage
    ├── WorkflowListSurface
    │   ├── full-list rendering when route is /workflows
    │   └── compact-list projection when route has :workflowId
    ├── WorkflowSidebar
    └── WorkflowDetailSurface
        └── existing WorkflowDetailsPage content
```

Rules:

1. `DashboardShell` owns global app providers, the masthead, global utilities, route-level error boundaries, and capability state.
2. `WorkflowsWorkspacePage` owns desktop workspace composition, route-derived workspace mode, list context, sidebar visibility preference, split layout, and Workflow collection utility state.
3. `WorkflowListSurface` owns full-list table/card rendering and can provide compact row data for the sidebar.
4. `WorkflowSidebar` owns compact workflow navigation only.
5. `WorkflowDetailSurface` adapts the existing detail page to the workspace pane and owns detail tabs/subroutes.
6. The detail page must not own the sidebar as an embedded child of its page container.
7. The sidebar must not duplicate the Workflow Details primary action bar.
8. Workflow row actions that mutate executions should remain in existing list/detail action components unless explicitly redesigned for the sidebar.
9. Client stores may hold transient workspace preferences, but server snapshots remain owned by query hooks and APIs.
10. The `new` child route is reserved for the Start Workflow page and must be declared before the dynamic `:workflowId` detail children so `/workflows/new` is not matched as a workflow detail for ID `new`. This preserves the existing dashboard reservation of `/workflows/new` for Start Workflow navigation.

---

## 7. URL, history, and list context

The workspace should preserve list context without making detail routing ambiguous.

List context may include:

- shareable filters already supported by the Workflows list;
- sort state when list sorting is URL-supported;
- page size;
- cursor or page hints when safe;
- scroll restoration and focus return hints in router state or session storage;
- compatibility/debug/source hints only when still needed.

Rules:

1. The workflow ID remains the path parameter.
2. List filters and sort belong in URL search parameters only when they are shareable and safe.
3. Transient scroll position and focus restoration should use router state, session storage, or a small client store rather than bloating the URL.
4. The UI must not add `selectedWorkflowId` to `/workflows` as the primary selection model.
5. `Expand workflow list` reconstructs `/workflows` from the preserved list context.
6. If no list context exists, `Expand workflow list` navigates to plain `/workflows`.
7. If a preserved pagination cursor is stale, the list should recover to the first page with a visible but non-blocking reset.
8. Query parameters must not include secrets, raw prompts, full memo/search attributes, or large detail payloads.
9. Browser refresh of a supported workflow route returns the SPA shell and lets the client route table render the correct state.

---

## 8. Data fetching, cache reuse, and resilience

Desktop workspace detail mode may fetch sidebar list data and selected workflow detail data in parallel. The selected detail must not depend on the sidebar list request succeeding.

Minimum API posture:

1. Sidebar/list context: compact workflow list rows from `GET /api/executions` or the SPA capability endpoint's configured workflow-list endpoint.
2. Selected detail: detail snapshot from `GET /api/executions/{workflowId}` or the configured detail endpoint.
3. Detail subresources: steps, artifacts, logs, events, related runs, and debug panels according to the Workflow Details page contract.
4. Optional live updates: list-level and detail-level streams or compact polling, owned by the persistent SPA shell or workspace.

Rules:

1. The full list and sidebar should share the same compact list-row contract.
2. The sidebar should reuse the full list query cache when the user entered from `/workflows`.
3. Selecting a workflow should not discard list data before the sidebar can render.
4. Detail data loads independently of list data.
5. Sidebar list failure shows a recoverable sidebar error while leaving the detail pane usable.
6. Detail failure shows the existing Workflow Details error or not-found state while leaving the sidebar usable when possible.
7. If the selected workflow is absent from the sidebar result because of filters, pagination, or stale data, show a pinned `Current workflow` row sourced from the selected detail snapshot when available.
8. Polling or live updates must not steal focus from sidebar or detail interactions.
9. Sidebar list refetches must preserve the active workflow highlight when the selected workflow still exists.
10. Sidebar rows must never expose workflows outside the current user's authorization scope.
11. List, sidebar, and detail queries should use stable query keys so SPA route changes reuse appropriate cached data.
12. Do not use route-specific HTML boot payloads as the source of workflow list or detail data in the target SPA state.

---

## 9. Workflow sidebar contract

The workflow sidebar is a compact desktop navigation rail derived from the current workflow list context.

For surfaces covered by `docs/UI/WorkflowListDisplayModes.md`, the sidebar visual and row metric contract is superseded by that document's sidebar-as-table-slice design. The rows still use the same authorization, routing, active-state, and resilience rules below, but their header, row height, divider, and first-column alignment follow the display-mode contract.

Required regions outside the superseding three-mode system:

1. top utility row;
2. `Expand workflow list` button or link;
3. optional `Hide workflow sidebar` button;
4. compact workflow rows;
5. active workflow highlight;
6. optional pinned `Current workflow` row;
7. optional loading, empty, and error states;
8. optional lightweight pagination or `Load more` when needed.

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

1. In the three-mode system, the sidebar is a one-column table slice with the shared table header and row metric behavior defined by `docs/UI/WorkflowListDisplayModes.md`.
2. Outside the three-mode system, the sidebar is not a full table and does not show the full column set.
3. Sidebar rows prioritize the workflow title and compact status context within the shared row height.
4. The sidebar uses the same authorization and visibility rules as `/workflows`.
5. The sidebar should use the current list filters and ordering when the user entered from `/workflows`.
6. If the selected workflow is not present in the current sidebar result window, show a pinned `Current workflow` row above the filtered list.
7. The pinned row is visually distinct from normal list rows but must not imply the workflow matches the active filters.
8. `aria-current="page"` marks the active workflow row/link.
9. Sidebar rows support keyboard navigation as ordinary links, with visible focus states.
10. Sidebar state must not mutate detail state except by explicit workflow navigation.
11. The sidebar must be mounted at the workspace grid level as the only sidebar beside the primary pane.

---

## 10. Sidebar and workspace controls

For surfaces covered by `docs/UI/WorkflowListDisplayModes.md`, the shell/workspace list display radio group supersedes the separate controls in this section. Use `hidden`, `sidebar`, and `table` as the canonical controls for hiding the list, showing the sidebar list, and returning to the full-screen table.

Outside that three-mode system, the workspace controls must make three different concepts distinct.

| Control | Route effect | Behavior |
| --- | --- | --- |
| `Expand workflow list` | Navigates to `/workflows`. | Closes the selected detail UI and restores the full Workflows list. |
| `Hide workflow sidebar` | No route change. | Optional focus mode; hides the sidebar while keeping the current detail route active. |
| `Open workflow sidebar` | No route change. | Restores the sidebar in the current detail route. |

Rules:

1. These rules apply only outside the superseding three-mode system.
2. `Expand workflow list` is the primary way to leave selected-detail workspace mode.
3. `Expand workflow list` must not be implemented as a width-only change that leaves an invisible selected detail mounted.
4. `Hide workflow sidebar` must not navigate away from the current workflow detail route.
5. `Open workflow sidebar` must not change the selected workflow.
6. The controls must not be visually or semantically interchangeable.
7. Controls must have visible labels, tooltips, or clear icon affordances plus accessible names.
8. Keyboard focus after `Hide workflow sidebar` moves to `Open workflow sidebar` or the first meaningful detail heading.
9. Keyboard focus after reopening the sidebar moves to the sidebar utility row or active workflow row.
10. Keyboard focus after `Expand workflow list` lands on a meaningful full-list element.

---

## 11. Layout and visual design contract

The selected-detail workspace should fit the Dashboard Design System while solving the current spacing problem.

Visual posture:

- desktop-only left rail;
- collection-sidebar alignment at the workspace left edge;
- no detail-page max-width wrapper around the entire split workspace;
- glass control shell where performance allows;
- matte or satin row interiors for legibility;
- strong active-row state;
- restrained hover brightening;
- compact status treatment;
- independent scroll containers;
- clear edge separation between sidebar and detail pane.

Layout rules:

1. Use a top-level workspace grid or flex layout owned by `WorkflowsWorkspacePage`.
2. In selected-detail workspace mode, the sidebar column starts at the workspace left edge.
3. The detail pane starts immediately after the sidebar gutter.
4. The split workspace must not be horizontally centered inside a narrow detail-page container.
5. The masthead remains above the workspace; no page-navigation sidebar is inserted beside the collection sidebar.
6. Workflow and Recurring sidebars share the neutral `CollectionSidebar` shell and state components; workflow-specific adapters provide row data and copy.
7. Full-list mode uses the same workspace root and expands to one column.
8. The detail pane may contain nested readable-width sections for logs, evidence, markdown, or dense tables, but those constraints apply inside the pane rather than to the entire workspace.
9. Recommended initial sidebar width is approximately `280-340px`.
10. Minimum usable sidebar width is approximately `240px`.
11. On medium desktop widths, prefer hiding optional sidebar metadata before shrinking titles below readability.
12. On narrow tablets, prefer the mobile/standalone detail model unless both sidebar and detail remain usable.

---

## 12. Motion and loading behavior

The list-to-detail transition should feel like a workspace morph, not a page reload.

Rules:

1. Selecting a workflow may animate the full list compressing into a sidebar, but the animation must be short and restrained.
2. Loading the detail should happen in the detail pane, not by blanking the entire dashboard.
3. The sidebar should appear quickly from existing list data when available.
4. Do not animate the detail page with large horizontal translations.
5. `Expand workflow list` may animate the sidebar/full-list expansion, but the selected detail should simply unmount or fade out.
6. In reduced-motion mode, layout changes snap or use near-instant opacity changes.
7. Hover and focus effects should brighten or clarify the target rather than darkening it.
8. Loading states should avoid shimmer when reduced motion is requested.

---

## 13. Accessibility requirements

Rules:

1. Workflow title links in the full list are keyboard reachable and behave as normal links.
2. Sidebar controls are keyboard reachable.
3. On surfaces outside the superseding three-mode system, `Expand workflow list`, `Hide workflow sidebar`, and `Open workflow sidebar` have descriptive accessible names.
4. Active workflow row/link uses `aria-current="page"`.
5. Sidebar list has an accessible name such as `Workflow navigation`.
6. Workflow row links include enough text for screen-reader users to identify the workflow.
7. The sidebar and detail regions are distinguishable by landmarks or labelled regions.
8. Collapsing or hiding the sidebar moves focus deterministically.
9. Reopening the sidebar moves focus deterministically.
10. Expanding back to the full list lands focus on a meaningful element in the full list.
11. Color is not the only active, hover, or selected-state indicator.
12. Mobile users do not encounter hidden desktop-only sidebar controls in the accessibility tree.
13. Browser Back and Forward update focus and route state without trapping users in stale panes.

---

## 14. Empty, loading, and error states

Sidebar and workspace states:

| State | Behavior |
| --- | --- |
| Full list loading | Show the Workflows list loading state in the full workspace. |
| Detail loading after selection | Keep sidebar/list context visible; show detail-pane loading state. |
| Sidebar loading with cached list | Render cached rows when safe and indicate refresh if needed. |
| Sidebar loading without cache | Show compact sidebar skeleton rows or `Loading workflows...`. |
| Empty sidebar list | Show `No workflows match the current list filters.` and keep `Expand workflow list` available. |
| Selected workflow outside filters | Show pinned `Current workflow` row plus an empty/filtered list message when appropriate. |
| Sidebar list error | Show recoverable error and retry action; center detail remains usable. |
| Detail not found | Use existing Workflow Details not-found state; sidebar remains available when it can load. |
| Expand list with stale context | Return to full list and recover to first page with a non-blocking reset message. |

Rules:

1. Empty sidebar state must not clear active list filters automatically.
2. Sidebar retry retries only sidebar list data, not the detail page.
3. Detail retry retries only detail data and relevant subresources.
4. If both sidebar and detail fail, each region shows its own recovery path.
5. Expanding back to the full list should remain available even when detail loading or detail error occurs.

---

## 15. Mobile behavior

Mobile does not use the desktop split workspace.

Rules:

1. `/workflows` renders the mobile workflow card list.
2. Tapping a workflow card title or `View details` action navigates to `/workflows/{workflowId}` through SPA routing when already inside the dashboard.
3. The workflow sidebar is not shown on mobile.
4. Desktop-only workspace controls are not shown on mobile.
5. Mobile detail pages keep a normal `Back to workflows` or breadcrumb affordance.
6. Browser back should return users to the prior card-list position when possible.
7. Mobile filtering remains the responsibility of the mobile filter sheet or equivalent list filter surface.
8. Mobile users must not need the desktop sidebar to switch workflows; they return to the card list to choose another workflow.
9. Direct mobile refresh of `/workflows/{workflowId}` should still render the detail route through the SPA shell.

Breakpoint guidance:

| Viewport class | Behavior |
| --- | --- |
| Desktop / wide tablet | Split workspace may be enabled. |
| Narrow tablet | Prefer standalone detail unless there is enough width for both usable sidebar and detail content. |
| Mobile | Always standalone detail. |

---

## 16. Security and privacy

Rules:

1. The workspace calls only same-origin MoonMind APIs.
2. Sidebar rows are subject to the same authorization and owner scoping as `/workflows`.
3. Filter params must not widen workflow visibility.
4. Sidebar labels, titles, repository values, runtime labels, and status text render as text, never trusted HTML.
5. URL state must not include secrets, raw prompts, full memo/search attributes, presigned URLs, or detail payloads.
6. Direct detail access remains backend-authorized even when the workflow is not visible in the current sidebar list.
7. SPA fallback must not capture API, auth, health, static, artifact, webhook, OpenAPI, or other non-dashboard routes.
8. Client-side route guards are presentation guards only; backend authorization remains required.

---

## 17. Testing contract

Implementation should add or preserve tests for these behaviors:

1. Desktop `/workflows` renders the full-width workflow list with table columns.
2. Desktop workflow title links point to canonical detail URLs.
3. Clicking a workflow title uses SPA routing and does not reload the document.
4. `DashboardShell` and `QueryClientProvider` remain mounted across `/workflows` to `/workflows/{workflowId}` transitions.
5. Desktop selecting a workflow renders the compact sidebar at the workspace left edge as the only sidebar.
6. Desktop selecting a workflow renders existing Workflow Details content in the main pane.
7. The split workspace is not wrapped in the detail page's centered max-width container.
8. Detail loading appears in the detail pane while sidebar/list context remains visible.
9. Detail subroutes render inside the same workspace.
10. The active workflow is highlighted in the sidebar.
11. The active sidebar item exposes `aria-current="page"`.
12. Clicking another sidebar workflow loads that workflow in the same detail pane through SPA routing.
13. On surfaces covered by `docs/UI/WorkflowListDisplayModes.md`, the shell/workspace `table` mode navigates to `/workflows`.
14. On surfaces covered by `docs/UI/WorkflowListDisplayModes.md`, the shell/workspace `table` mode unmounts or closes the selected detail UI.
15. On surfaces covered by `docs/UI/WorkflowListDisplayModes.md`, the shell/workspace `table` mode preserves list query state where possible.
16. Outside the superseding three-mode system, `Expand workflow list` navigates to `/workflows`.
17. Outside the superseding three-mode system, `Expand workflow list` unmounts or closes the selected detail UI.
18. Outside the superseding three-mode system, `Expand workflow list` preserves list query state where possible.
19. Outside the superseding three-mode system, `Hide workflow sidebar`, if implemented, hides the sidebar without changing the workflow route.
20. Outside the superseding three-mode system, `Open workflow sidebar`, if implemented, restores the sidebar without changing the workflow route.
21. If the selected workflow is outside the current sidebar result window, a pinned current-workflow row appears.
22. Sidebar list failure does not prevent selected detail content from rendering.
23. Detail failure does not erase a successfully loaded sidebar.
24. Mobile `/workflows` renders cards rather than the desktop sidebar layout.
25. Mobile tapping a card opens standalone `/workflows/{workflowId}` detail.
26. Desktop-only sidebar controls are absent from mobile rendering and from the mobile accessibility tree.
27. Keyboard focus returns to the correct control or region after sidebar hide, reopen, and list expansion.
28. Reduced-motion settings disable large sidebar/list transition effects.
29. Unauthorized workflows never appear in sidebar rows, counts, pinned rows, or fallback states.
30. Direct refresh of `/workflows/{workflowId}` returns the SPA shell and renders the selected detail route.
31. API paths and non-dashboard routes do not fall back to the SPA shell.

---

## 18. Non-goals

This design does not require:

1. replacing canonical workflow URLs;
2. implementing workflow detail as an embedded modal;
3. duplicating the Workflow Details page component;
4. duplicating detail data in a client store;
5. showing all workflow table columns in the sidebar;
6. adding a mobile split view;
7. exposing system workflow browsing through the sidebar;
8. direct browser calls to Temporal, GitHub, Jira, object storage, model providers, runtime providers, or artifact stores;
9. implementing draggable/resizable sidebars in the first version;
10. adding raw Temporal query syntax to sidebar navigation;
11. moving Workflow Details primary actions into the sidebar;
12. making the full list unusable as a comparison table;
13. preserving an invisible selected detail while the UI claims to be in full-list mode.

---

## 19. Fundamental risks and design answers

There is no fundamental product blocker in the desired desktop interaction, but the implementation can go wrong if these seams are not explicit.

### 19.1 Route change is still useful

The dream does not require avoiding URL changes. It requires avoiding document reloads and disjoint page shells. Keeping `/workflows/{workflowId}` in the address bar is important for deep links, refresh, browser history, and shareability.

Design answer: use SPA routing for the transition and keep the workflow ID in the path.

### 19.2 Parent ownership is required

If the sidebar is mounted by the detail page, the implementation tends to inherit detail-page layout constraints. That is the likely cause of sidebars appearing inside a central container with a large empty left margin.

Design answer: `WorkflowsWorkspacePage` owns the grid and places the sidebar and detail pane as siblings.

### 19.3 Full list and sidebar are not identical surfaces

A full workflow table cannot simply be squeezed into a left rail. The sidebar needs a compact projection.

Design answer: share list query context and row identity, but render a sidebar-specific row layout.

### 19.4 Expanding the list must clear visible selection

If expanding the list only widens the sidebar while retaining an invisible detail selection, users can end up with ambiguous state.

Design answer: expanding navigates to `/workflows` and unmounts the selected detail UI. Query caches may remain, but selected detail is no longer active UI state.

### 19.5 List context is best-effort

Filters, sorting, and page size can be preserved. Exact scroll position, stale cursors, and virtualized row windows are more fragile.

Design answer: preserve shareable context in URL parameters, transient restoration hints in router/client state, and recover gracefully when stale.

### 19.6 Mobile should not inherit desktop complexity

Trying to force the split workspace onto mobile would degrade both list scanning and detail reading.

Design answer: keep mobile card list to standalone detail.

---

## 20. Desired implementation sequence

Recommended sequence for the SPA target:

1. Introduce `WorkflowsWorkspacePage` as the parent route for `/workflows` and workflow detail subroutes.
2. Move desktop workspace layout ownership out of detail-only entrypoints or page containers.
3. Ensure FastAPI serves the SPA shell for supported workflow deep links and never for API/non-dashboard routes.
4. Route workflow title clicks through router-native links.
5. Preserve list query context and query cache when transitioning from full list to selected-detail workspace.
6. Render the compact `WorkflowSidebar` from the shared list-row contract.
7. Render existing `WorkflowDetailsPage` content in the workspace detail pane.
8. For the superseding three-mode system, add the shell/workspace `table` mode and make it navigate to `/workflows` while closing the visible detail.
9. Outside the superseding three-mode system, add `Expand workflow list` and make it navigate to `/workflows` while closing the visible detail.
10. Add optional sidebar hide/open controls only outside the superseding three-mode system and only after the core list-to-detail-to-list flow is correct.
11. Add pinned current-workflow behavior when selected detail is outside the sidebar list.
12. Remove or invert any transitional detail-owned `WorkflowWorkspaceShell` once the parent workspace route is in place.
13. Add tests for SPA navigation, shell persistence, layout placement, detail close-on-expand, direct deep links, mobile fallback, and API fallback exclusions.
14. Remove any feature flag after desktop and mobile acceptance tests pass.

Final desired state: desktop Workflows feels like one continuous SPA workspace where users can scan, select, inspect, switch, and return to the full list without a document reload, while mobile remains a straightforward card-list-to-detail experience.
