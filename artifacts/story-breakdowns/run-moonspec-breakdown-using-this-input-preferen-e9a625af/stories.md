# Story Breakdown: Workflow Workspace Sidebar

- Source: `docs/UI/WorkflowWorkspaceSidebar.md`
- Source document class: `canonical-declarative`
- Source Jira issue key: `MM-975`
- Extracted at: `2026-06-28T09:33:52Z`
- Coverage gate: PASS - every major design point is owned by at least one story.

## Design Summary

The design defines a desktop-only workflow workspace presentation mode: /workflows remains the full list, while workflow detail routes render a compact left navigation sidebar and the existing Workflow Details page in the center. Mobile remains a card-list-to-standalone-detail flow. The design preserves existing route, API, authorization, detail-action, and canonical UI document contracts while adding sidebar navigation, collapse/expand controls, list-context preservation, independent resilience states, visual/motion/accessibility requirements, security guardrails, rollout flag guidance, and targeted test coverage.

## Coverage Points

- `DESIGN-REQ-001` Desktop workspace route shell: Desktop workflow detail routes render a split shell with sidebar plus existing detail, while /workflows remains full-width list.
- `DESIGN-REQ-002` Reuse existing detail contract: The workspace must reuse WorkflowDetailsPage and must not introduce a second detail implementation, data model, or action bar.
- `DESIGN-REQ-003` Responsive mobile standalone behavior: Mobile uses cards on /workflows and standalone detail routes with no sidebar controls or accessibility tree leakage.
- `DESIGN-REQ-004` Route identity and subroutes: Workflow identity stays in the path, detail subroutes stay in the workspace on desktop, and selectedWorkflowId query selection is forbidden.
- `DESIGN-REQ-005` Full list context preservation: Desktop row/title navigation preserves URL-safe list context for browser back and Expand to full list recovery.
- `DESIGN-REQ-006` Sidebar compact navigation: Sidebar is a compact non-table navigation rail with workflow title, status, recency, active highlight, keyboard navigation, and quick switching.
- `DESIGN-REQ-007` Pinned current workflow: When selected detail loads but the workflow is absent from the sidebar result, a distinct pinned Current workflow row appears above the filtered list.
- `DESIGN-REQ-008` Independent data fetching and resilience: Sidebar list and detail fetch independently; each region keeps its own loading/error/retry behavior and does not block the other.
- `DESIGN-REQ-009` Sidebar controls and layout state: Close/Open/Expand controls manage layout or navigation correctly, preserve route state, and handle focus predictably.
- `DESIGN-REQ-010` Collapsed-state persistence: Sidebar collapsed state is layout-only, may persist by session or preference, defaults open on direct desktop detail unless persisted, and is ignored on mobile.
- `DESIGN-REQ-011` Visual layout and styling: Workspace sidebar has stable desktop rail dimensions, readable row treatment, clear edge separation, design-token alignment, and strong non-color-only active state.
- `DESIGN-REQ-012` Motion and reduced motion: Open/close motion is restrained, reduced-motion users avoid large transitions/shimmer, and detail content does not slide dramatically.
- `DESIGN-REQ-013` Accessibility contract: Regions, controls, row links, active states, focus management, and mobile absence are accessible and keyboard reachable.
- `DESIGN-REQ-014` Security and API boundary: Browser clients call only MoonMind APIs, sidebar visibility matches /workflows authorization, URL state carries no secrets, and labels render as text.
- `DESIGN-REQ-015` Detail actions inside workspace: Existing detail actions, menus, subroutes, copy/log/artifact behavior, toasts, and dialogs remain owned by and functional inside WorkflowDetailsPage.
- `DESIGN-REQ-016` Feature flag and rollout: The shell may be introduced behind a rollout flag and later removed after desktop/mobile acceptance tests pass.
- `DESIGN-REQ-017` Testing coverage: Desktop routes, sidebar switching, controls, persistence, resilience, mobile regression, accessibility, motion, and authorization require tests.
- `DESIGN-REQ-018` Non-goal guardrails: Implementation must avoid detail replacement, modal detail, duplicate table sidebar, mobile split view, system browsing, external browser calls, raw Temporal syntax, first-version resizing, and moving primary detail actions to sidebar.
- `DESIGN-REQ-019` Canonical addendum cross-reference: The sidebar contract is an addendum to list/detail/architecture/design-system contracts and must not contradict them.

