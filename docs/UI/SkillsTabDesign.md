# Dashboard Skills Tab Design Document

Status: Adopted
Owners: MoonMind Engineering
Last Updated: 2026-07-11
Canonical for: Skills workspace layout, shared sidebar adaptation, skill list/detail behavior, and create-skill presentation

**Implementation tracking:** Rollout and backlog notes live under `docs/tmp/` or in gitignored local-only handoffs (for example `artifacts/`), not as migration checklists in canonical `docs/`.

## 1. Purpose

Define the desired design for the **Skills** area in the dashboard: navigation, list/detail UX, API usage, and security expectations so operators can view and create `.agents/skills` entries from the dashboard.

On desktop, `/skills` is a list–detail workspace that reuses the dashboard's existing workspace-sidebar system instead of introducing a second left-navigation pattern. The Skills page uses the same sidebar shell, table-slice geometry, header and filter treatment, active-row styling, scrolling behavior, and loading/empty/error states as the workflow workspace. The entity-specific content changes: the sidebar lists skills, its column header is **Skill**, and the main pane previews or creates skills.

## 2. Goals

- Dedicated dashboard entry point for skills (route `/skills`) reachable from the **System** menu.
- Reuse the geometry and neutral primitives in [`CollectionWorkspaceLayout.md`](CollectionWorkspaceLayout.md).
- Reuse the shared workspace/sidebar foundation defined by [`WorkflowWorkspaceSidebar.md`](WorkflowWorkspaceSidebar.md) and [`WorkflowListDisplayModes.md`](WorkflowListDisplayModes.md); do not create a parallel Skills-only sidebar implementation or copy its CSS.
- List skills in the shared sidebar and preview the selected skill's `SKILL.md` content (Markdown → HTML) in the main pane.
- Create flow: name + Markdown body persisted under `.agents/skills/local/{name}/SKILL.md` while the skills sidebar remains available.
- `POST /api/workflows/skills` creates an on-disk skill; `GET /api/workflows/skills` lists skills and, when complete, returns enough data for sidebar and detail views.
- New skills participate in the same skill selection surfaces as other dashboard flows once written.

## 3. User Interface Design

### 3.1 Navigation

- **Skills** is a System destination: the permanent primary masthead contains only **Workflows**, **Create**, and the **System** trigger, and Skills appears (with Recurring) inside the System menu under the **Workflow resources** section.
- `/skills` and `/skills/{skillId}` mark the **System** trigger active; the selected child link is active inside the open menu.
- On mobile, Recurring and Skills render as normal inline links in the mobile System section — never a nested popover inside the drawer.
- The Skills sidebar is page-local content navigation. It complements rather than replaces the **Skills** route entry.
- Skills participates in the shared collection list-display system with its own independent state (see 3.2). Selecting a Skills display mode never changes Workflow or Recurring state or preferences.

### 3.2 Routes and Display Modes

Selection is route-derived:

- `/skills` — the canonical full Skills catalog table (route-owned `table` surface).
- `/skills/{skillId}` — the selected skill preview/detail.

Route parsing rejects malformed percent encoding, encoded slashes, empty IDs, and extra path segments.

The masthead exposes the shared three-option radio group named **Skills list display** on Skills routes, with **No list**, **Sidebar list**, and **Full table** options. The persisted preferences are `skillsListDisplayMode` (default `table`) and `lastSelectedSkillId` (default empty), independent of the Workflow and Recurring preferences.

| Current route | No list | Sidebar list | Full table |
| --- | --- | --- | --- |
| `/skills` with available skills | Open the remembered or first visible skill without a sidebar | Open the remembered or first visible skill with the sidebar | Stay on the catalog table |
| `/skills` with no available skills | Stay on the empty table and announce that no skill can be opened | Same | Stay on the empty table |
| `/skills/{skillId}` | Stay on the detail route and hide the sidebar | Stay on the detail route and show the sidebar | Navigate to `/skills` |
| `/skills/{staleId}` | Localized not-found state without guessing another route | Usable catalog sidebar plus a localized not-found detail state | Navigate to `/skills` |

Rules: a direct `/skills` visit is always the table; a direct detail visit honors persisted `hidden`/`sidebar` and coerces a persisted `table` to `sidebar` without redirecting; a remembered skill is verified against the catalog (and cleared when stale) before it is used to leave the table; the desktop-only control stays out of the mobile accessibility tree.

### 3.3 Desktop Workspace Layout

- `/skills` uses the fluid/data-wide shell so the workspace and full table are not constrained by the centered panel width.
- The full catalog table renders with the shared table primitives; its first column shares the sidebar rail width so switching modes reads as the same list collapsing or expanding. Minimum columns: Skill (label with canonical ID as secondary text), Description, Source, Inputs, Content, and an Open action linking to the canonical detail route. The table owns its empty, loading, and error states.
- Detail routes use the shared `CollectionWorkspace` with the neutral `collection-workspace--edge-rail` geometry: the Skills sidebar is the first workspace column and the primary pane is its sibling.
- The sidebar starts at the dashboard content region's far-left edge and is never inside a centered/max-width page wrapper. It follows the shared sidebar width, divider, row-height, and independent-scroll behavior.
- The primary pane uses the remaining width for the selected skill preview or create form. Readable-width constraints may be applied inside the pane, but not around the entire split workspace.
- The left side must not render a bespoke **Available Skills** card or a vertical stack of primary/secondary buttons as a second navigation design.
- **View mode:** the selected skill renders `SKILL.md` as HTML using the shared Markdown component. Rendered, raw, and metadata views may remain tabs inside the primary pane.
- **Create mode:** **Create New Skill** opens the create drawer while the current workspace stays mounted. Saving or uploading refreshes the skills query, records `lastSelectedSkillId`, and navigates to the new skill's detail route.
- The catalog table, sidebar, and detail all consume one normalized catalog model from the same `GET /api/workflows/skills` query, and one filter value applies to both the table and the sidebar.

