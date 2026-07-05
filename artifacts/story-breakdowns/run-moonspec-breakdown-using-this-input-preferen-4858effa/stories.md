# MoonSpec Story Breakdown: Workflow List Display Modes

- Source: `docs/UI/WorkflowListDisplayModes.md`
- Source document class: `canonical-declarative`
- Source Jira issue key: `MM-1111`
- Output mode: `jira`
- Coverage gate: PASS - every major design point is owned by at least one story.

## Design Summary

The source defines a shared workflow-list display system for Workflows and Create surfaces with three mutually exclusive modes: hidden, sidebar, and table. It establishes typed state resolution, route behavior, first-workflow fallback, masthead controls, surface composition, sidebar/table visual continuity, persistence, data reuse, accessibility, scoped failure states, future extension rules, and a testing contract. The canonical source remains the document above; this breakdown is a temporary Jira-ready derived view.

## Canonical Claims

- `CLAIM-docs-ui-workflowlistdisplaymodes-001` (1. Purpose) - Workflow navigation is represented as three mutually exclusive modes: hidden, sidebar, and table, initially for Workflows and Create surfaces.
- `CLAIM-docs-ui-workflowlistdisplaymodes-002` (2. Relationship to existing UI contracts) - This contract narrows older sidebar controls and composes with existing dashboard, list, detail, create, and workspace UI contracts.
- `CLAIM-docs-ui-workflowlistdisplaymodes-003` (3. Declarative model) - The UI uses typed display mode, surface, selection, registry, and resolved display models instead of scattered page booleans.
- `CLAIM-docs-ui-workflowlistdisplaymodes-004` (4. Route and presentation matrix) - Each supported route family has deterministic required route results, primary surfaces, and list surfaces for every selected mode.
- `CLAIM-docs-ui-workflowlistdisplaymodes-005` (5. First-workflow fallback) - When a detail target is required and no workflow is selected, the system resolves route selection, remembered selection, then first row from the current effective list, or keeps the table empty state.
- `CLAIM-docs-ui-workflowlistdisplaymodes-006` (6. Masthead radio control) - The global masthead contains an accessible icon radio group for No list, Sidebar list, and Full screen table on participating pages.
- `CLAIM-docs-ui-workflowlistdisplaymodes-007` (7. Surface composition) - Hidden, sidebar, and table modes compose with Workflow Detail and Create surfaces without changing page-owned content contracts or hiding stale panes.
- `CLAIM-docs-ui-workflowlistdisplaymodes-008` (8. Sidebar-as-table-slice visual contract) - The sidebar renders as the Workflows table first-column slice with shared row metrics, header styling, divider styling, semantics, and state frame.
- `CLAIM-docs-ui-workflowlistdisplaymodes-009` (9. Motion and continuity) - Mode changes preserve row geometry, selected row or scroll/focus context, shell continuity, and reduced-motion behavior.
- `CLAIM-docs-ui-workflowlistdisplaymodes-010` (10. State persistence) - Dashboard preferences remember explicit mode and last selected workflow while keeping canonical URLs and avoiding unsafe query payloads.
- `CLAIM-docs-ui-workflowlistdisplaymodes-011` (11. Data fetching and cache reuse) - Sidebar and table share the authorized workflow list API/cache model while detail and Create data remain independent and focus-safe.
- `CLAIM-docs-ui-workflowlistdisplaymodes-012` (12. Accessibility requirements) - The mode selector, sidebar navigation, route-changing interactions, active rows, mobile behavior, and draft warnings are accessible.
- `CLAIM-docs-ui-workflowlistdisplaymodes-013` (13. Empty, loading, and error states) - Table, sidebar, first-workflow, detail, and Create state failures are scoped recoverably without blanking unrelated primary surfaces.
- `CLAIM-docs-ui-workflowlistdisplaymodes-014` (14. Future extension model) - Future pages can opt in only through explicit surface contracts; unsupported modes are not accidentally exposed.
- `CLAIM-docs-ui-workflowlistdisplaymodes-015` (15. Testing contract) - Implementation coverage must verify masthead controls, routing, fallbacks, sidebar visuals, continuity, failure isolation, keyboard behavior, reduced motion, and authorization boundaries.

## Coverage Points

