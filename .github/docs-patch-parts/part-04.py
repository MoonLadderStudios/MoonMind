Desktop primary navigation uses a persistent far-left application rail, not viewport-centered masthead pills. The content region begins immediately to its right. When a collection sidebar is present, it is the content region's first column and the primary pane starts immediately after it.

The application rail, collection sidebar, and primary pane are siblings at their respective shell/workspace layers. Do not place either rail inside a constrained or centered page shell. Constrained and data-wide widths apply only inside the primary pane.

The application rail and every collection sidebar share token-driven glass/matte surfaces, border light, icon sizing, hover brightening, visible focus, active state, tooltip posture, and responsive collapse behavior. Workflows, Recurring, and Skills use the same collection-sidebar component family.

''', path)
text = replace_section(text, "### 10.1 Masthead and navigation", "### 10.2 Buttons", r'''### 10.1 Application rail and collection sidebars

Top-level destinations render in the far-left application rail. Active links are unmistakable, hover/focus states brighten, icons and labels use common metrics, and utilities occupy a consistent lower/terminal region.

Collection sidebars reuse one header, filter, row, selected state, divider, scrolling container, pinned-current row, and loading/empty/error treatment. They must read as collapsed table slices rather than card stacks. Entity-specific copy and data are adapters; CSS and interaction primitives are shared.

The rail and sidebar must remain coherent without liquidGL. Use glass only for bounded control shells and matte/satin row interiors for sustained scanning.

''', path)
text = replace_section(text, "### 11.3 Workflow detail and evidence-heavy pages", "---\n\n## 12. Accessibility and performance", r'''### 11.3 Workflow and recurring schedule detail pages

Workflow detail and Recurring schedule detail use the shared `EntityDetailFrame` defined by `docs/UI/CollectionWorkspaceLayout.md`.

Shared composition includes breadcrumb context, title/subtitle/status cluster, primary and overflow actions, summary/facts strip, tabs or section navigation, main content slab, optional facts rail, and localized loading/error states. Typography, spacing, status chips, action placement, tabs, facts-rail geometry, and responsive stacking must match.

Workflow adapters supply execution progress, evidence, logs, artifacts, remediation, and recovery. Recurring adapters supply cadence, next run, policy, configuration, and run history. The sidebar remains a workspace sibling at the far-left content edge; it is never owned by or centered with the detail frame.

Do not allow glass effects to compete with evidence density.

---

## 12. Accessibility and performance''', path)
save(path, text)

# List display modes and workflow workspace.
path = "docs/UI/WorkflowListDisplayModes.md"
text = load(path)
text = text.replace("Last updated: 2026-07-03", f"Last updated: {DATE}", 1)
text = replace_once(text, "- `docs/UI/DashboardSPAArchitecture.md` remains canonical", "- `docs/UI/CollectionWorkspaceLayout.md` is canonical for the far-left application rail, shared collection-sidebar primitive, and workspace geometry.\n- `docs/UI/DashboardSPAArchitecture.md` remains canonical", path)
control = r'''## 6. Shell/workspace list display control

The list display selector belongs to the current collection's shell/workspace utility region. It must remain adjacent to collection context without becoming a centered masthead element or moving into page content, the collection sidebar, or a table toolbar.

```text
[far-left application rail] [collection sidebar when visible] [collection utility + primary pane]
```

The control remains one accessible radio group with `No list`, `Sidebar list`, and `Full screen table` options using `Square`, `PanelLeft`, and `Rows3`. The selected option reflects resolved route state; keyboard users enter with Tab and use arrow keys. On routes without a declared contract, hide it. On mobile, hide desktop-only modes until a mobile contract exists.

The shell supplies common control styling and placement; each collection supplies its accessible name and route resolution. Workflow and Recurring preferences remain separate.

---

'''
text = replace_section(text, "## 6. Masthead radio control", "## 7. Surface composition", control, path)