## Ordered Stories

### STORY-001: Add desktop workflow workspace shell for detail routes

- Short name: `workspace-shell`
- Source reference: `docs/UI/WorkflowWorkspaceSidebar.md`
- Source sections: 1. Purpose, 2. Product stance, 3. Route and presentation model, 4. Desktop workspace states, 10. Component boundaries, 17. Non-goals, 18. Desired implementation sequence
- Claim IDs: `CLAIM-docs-ui-workflow-workspace-sidebar-purpose-001`, `CLAIM-docs-ui-workflow-workspace-sidebar-purpose-002`, `CLAIM-docs-ui-workflow-workspace-sidebar-product-001`, `CLAIM-docs-ui-workflow-workspace-sidebar-routes-001`, `CLAIM-docs-ui-workflow-workspace-sidebar-routes-002`, `CLAIM-docs-ui-workflow-workspace-sidebar-states-001`, `CLAIM-docs-ui-workflow-workspace-sidebar-states-002`, `CLAIM-docs-ui-workflow-workspace-sidebar-boundaries-001`, `CLAIM-docs-ui-workflow-workspace-sidebar-nongoals-001`, `CLAIM-docs-ui-workflow-workspace-sidebar-sequence-001`
- Coverage IDs: `DESIGN-REQ-001`, `DESIGN-REQ-002`, `DESIGN-REQ-004`, `DESIGN-REQ-016`, `DESIGN-REQ-018`, `DESIGN-REQ-019`
- Dependencies: None

As a desktop workflow user, I can open any workflow detail route inside a split workspace that keeps workflow navigation beside the existing Workflow Details page, while /workflows remains the full-width list.

Independent test:
With a desktop viewport and the workspace flag enabled, verify /workflows renders the existing full list, /workflows/{workflowId} and steps/artifacts/runs subroutes render a labelled shell with sidebar region and the existing detail content, and disabling the flag restores standalone desktop detail behavior.

Acceptance criteria:
- Desktop /workflows continues to render the full-width Workflows List page, not the workspace shell.
- Desktop /workflows/{workflowId}, /steps, /artifacts, and /runs render inside WorkflowWorkspaceShell.
- The center region renders the existing Workflow Details page component and action/data contract.
- The selected workflow ID remains in the route path and no selectedWorkflowId query parameter is introduced.
- Direct desktop detail navigation shows the sidebar by default unless persisted collapsed state is applied by the later state story.
- A feature flag can disable the desktop workspace shell without changing mobile behavior.
- The shell remains an addendum to existing list, detail, architecture, and design-system docs and does not contradict their contracts.

Requirements:
- Create or wire WorkflowWorkspaceShell for desktop workflow detail routes only.
- Keep /workflows as the canonical full-list route.
- Route detail subroutes through the same shell on desktop.
- Reuse WorkflowDetailsPage in the center region without duplicating detail data models or primary actions.
- Implement a workspace enablement flag if consistent with existing rollout patterns and document its default in code/tests.

Assumptions:
- Existing dashboard route bootstrapping can select desktop versus mobile presentation from viewport state on the client.

### STORY-002: Preserve workflow list context during desktop list-to-detail navigation

