# Dashboard Skills Tab Design Document

Status: Proposed
Owners: MoonMind Engineering
Last Updated: 2026-07-10
Canonical for: Skills workspace layout, shared sidebar adaptation, skill list/detail behavior, and create-skill presentation

**Implementation tracking:** Rollout and backlog notes live under `docs/tmp/` or in gitignored local-only handoffs (for example `artifacts/`), not as migration checklists in canonical `docs/`.

## 1. Purpose

Define the desired design for the **Skills** area in the dashboard: navigation, list/detail UX, API usage, and security expectations so operators can view and create `.agents/skills` entries from the dashboard.

On desktop, `/skills` is a list–detail workspace that reuses the dashboard's existing workspace-sidebar system instead of introducing a second left-navigation pattern. The Skills page uses the same sidebar shell, table-slice geometry, header and filter treatment, active-row styling, scrolling behavior, and loading/empty/error states as the workflow workspace. The entity-specific content changes: the sidebar lists skills, its column header is **Skill**, and the main pane previews or creates skills.

## 2. Goals

- Dedicated dashboard entry point for skills (route `/skills`) in the shared masthead navigation.
- Reuse the geometry and neutral primitives in [`CollectionWorkspaceLayout.md`](CollectionWorkspaceLayout.md).
- Reuse the shared workspace/sidebar foundation defined by [`WorkflowWorkspaceSidebar.md`](WorkflowWorkspaceSidebar.md) and [`WorkflowListDisplayModes.md`](WorkflowListDisplayModes.md); do not create a parallel Skills-only sidebar implementation or copy its CSS.
- List skills in the shared sidebar and preview the selected skill's `SKILL.md` content (Markdown → HTML) in the main pane.
- Create flow: name + Markdown body persisted under `.agents/skills/local/{name}/SKILL.md` while the skills sidebar remains available.
- `POST /api/workflows/skills` creates an on-disk skill; `GET /api/workflows/skills` lists skills and, when complete, returns enough data for sidebar and detail views.
- New skills participate in the same skill selection surfaces as other dashboard flows once written.

## 3. User Interface Design

### 3.1 Navigation

- Top-level **Skills** destination in the shared masthead → `/skills`, using the same icon/label, active, hover, focus, tooltip, and responsive behavior as Workflows and Recurring.
- The Skills sidebar is page-local content navigation. It complements rather than replaces the top-level **Skills** route entry.
- Reusing the sidebar does not make Skills part of the workflow `hidden` / `sidebar` / `table` display-mode state machine. Unless that system is explicitly generalized later, `/skills` owns its own skills list and does not show workflow-list controls or workflow rows.

### 3.2 Desktop Workspace Layout

- `/skills` uses the shared `CollectionWorkspace`: the Skills sidebar is the first workspace column, lists skills only, and the primary pane is its sibling.
- The Skills sidebar is always present for desktop preview and create states; it is not optional Workflow list-display state.
- The sidebar starts at the dashboard content region's far-left edge and is never inside a centered/max-width page wrapper. It follows the shared sidebar width, divider, row-height, and independent-scroll behavior.
- The primary pane uses the remaining width for the selected skill preview or create form. Readable-width constraints may be applied inside the pane, but not around the entire split workspace.
- The left side must not render a bespoke **Available Skills** card or a vertical stack of primary/secondary buttons as a second navigation design.
- **View mode:** the selected skill renders `SKILL.md` as HTML using the shared Markdown component. Rendered, raw, and metadata views may remain tabs inside the primary pane.
- **Create mode:** **Create New Skill** opens the create surface in the primary pane or an existing page-owned drawer while the shared skills sidebar stays mounted. Saving refreshes the skills query and selects the newly created skill.

### 3.3 Skills Sidebar Adaptation Contract

The Skills page reuses the sidebar's entity-neutral structure and supplies skill-specific data and copy.

| Sidebar concern | Skills value |
| --- | --- |
| Column header | **Skill** |
| Row primary text | Canonical skill name, or skill title with canonical name as secondary text when both are available |
| Data source | The Skills list query backed by `GET /api/workflows/skills` |
| Filter title | **Skill filter** |
| Filter field label | **Skill** |
| Filter placeholder | **Filter skills** |
| Region label | **Skill navigation** |
| Table-slice label | **Skill list table slice** |
| Pinned active-row label, when needed | **Current skill** |

Rules:

1. The sidebar must reuse the same shared shell, header row, filter affordance, row metric, active-state treatment, divider, focus styling, and loading/empty/error presentation used by the app sidebar.
2. All workflow-specific visible and accessible copy must be parameterized. The Skills page must not announce `Workflow`, `Workflow navigation`, or `Workflow filter`.
3. Sidebar rows contain skills, not workflows. They must not render workflow status icons, execution state, workflow IDs, or workflow actions unless a future skill-specific contract defines equivalent data.
4. Selecting a row updates the skill shown in the primary pane and applies the shared active-row styling. Same-route selection uses appropriate selected-item semantics; a future route-backed skill detail link may use `aria-current="page"`.
5. Filtering narrows the skill rows without clearing the selected detail. If the selected skill is outside the filtered result window, the shared pinned-row behavior must show **Current skill** rather than implying the skill matches the filter.
6. Sidebar query failure must not erase an already loaded skill preview, and detail-content failure must not erase a successfully loaded skill list.
7. **Create New Skill** is a page action, not a replacement for the sidebar's **Skill** column header. Place it in the Skills page header, the primary pane, or a shared utility slot that preserves the standard header row.

### 3.4 Shared Component Boundary

- The reusable sidebar structure should be entity-neutral and accept labels, query states, row content, active identity, filter behavior, and navigation/selection callbacks as inputs.
- The workflow adapter continues to supply workflow rows, status presentation, workflow URLs, and workflow-specific copy.
- The Skills adapter supplies skill rows, skill selection, skill-specific copy, and the Skills query.
- Do not import a workflow-specific data panel and mutate its results into skills. Share the presentational primitive and keep workflow and skill data adapters separate.
- Do not fork `workflow-workspace-sidebar-*` styling into a parallel Skills-only visual system. Shared styles should move behind neutral component classes or shared tokens where necessary.

### 3.5 Visual Style and Responsive Behavior

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