- `DESIGN-REQ-001` (requirement, 1. Purpose) - Three-mode workflow list system: Expose hidden, sidebar, and table as the only workflow list display modes for Workflows and Create.
- `DESIGN-REQ-002` (constraint, 2. Relationship to existing UI contracts) - Existing UI contract precedence: Keep page-specific contracts canonical and replace older separate sidebar controls with the shared masthead mode control where this design applies.
- `DESIGN-REQ-003` (state-model, 3. Declarative model) - Typed mode state machine: Model requested mode, effective mode, surface, route action, primary surface, and list surface through typed data-first definitions.
- `DESIGN-REQ-004` (requirement, 4. Route and presentation matrix) - Route presentation resolution: Resolve each mode selection from table, detail, subroute, and Create routes into deterministic navigation and composition outcomes.
- `DESIGN-REQ-005` (state-model, 5. First-workflow fallback) - First-workflow fallback: Use route ID, authorized remembered selection, then current-list first visible row; never guess on empty or failed lists.
- `DESIGN-REQ-006` (requirement, 6. Masthead radio control) - Masthead radio group: Place an accessible Lucide icon radio group beside the MoonMind title with canonical labels and radio semantics.
- `DESIGN-REQ-007` (requirement, 7. Surface composition) - Mode-specific surface composition: Compose hidden/sidebar/table with Workflow Detail and Create without moving page-owned behavior into the list layer or keeping hidden stale panes.
- `DESIGN-REQ-008` (requirement, 8. Sidebar-as-table-slice visual contract) - Sidebar table-slice visuals: Render the sidebar as a one-column table slice sharing header, body row, divider, width, semantics, and metric tokens with the Workflows table.
- `DESIGN-REQ-009` (requirement, 9. Motion and continuity) - Continuity and motion: Make sidebar/table switching feel like expanding or collapsing the same list while respecting reduced motion.
- `DESIGN-REQ-010` (constraint, 10. State persistence) - Preference persistence and URL safety: Persist explicit mode and selected workflow while preserving canonical URLs and excluding unsafe or bulky query payloads.
- `DESIGN-REQ-011` (integration, 11. Data fetching and cache reuse) - Shared list data model: Share authorized workflow list API/cache data between table and sidebar while fetching detail and Create independently.
- `DESIGN-REQ-012` (requirement, 12. Accessibility requirements) - Accessibility: Ensure keyboard order, names, checked state, focus after navigation, active row semantics, and accessible draft warnings.
- `DESIGN-REQ-013` (requirement, 13. Empty, loading, and error states) - Scoped loading/error/empty states: Keep table, sidebar, first-workflow, detail, and Create failures scoped with available recovery and mode controls.
- `DESIGN-REQ-014` (integration, 14. Future extension model) - Future surface contracts: Require future pages to declare support before exposing workflow list modes, and keep workflow entities targeted at the Workflows table.
- `DESIGN-REQ-015` (requirement, 15. Testing contract) - Verification expectations: Test routing, controls, visuals, continuity, state isolation, accessibility, reduced motion, and authorization boundaries.

## Stories

### STORY-001: Implement the workflow list display state model and route resolution

- Short name: `list-mode-resolution`
- Source reference: `docs/UI/WorkflowListDisplayModes.md`
- Sections: 1. Purpose, 2. Relationship to existing UI contracts, 3. Declarative model, 4. Route and presentation matrix
- Claim IDs: `CLAIM-docs-ui-workflowlistdisplaymodes-001`, `CLAIM-docs-ui-workflowlistdisplaymodes-002`, `CLAIM-docs-ui-workflowlistdisplaymodes-003`, `CLAIM-docs-ui-workflowlistdisplaymodes-004`
- Coverage IDs: `DESIGN-REQ-001`, `DESIGN-REQ-002`, `DESIGN-REQ-003`, `DESIGN-REQ-004`
- Dependencies: None

As a dashboard user, I need the selected workflow list display mode to resolve predictably on Workflows, Workflow Detail, detail subroutes, and Create so changing modes never strands me on an invalid or empty primary surface.

Independent test: Unit-test the resolver with every matrix row and verify requestedMode, effectiveMode, routeAction, primarySurface, and listSurface for Workflows, detail/subroute, and Create inputs.

Acceptance criteria:
- A typed WorkflowListDisplayMode registry exposes hidden, sidebar, and table with canonical labels, icons, and list regions.
- The resolver returns table mode as the Workflows list primary surface whenever /workflows is effective.
- Selecting hidden or sidebar from /workflows resolves to a selected or first workflow detail when one can be opened.
- Selecting table from any detail route or from Create navigates to /workflows and unmounts non-table primary panes.
- Older covered-page sidebar controls are replaced by the shared mode model instead of remaining as separate controls.

Requirements:
- Represent list display through requested/effective mode data rather than page-local booleans.
- Apply the route and presentation matrix exactly for /workflows, /workflows/{workflowId} subroutes, and /workflows/new.
- Preserve detail subroutes when switching only between hidden and sidebar on detail routes.