- Short name: `list-context`
- Source reference: `docs/UI/WorkflowWorkspaceSidebar.md`
- Source sections: 4.1 Full list state, 8. URL and list context state, 6. Sidebar top controls
- Claim IDs: `CLAIM-docs-ui-workflow-workspace-sidebar-states-001`, `CLAIM-docs-ui-workflow-workspace-sidebar-url-001`, `CLAIM-docs-ui-workflow-workspace-sidebar-controls-001`
- Coverage IDs: `DESIGN-REQ-005`, `DESIGN-REQ-004`
- Dependencies: `STORY-001`

As a desktop workflow user, I can select a workflow from the full list, inspect it in workspace mode, and expand back to the same useful list context whenever that context is still valid.

Independent test:
Start on /workflows with URL-safe filters, page size, sort, and a stale cursor variant; click a row, assert navigation reaches /workflows/{workflowId} with preserved context where safe, then use Expand to full list and verify /workflows reconstructs or safely resets context.

Acceptance criteria:
- Desktop workflow row/title click navigates to /workflows/{workflowId}.
- URL-safe list filters, page size, and supported sort state are preserved during navigation.
- Safe pagination cursor context is preserved where usable and stale cursor context recovers to a valid list state.
- Expand to full list reconstructs /workflows with preserved query state where possible.
- If no preserved context exists, Expand to full list navigates to plain /workflows.
- The selected workflow identity is never modeled primarily as selectedWorkflowId in query state.

Requirements:
- Reuse existing list query parsing/serialization for context preservation.
- Define the list-context allowlist for detail routes.
- Implement stale cursor recovery to the first valid page with non-blocking user feedback when applicable.
- Ensure browser back returns to the prior list context when possible.

Assumptions:
- Only existing URL-safe list state is preserved; unsafe or unsupported params are dropped rather than transformed.

### STORY-003: Build compact workflow sidebar navigation with pinned current workflow

- Short name: `sidebar-navigation`
- Source reference: `docs/UI/WorkflowWorkspaceSidebar.md`
- Source sections: 4.2 Split detail state, 5. Workflow sidebar contract, 9. Data fetching and resilience, 14. Empty, loading, and error states
- Claim IDs: `CLAIM-docs-ui-workflow-workspace-sidebar-states-002`, `CLAIM-docs-ui-workflow-workspace-sidebar-sidebar-001`, `CLAIM-docs-ui-workflow-workspace-sidebar-sidebar-002`, `CLAIM-docs-ui-workflow-workspace-sidebar-data-001`, `CLAIM-docs-ui-workflow-workspace-sidebar-states-004`
- Coverage IDs: `DESIGN-REQ-006`, `DESIGN-REQ-007`, `DESIGN-REQ-008`
- Dependencies: `STORY-001`, `STORY-002`

As a desktop workflow user, I can scan a compact sidebar, see the active workflow, switch to another workflow, and still understand the current workflow when it is outside the filtered sidebar list.

Independent test:
Render a desktop detail route with sidebar list data, switch to another sidebar row, then render cases where the selected workflow is outside filters, sidebar list is empty, sidebar list fails, and detail fails independently.

Acceptance criteria:
- Sidebar renders in desktop workflow detail mode as a compact non-table list.
- Each normal row shows workflow title and status, with recency or next action when available.
- The active workflow row is visually highlighted and exposes aria-current="page".
- Clicking another workflow row navigates to that workflow detail route and loads it in the center region.
- Sidebar scrolling is independent from detail scrolling.
- If selected workflow is absent from the sidebar result but detail data exists, a visually distinct pinned Current workflow row appears above normal rows.
- Pinned Current workflow disappears when the selected workflow appears in the normal sidebar list.
- Sidebar loading, empty, filtered-out, and recoverable error states do not block or erase the selected detail page.
- Sidebar retry retries only sidebar data.

Requirements:
- Implement WorkflowSidebar, WorkflowSidebarControls container placement, and WorkflowSidebarList compact rows.
- Fetch sidebar list data independently from selected workflow detail data using MoonMind APIs.
- Use current list filters/order where available.
- Preserve active workflow highlight across sidebar refetches where possible.
- Show empty filtered-list text without clearing filters.