### 3.4 Skills Sidebar Adaptation Contract

The Skills page reuses the sidebar's entity-neutral structure and supplies skill-specific data and copy.

| Sidebar concern | Skills value |
| --- | --- |
| Column header | **Skill** |
| Row primary text | Skill label when available, otherwise the canonical skill ID |
| Data source | The Skills list query backed by `GET /api/workflows/skills` |
| Filter dialog title | **Skill filter** |
| Filter field label | **Skill** |
| Filter placeholder | **Filter skills** |
| Filter trigger, idle | **Skill sidebar filter. No filter applied.** |
| Filter trigger, active | Announces the current filter value |
| Reset action | **Reset skill sidebar filter** |
| Apply action | **Apply skill sidebar filter** |
| Region label | **Skill navigation** |
| Table-slice label | **Skill list table slice** |
| Pinned active-row label, when needed | **Current skill** |

The filter reuses the shared filter-icon/popover header (the same `ListFilter` trigger, focus-into-field, `Escape`/outside-click close with focus restoration, disabled-when-empty reset, and apply/close behavior as the Workflow sidebar). The search field is not visible by default.

Rules:

1. The sidebar must reuse the same shared shell, header row, filter affordance, row metric, active-state treatment, divider, focus styling, and loading/empty/error presentation used by the app sidebar.
2. All workflow-specific visible and accessible copy must be parameterized. The Skills page must not announce `Workflow`, `Workflow navigation`, or `Workflow filter`.
3. Sidebar rows contain skills, not workflows. They must not render workflow status icons, execution state, workflow IDs, or workflow actions unless a future skill-specific contract defines equivalent data.
4. Rows are router links to the canonical `/skills/{skillId}` detail route — flat rectangular table slices, never pill-shaped primary buttons. The active route exposes `aria-current="page"`, keeps a visible focus state, and includes a non-color-only active indicator. Titles clamp within the shared row height and long IDs truncate or wrap without growing the row.
5. Filtering narrows the skill rows without clearing the selected detail. If the selected skill is outside the filtered result window, the shared pinned-row behavior must show **Current skill** rather than implying the skill matches the filter.
6. Sidebar query failure must not erase an already loaded skill preview, and detail-content failure must not erase a successfully loaded skill list.
7. **Create New Skill** is a page action, not a replacement for the sidebar's **Skill** column header. Place it in the Skills page header, the primary pane, or a shared utility slot that preserves the standard header row.

### 3.5 Shared Component Boundary

- The reusable sidebar structure should be entity-neutral and accept labels, query states, row content, active identity, filter behavior, and navigation/selection callbacks as inputs.
- The workflow adapter continues to supply workflow rows, status presentation, workflow URLs, and workflow-specific copy.
- The Skills adapter supplies skill rows, skill selection, skill-specific copy, and the Skills query.
- Do not import a workflow-specific data panel and mutate its results into skills. Share the presentational primitive and keep workflow and skill data adapters separate.
- Do not fork `workflow-workspace-sidebar-*` styling into a parallel Skills-only visual system. Shared styles should move behind neutral component classes or shared tokens where necessary.

### 3.6 Visual Style and Responsive Behavior

- Follow [`DashboardDesignSystem.md`](DashboardDesignSystem.md): `queue-submit-primary` for primary save, `mm-glass` / `mm-glass-strong` containers, and `markdown-body` or equivalent prose styling for preview (dark-mode safe).
- The desktop sidebar should visually match the app sidebar closely enough that switching between Workflows and Skills changes the data and header, not the navigation pattern.
- Below the shared desktop workspace breakpoint (initially `lg`), where the sidebar and primary pane cannot both remain usable, use a single-column Skills list/detail presentation or mobile list-to-detail transition. Preserve the **Skill** header, filtering, active selection, and accessible names rather than squeezing the desktop rail beside the preview.

## 4. API & Backend

### 4.1 Endpoints

- **`POST /api/workflows/skills`** — body `{ "name", "markdown" }`; `201` on success; `400` on validation (name conflict, invalid name).
- **`GET /api/workflows/skills`** — list skills; extended to return file content for detail view when implementation is complete (see tracker).

### 4.2 Storage

- Handler writes `.agents/skills/local/{name}/SKILL.md`, creating directories as needed (see `AGENTS.md` shared skills runtime).

## 5. Frontend

- Route `/skills` in the dashboard shell (`react_dashboard.html` template + client routing).
- Compose the page from the shared workspace-sidebar primitive plus a Skills-specific data adapter and the existing Skills primary pane.
- Client: fetch the skills list, render sidebar rows, select a skill, render its preview, submit the create form to `POST /api/workflows/skills`, refresh the list, and select the new skill on success.
- Preserve the selected skill and primary-pane state across sidebar refetches when that skill still exists.

## 6. Accessibility

- The sidebar landmark has the accessible name **Skill navigation**.
- The filter control, dialog, field, reset action, and apply action use skill-specific names.
- Every skill row is keyboard reachable and has a visible focus state.
- The active skill is exposed with selection semantics appropriate to the row interaction model.
- Loading, empty, filtered-empty, and error states are announced without moving focus unexpectedly.

## 7. Security

- Reject path traversal in `name` (`../`, absolute paths).
- Render Markdown with the same safety posture as other user-editable dashboard content.