Assumptions:
- Existing route helpers can be extended without changing canonical URL shapes.

### STORY-002: Add safe first-workflow fallback and selection memory

- Short name: `first-workflow-fallback`
- Source reference: `docs/UI/WorkflowListDisplayModes.md`
- Sections: 4. Route and presentation matrix, 5. First-workflow fallback, 10. State persistence, 11. Data fetching and cache reuse
- Claim IDs: `CLAIM-docs-ui-workflowlistdisplaymodes-004`, `CLAIM-docs-ui-workflowlistdisplaymodes-005`, `CLAIM-docs-ui-workflowlistdisplaymodes-010`, `CLAIM-docs-ui-workflowlistdisplaymodes-011`
- Coverage IDs: `DESIGN-REQ-004`, `DESIGN-REQ-005`, `DESIGN-REQ-010`, `DESIGN-REQ-011`
- Dependencies: `STORY-001`

As a user switching away from the full table, I need MoonMind to open the current or remembered workflow when authorized, otherwise the first visible list row, without guessing or leaking unauthorized workflows.

Independent test: Mock current route, session preferences, authorized detail response, list cache, list loading, list failure, and empty list cases; verify the selected target and no-navigation outcomes.

Acceptance criteria:
- Fallback resolution prefers route workflow ID, then last explicitly selected authorized workflow, then first visible row from the current effective list query.
- When the matching list is loading, the UI exposes a resolving state such as Opening first workflow...
- When the list fails or has no rows, the UI stays on /workflows and does not navigate to a guessed workflow ID.
- Remembered selections absent from active filters may open only after detail authorization succeeds and are labeled as current rather than filter-matching.
- The fallback never exposes unauthorized workflows in remembered, first-row, sidebar, table, or pinned-current states.

Requirements:
- Persist lastSelectedWorkflowId from explicit user workflow selections.
- Use the exact current list query or matching cache for first-row fallback.
- Keep detail data authorization separate from list row presence.

Assumptions:
- Authorization can be confirmed through existing detail API behavior.

### STORY-003: Render the masthead workflow list display radio group

- Short name: `masthead-mode-control`
- Source reference: `docs/UI/WorkflowListDisplayModes.md`
- Sections: 6. Masthead radio control, 12. Accessibility requirements
- Claim IDs: `CLAIM-docs-ui-workflowlistdisplaymodes-006`, `CLAIM-docs-ui-workflowlistdisplaymodes-012`
- Coverage IDs: `DESIGN-REQ-006`, `DESIGN-REQ-012`
- Dependencies: `STORY-001`

As a keyboard and pointer user, I need a single masthead radio control for workflow list visibility so I can change between No list, Sidebar list, and Full screen table from participating pages.

Independent test: Render the dashboard masthead on Workflows, detail, and Create surfaces; assert control placement, role, accessible name, radio options, icons, checked state, keyboard operation, focus styles, and mobile hiding behavior.

Acceptance criteria:
- The control appears immediately after the MoonMind brand area and before route navigation on participating desktop pages.
- The control uses Square, PanelLeft, and Rows3 Lucide icons with No list, Sidebar list, and Full screen table labels/tooltips.
- The control exposes radiogroup semantics named Workflow list display and each option announces label plus checked state.
- Tab enters the group, arrow keys change options, and visible focus styling follows dashboard control tokens.
- Non-participating pages hide or disable the control, with hiding preferred, and mobile users do not encounter unusable desktop-only sidebar controls.

Requirements:
- Use one radio group rather than unrelated buttons.
- Reflect the resolved mode after navigation settles.
- Provide deterministic focus behavior after route-changing selections.

Assumptions:
- The dashboard masthead component already centralizes brand and route navigation layout.

### STORY-004: Compose hidden, sidebar, and table surfaces for Detail and Create

- Short name: `surface-composition`
- Source reference: `docs/UI/WorkflowListDisplayModes.md`
- Sections: 7. Surface composition, 13. Empty, loading, and error states
- Claim IDs: `CLAIM-docs-ui-workflowlistdisplaymodes-007`, `CLAIM-docs-ui-workflowlistdisplaymodes-013`
- Coverage IDs: `DESIGN-REQ-007`, `DESIGN-REQ-013`
- Dependencies: `STORY-001`, `STORY-003`

As a dashboard user, I need each mode to compose with Workflow Detail and Create without disrupting those pages own data, draft, tab, action, or error behavior.