Assumptions:
- The existing executions list API provides enough title/status/recency data for compact sidebar rows.

### STORY-004: Implement sidebar controls and collapsed layout state

- Short name: `sidebar-controls`
- Source reference: `docs/UI/WorkflowWorkspaceSidebar.md`
- Source sections: 4.3 Collapsed-sidebar detail state, 6. Sidebar top controls, 12. Motion and reduced motion, 13. Accessibility requirements
- Claim IDs: `CLAIM-docs-ui-workflow-workspace-sidebar-states-003`, `CLAIM-docs-ui-workflow-workspace-sidebar-controls-001`, `CLAIM-docs-ui-workflow-workspace-sidebar-motion-001`, `CLAIM-docs-ui-workflow-workspace-sidebar-a11y-001`
- Coverage IDs: `DESIGN-REQ-009`, `DESIGN-REQ-010`, `DESIGN-REQ-012`, `DESIGN-REQ-013`
- Dependencies: `STORY-001`, `STORY-002`, `STORY-003`

As a desktop workflow user, I can close the sidebar to focus on details, reopen it without losing my selected workflow, and expand back to the full workflow list with predictable focus behavior.

Independent test:
On desktop detail, close and reopen the sidebar while asserting the URL and selected workflow do not change, focus lands on expected controls/rows, persisted state affects reload deterministically, and reduced-motion mode suppresses large transitions.

Acceptance criteria:
- Close sidebar hides the sidebar without navigating away from /workflows/{workflowId}.
- Detail content expands into the available width after sidebar close.
- Open workflow sidebar appears after close and restores the sidebar.
- Reopening does not unnecessarily refetch selected detail data.
- Collapsed state is tracked separately from route-selected workflow identity.
- Direct desktop detail routes default to sidebar open unless persisted state says collapsed.
- Collapsed state is ignored on mobile.
- Expand to full list navigates to /workflows using preserved context from Story 002.
- Keyboard focus moves predictably after close, reopen, and expand actions.
- Reduced-motion mode disables or greatly reduces sidebar transition effects and avoids large detail translation.

Requirements:
- Implement layout-only collapsed state using existing MoonMind UI state patterns where practical.
- Persist collapsed state at session or user-preference level and document reload behavior in tests/code.
- Place Open workflow sidebar control near the top-left workspace/detail area when collapsed.
- Wire Close sidebar and Expand to full list as visually and semantically distinct controls with accessible names.
- Respect prefers-reduced-motion for sidebar transitions and loading states.

Assumptions:
- Session-level persistence is acceptable unless an existing user-preference mechanism is already used for comparable dashboard layout state.

### STORY-005: Keep mobile workflow navigation standalone

- Short name: `mobile-standalone`
- Source reference: `docs/UI/WorkflowWorkspaceSidebar.md`
- Source sections: 3. Route and presentation model, 7. Mobile behavior, 13. Accessibility requirements
- Claim IDs: `CLAIM-docs-ui-workflow-workspace-sidebar-routes-001`, `CLAIM-docs-ui-workflow-workspace-sidebar-routes-002`, `CLAIM-docs-ui-workflow-workspace-sidebar-mobile-001`, `CLAIM-docs-ui-workflow-workspace-sidebar-a11y-001`
- Coverage IDs: `DESIGN-REQ-003`, `DESIGN-REQ-013`, `DESIGN-REQ-018`
- Dependencies: `STORY-001`

As a mobile workflow user, I can continue using workflow cards to open standalone details, with no desktop sidebar controls or split-workspace behavior leaking into mobile rendering or accessibility.

Independent test:
With a mobile viewport, verify /workflows renders workflow cards, card title and View details navigate to /workflows/{workflowId}, detail renders standalone, browser/back affordances work, and no sidebar controls exist in the DOM or accessibility tree.