Independent test: Exercise detail and Create routes in hidden/sidebar/table mode with successful, loading, empty, and error list/detail/create states; assert primary surfaces remain or unmount exactly as specified.

Acceptance criteria:
- Hidden detail renders Workflow Details alone with the selected workflow ID still sourced from the route.
- Sidebar detail renders the list on the left and existing detail surface on the right; sidebar failures do not erase authorized detail content.
- Hidden Create renders Create alone and keeps form behavior owned by the Create page contract.
- Sidebar Create renders workflow navigation beside Create, keeps Create usable, and sidebar row clicks navigate to workflow detail after draft preservation rules are respected.
- Table mode renders /workflows only, with no hidden detail pane or Create form left mounted.

Requirements:
- Own sidebar composition in the workspace/layout layer rather than in detail or Create page bodies.
- Keep detail, Create, and list error recovery scoped to their own data regions.
- Respect Create draft preservation or unsaved-change confirmation before route-changing mode selections complete.

Assumptions:
- Create draft preservation is defined by the existing Create page contract referenced by the source document.

### STORY-005: Build sidebar list as a Workflows table slice

- Short name: `sidebar-table-slice`
- Source reference: `docs/UI/WorkflowListDisplayModes.md`
- Sections: 8. Sidebar-as-table-slice visual contract, 9. Motion and continuity
- Claim IDs: `CLAIM-docs-ui-workflowlistdisplaymodes-008`, `CLAIM-docs-ui-workflowlistdisplaymodes-009`
- Coverage IDs: `DESIGN-REQ-008`, `DESIGN-REQ-009`
- Dependencies: `STORY-004`

As a user moving between sidebar and full table, I need the sidebar to look and behave like the first column of the Workflows table so the mode change feels continuous.

Independent test: Visual/component-test the table and sidebar variants with matching header row, body row heights, divider styling, active row, loading/empty/error frame, reduced motion, and scroll/focus preservation behavior.

Acceptance criteria:
- The sidebar has a header row labeled Workflow before any selectable workflow rows.
- Sidebar header typography, casing, background, border, padding, and non-hover behavior match the full table first-column header.
- Sidebar and table share row metric tokens for header height, body row height, workflow column width, divider width, and divider color.
- Sidebar rows have the same block size as full table rows and clamp titles or compact supplements without increasing row height.
- Switching between sidebar and table preserves row geometry, aligns the divider, avoids large page-slide animations, and respects reduced-motion settings.

Requirements:
- Prefer shared row/frame primitives capable of rendering table and sidebar variants.
- Preserve table-equivalent semantics if div/grid markup is required.
- Do not present the sidebar as cards, a menu, or an unrelated navigation rail.

Assumptions:
- The existing Workflows table exposes reusable row data sufficient for a one-column variant.

### STORY-006: Persist workflow list preferences and reuse authorized list data

- Short name: `preferences-data-reuse`
- Source reference: `docs/UI/WorkflowListDisplayModes.md`
- Sections: 10. State persistence, 11. Data fetching and cache reuse
- Claim IDs: `CLAIM-docs-ui-workflowlistdisplaymodes-010`, `CLAIM-docs-ui-workflowlistdisplaymodes-011`
- Coverage IDs: `DESIGN-REQ-010`, `DESIGN-REQ-011`
- Dependencies: `STORY-001`, `STORY-002`

As a returning dashboard user, I need MoonMind to remember my list display intent and reuse safe list data without polluting URLs or interrupting work in Detail or Create.

Independent test: Test direct visits and mode changes with persisted preferences, query parameters, matching and non-matching caches, live updates, and focus-sensitive Create/detail interactions.

Acceptance criteria:
- Dashboard preferences persist the last explicit workflowListDisplayMode and lastSelectedWorkflowId.
- Direct desktop visits to detail default to persisted hidden/sidebar mode, otherwise sidebar; direct Create visits default to persisted hidden/sidebar mode, otherwise hidden.
- Direct visits to /workflows resolve to table regardless of persisted preference until the user selects another mode.
- Workflow detail URLs do not include hidden/sidebar mode, and query parameters never include raw prompts, full drafts, secrets, presigned URLs, logs, artifacts, or large detail payloads.
- Sidebar and table share the authorized workflow list API/cache model, while list refetches and live updates preserve active selection, row height, and focus.

Requirements:
- Represent full table mode naturally through /workflows.
- Reuse cached Workflows list data only when query parameters match.
- Keep selected detail and Create data independent from list data.

Assumptions:
- Dashboard preferences already have a durable client-side or server-backed storage mechanism.