Acceptance criteria:
- Mobile /workflows renders workflow cards and existing mobile filter behavior remains available.
- Mobile workflow card title navigation reaches /workflows/{workflowId}.
- Mobile View details action reaches /workflows/{workflowId}.
- Mobile detail route renders standalone Workflow Details page.
- Workflow sidebar, Close sidebar, Open workflow sidebar, and Expand to full list are absent from mobile rendering and accessibility tree.
- Mobile users can navigate back to the workflow card list by browser back or a normal breadcrumb/back affordance.
- The desktop workspace feature flag does not alter mobile behavior unless explicitly intended.

Requirements:
- Guard workspace shell and sidebar controls behind desktop/narrow-width presentation checks.
- Preserve existing mobile cards and filter surfaces on /workflows.
- Ensure mobile direct detail links do not instantiate sidebar state or controls.
- Add mobile regression coverage for card-to-detail and no-sidebar leakage.

Assumptions:
- The existing viewport test utilities can model mobile and desktop breakpoints for frontend tests.

### STORY-006: Style and secure the workflow sidebar contract

- Short name: `sidebar-polish`
- Source reference: `docs/UI/WorkflowWorkspaceSidebar.md`
- Source sections: 11. Visual design contract, 12. Motion and reduced motion, 13. Accessibility requirements, 15. Security and privacy, 17. Non-goals
- Claim IDs: `CLAIM-docs-ui-workflow-workspace-sidebar-visual-001`, `CLAIM-docs-ui-workflow-workspace-sidebar-motion-001`, `CLAIM-docs-ui-workflow-workspace-sidebar-a11y-001`, `CLAIM-docs-ui-workflow-workspace-sidebar-security-001`, `CLAIM-docs-ui-workflow-workspace-sidebar-nongoals-001`
- Coverage IDs: `DESIGN-REQ-011`, `DESIGN-REQ-012`, `DESIGN-REQ-013`, `DESIGN-REQ-014`, `DESIGN-REQ-018`
- Dependencies: `STORY-003`, `STORY-004`

As a MoonMind operator, I get a readable, accessible, and secure desktop sidebar that follows dashboard visual language, keeps authorization boundaries intact, and never trusts workflow labels as HTML.

Independent test:
Run frontend rendering tests in light/dark and reduced-motion modes with malicious-looking workflow labels and unauthorized/sidebar-filter scenarios; assert readable styling, no HTML injection, MoonMind-only API calls, and no unauthorized rows or pinned rows appear.

Acceptance criteria:
- Workspace layout has stable left sidebar and center detail regions with a clear visual boundary.
- Sidebar width is usable, initially within the approximate 280-340px range, and does not crowd detail content on desktop.
- Sidebar rows remain readable in light and dark themes.
- Active workflow state is visible without relying only on color.
- Dense detail evidence remains matte/readable and is not over-glassed.
- Styling uses existing dashboard tokens/classes where possible and avoids nested chrome.
- Sidebar calls only MoonMind APIs and filter params do not widen workflow visibility.
- Sidebar labels, titles, repository values, runtime labels, and status text render as text, not trusted HTML.
- URL state does not include secrets and direct detail access remains backend-authorized.

Requirements:
- Add CSS/layout styling for the desktop rail, rows, active state, scroll area, and boundary.
- Use existing dashboard design tokens/classes where practical.
- Ensure generated assets are not hand-edited.
- Verify browser fetches remain within MoonMind API boundaries.
- Add or preserve tests for text rendering and authorization-scope behavior.

Assumptions:
- Authorization is enforced by the existing executions API; frontend tests should still prove the sidebar does not request broader scopes than /workflows.

### STORY-007: Verify workflow detail actions and workspace regressions end to end