### STORY-007: Add opt-in future surface contracts and coverage tests

- Short name: `surface-contract-tests`
- Source reference: `docs/UI/WorkflowListDisplayModes.md`
- Sections: 14. Future extension model, 15. Testing contract, 12. Accessibility requirements
- Claim IDs: `CLAIM-docs-ui-workflowlistdisplaymodes-014`, `CLAIM-docs-ui-workflowlistdisplaymodes-015`, `CLAIM-docs-ui-workflowlistdisplaymodes-012`
- Coverage IDs: `DESIGN-REQ-014`, `DESIGN-REQ-015`, `DESIGN-REQ-012`
- Dependencies: `STORY-001`, `STORY-003`, `STORY-004`, `STORY-005`, `STORY-006`

As a maintainer, I need future pages to opt into workflow list modes explicitly and the current implementation to have regression tests for the routing, UI, accessibility, visual, and authorization contract.

Independent test: Run the targeted frontend test suite covering the source testing contract plus a contract test proving non-participating pages do not expose the masthead control or sidebar modes accidentally.

Acceptance criteria:
- A surface contract declares whether each future page supports hidden, sidebar, and table modes before the masthead control appears there.
- Unsupported modes are not enabled, and pages outside Workflows/Create do not receive accidental workflow sidebars.
- Future non-workflow lists may reuse visual patterns only with their own entity, route, and selection semantics.
- Tests cover the 22 listed behaviors from masthead rendering through authorization boundaries.
- The Workflows table remains the canonical table target for workflow entities.

Requirements:
- Keep future extension as a declaration, not implicit route sniffing.
- Preserve accessibility test coverage for keyboard operation, focus, active row state, hidden mobile controls, and draft warnings.
- Include authorization boundary tests for table rows, sidebar rows, first-row fallback, remembered selections, and pinned current rows.

Assumptions:
- Frontend tests can assert Lucide icon usage through accessible labels, component props, or stable test identifiers if SVG internals are brittle.

## Coverage Matrix

- `CLAIM-docs-ui-workflowlistdisplaymodes-001` -> `STORY-001`
- `CLAIM-docs-ui-workflowlistdisplaymodes-002` -> `STORY-001`
- `CLAIM-docs-ui-workflowlistdisplaymodes-003` -> `STORY-001`
- `CLAIM-docs-ui-workflowlistdisplaymodes-004` -> `STORY-001`, `STORY-002`
- `CLAIM-docs-ui-workflowlistdisplaymodes-005` -> `STORY-002`
- `CLAIM-docs-ui-workflowlistdisplaymodes-006` -> `STORY-003`
- `CLAIM-docs-ui-workflowlistdisplaymodes-007` -> `STORY-004`
- `CLAIM-docs-ui-workflowlistdisplaymodes-008` -> `STORY-005`
- `CLAIM-docs-ui-workflowlistdisplaymodes-009` -> `STORY-005`
- `CLAIM-docs-ui-workflowlistdisplaymodes-010` -> `STORY-002`, `STORY-006`
- `CLAIM-docs-ui-workflowlistdisplaymodes-011` -> `STORY-002`, `STORY-006`
- `CLAIM-docs-ui-workflowlistdisplaymodes-012` -> `STORY-003`, `STORY-007`
- `CLAIM-docs-ui-workflowlistdisplaymodes-013` -> `STORY-004`
- `CLAIM-docs-ui-workflowlistdisplaymodes-014` -> `STORY-007`
- `CLAIM-docs-ui-workflowlistdisplaymodes-015` -> `STORY-007`
- `DESIGN-REQ-001` -> `STORY-001`
- `DESIGN-REQ-002` -> `STORY-001`
- `DESIGN-REQ-003` -> `STORY-001`
- `DESIGN-REQ-004` -> `STORY-001`, `STORY-002`
- `DESIGN-REQ-005` -> `STORY-002`
- `DESIGN-REQ-006` -> `STORY-003`
- `DESIGN-REQ-007` -> `STORY-004`
- `DESIGN-REQ-008` -> `STORY-005`
- `DESIGN-REQ-009` -> `STORY-005`
- `DESIGN-REQ-010` -> `STORY-002`, `STORY-006`
- `DESIGN-REQ-011` -> `STORY-002`, `STORY-006`
- `DESIGN-REQ-012` -> `STORY-003`, `STORY-007`
- `DESIGN-REQ-013` -> `STORY-004`
- `DESIGN-REQ-014` -> `STORY-007`
- `DESIGN-REQ-015` -> `STORY-007`

Gate result: PASS - every major design point is owned by at least one story.