- Short name: `workspace-qa`
- Source reference: `docs/UI/WorkflowWorkspaceSidebar.md`
- Source sections: 10. Component boundaries, 16. Testing contract, 17. Non-goals, 18. Desired implementation sequence
- Claim IDs: `CLAIM-docs-ui-workflow-workspace-sidebar-boundaries-001`, `CLAIM-docs-ui-workflow-workspace-sidebar-testing-001`, `CLAIM-docs-ui-workflow-workspace-sidebar-nongoals-001`, `CLAIM-docs-ui-workflow-workspace-sidebar-sequence-001`
- Coverage IDs: `DESIGN-REQ-015`, `DESIGN-REQ-017`, `DESIGN-REQ-018`, `DESIGN-REQ-019`
- Dependencies: `STORY-001`, `STORY-002`, `STORY-003`, `STORY-004`, `STORY-005`, `STORY-006`

As a maintainer, I can trust the workspace rollout because existing Workflow Details actions, list behavior, desktop workspace behavior, mobile regressions, resilience states, and documentation cross-references are covered by targeted tests and a final acceptance sweep.

Independent test:
Run the targeted frontend and API/unit test suites for workflow list/detail/workspace routes, including existing detail action tests, new desktop workspace tests, sidebar control/state tests, mobile regression tests, and docs cross-reference checks where applicable.

Acceptance criteria:
- Existing Workflow Details primary actions work inside workspace mode, including Remediate, Edit Workflow, Rerun, Resume, Cancel, Pause, lifecycle Resume, More menu, copy, log, artifact, and dialog/toast behavior.
- Detail subroute navigation preserves workspace shell on desktop and remains standalone on mobile.
- Sidebar does not duplicate detail primary actions.
- Desktop workspace route tests cover full list, row navigation, detail/subroutes in shell, active highlight, sidebar switching, sidebar failure, and detail failure.
- Sidebar control/state tests cover close, open, expand, preserved query state, route stability, persisted collapsed state, focus, and reduced motion.
- Mobile regression tests cover card rendering, title/View details navigation, standalone detail, no sidebar controls, no accessibility leakage, and existing mobile filters.
- Final QA validates desktop list, detail workspace, switching, collapse/reopen, expand, list context, direct detail links, mobile flow, accessibility, reduced motion, error states, light/dark themes, and documentation reflection.
- Existing UI docs link to the sidebar/workspace contract as an addendum and do not contradict canonical list/detail/architecture/design-system contracts.

Requirements:
- Add or update targeted route/layout tests for desktop workspace behavior.
- Add sidebar control, state, focus, and reduced-motion tests.
- Add mobile regression tests.
- Preserve and, where needed, extend existing Workflow Details action tests for workspace rendering.
- Update UI documentation cross-references to the sidebar contract without replacing existing canonical documents.
- Perform and record the final acceptance sweep before closing the epic.

Assumptions:
- Documentation cross-reference updates are small canonical-doc edits made after implementation decisions are known.

## Coverage Matrix

- `CLAIM-docs-ui-workflow-workspace-sidebar-a11y-001` -> `STORY-004`, `STORY-005`, `STORY-006`
- `CLAIM-docs-ui-workflow-workspace-sidebar-boundaries-001` -> `STORY-001`, `STORY-007`
- `CLAIM-docs-ui-workflow-workspace-sidebar-controls-001` -> `STORY-002`, `STORY-004`
- `CLAIM-docs-ui-workflow-workspace-sidebar-data-001` -> `STORY-003`
- `CLAIM-docs-ui-workflow-workspace-sidebar-mobile-001` -> `STORY-005`
- `CLAIM-docs-ui-workflow-workspace-sidebar-motion-001` -> `STORY-004`, `STORY-006`
- `CLAIM-docs-ui-workflow-workspace-sidebar-nongoals-001` -> `STORY-001`, `STORY-006`, `STORY-007`
- `CLAIM-docs-ui-workflow-workspace-sidebar-product-001` -> `STORY-001`
- `CLAIM-docs-ui-workflow-workspace-sidebar-purpose-001` -> `STORY-001`
- `CLAIM-docs-ui-workflow-workspace-sidebar-purpose-002` -> `STORY-001`
- `CLAIM-docs-ui-workflow-workspace-sidebar-routes-001` -> `STORY-001`, `STORY-005`
- `CLAIM-docs-ui-workflow-workspace-sidebar-routes-002` -> `STORY-001`, `STORY-005`
- `CLAIM-docs-ui-workflow-workspace-sidebar-security-001` -> `STORY-006`
- `CLAIM-docs-ui-workflow-workspace-sidebar-sequence-001` -> `STORY-001`, `STORY-007`
- `CLAIM-docs-ui-workflow-workspace-sidebar-sidebar-001` -> `STORY-003`
- `CLAIM-docs-ui-workflow-workspace-sidebar-sidebar-002` -> `STORY-003`
- `CLAIM-docs-ui-workflow-workspace-sidebar-states-001` -> `STORY-001`, `STORY-002`
- `CLAIM-docs-ui-workflow-workspace-sidebar-states-002` -> `STORY-001`, `STORY-003`
- `CLAIM-docs-ui-workflow-workspace-sidebar-states-003` -> `STORY-004`
- `CLAIM-docs-ui-workflow-workspace-sidebar-states-004` -> `STORY-003`
- `CLAIM-docs-ui-workflow-workspace-sidebar-testing-001` -> `STORY-007`
- `CLAIM-docs-ui-workflow-workspace-sidebar-url-001` -> `STORY-002`
- `CLAIM-docs-ui-workflow-workspace-sidebar-visual-001` -> `STORY-006`
- `DESIGN-REQ-001` -> `STORY-001`
- `DESIGN-REQ-002` -> `STORY-001`
- `DESIGN-REQ-003` -> `STORY-005`
- `DESIGN-REQ-004` -> `STORY-001`, `STORY-002`
- `DESIGN-REQ-005` -> `STORY-002`
- `DESIGN-REQ-006` -> `STORY-003`
- `DESIGN-REQ-007` -> `STORY-003`
- `DESIGN-REQ-008` -> `STORY-003`
- `DESIGN-REQ-009` -> `STORY-004`
- `DESIGN-REQ-010` -> `STORY-004`
- `DESIGN-REQ-011` -> `STORY-006`
- `DESIGN-REQ-012` -> `STORY-004`, `STORY-006`
- `DESIGN-REQ-013` -> `STORY-004`, `STORY-005`, `STORY-006`
- `DESIGN-REQ-014` -> `STORY-006`
- `DESIGN-REQ-015` -> `STORY-007`
- `DESIGN-REQ-016` -> `STORY-001`
- `DESIGN-REQ-017` -> `STORY-007`
- `DESIGN-REQ-018` -> `STORY-001`, `STORY-005`, `STORY-006`, `STORY-007`
- `DESIGN-REQ-019` -> `STORY-001`, `STORY-007`

## Out Of Scope

- Creating or modifying spec.md files or specs/ directories during breakdown.
- Replacing WorkflowDetailsPage, introducing modal detail, or duplicating detail data/action contracts.
- Implementing code changes for the workspace/sidebar in this breakdown step.
- Creating Jira issues, PRs, implementation plans, or task files in this breakdown step.

## Recommended First Story

`STORY-001` Add desktop workflow workspace shell for detail routes. It establishes the route/presentation boundary that later sidebar, context, mobile, and testing stories depend on.

## Downstream Notes

- No stories contain unresolved `[NEEDS CLARIFICATION]` markers.
- No `spec.md` files or `specs/` directories are created by this breakdown.
- TDD remains the default strategy for downstream `/speckit.plan`, `/speckit.tasks`, and `/speckit.implement`.
- Run `/speckit.verify` after implementation to compare final behavior against the original design preserved through specify.
