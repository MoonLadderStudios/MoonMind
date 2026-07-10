- [ ] **1.9 UI regression coverage** — test far-left geometry, common sidebar anatomy, required Recurring/Skills sidebars, shared Workflow/Recurring detail regions, direct deep links, localized failures, and mobile accessibility.

**Done means:** every major area is reachable from one far-left application rail; Workflow, Recurring, and Skills use the same sidebar shell at the content edge; Workflow and recurring schedule detail pages share a recognizable detail frame; no split workspace is centered with a large left margin; and routes, preferences, accessibility, and mobile fallbacks remain correct.
'''
text = replace_section(text, "## Milestone 1 —", "## Milestone 2 —", milestone + "\n---\n\n", path)
save(path, text)

# Canonical collection doc.
save("docs/UI/CollectionWorkspaceLayout.md", COLLECTION_DOC)

# SPA shell contract.
path = "docs/UI/DashboardSPAArchitecture.md"
text = load(path)
text = text.replace("Last Updated: 2026-06-29", f"Last Updated: {DATE}", 1)
text = replace_once(text, "Related: `docs/UI/WorkflowConsoleArchitecture.md`", "Related: `docs/UI/CollectionWorkspaceLayout.md`, `docs/UI/WorkflowConsoleArchitecture.md`", path)
shell = r'''## 8. Shell, application rail, and collection workspaces

The React-owned `DashboardShell` provides global providers plus a persistent application rail. On desktop, that rail is the first column at the viewport's far-left edge; it contains the brand and top-level links, including Workflows, Create, Recurring, and Skills. Primary navigation is not a centered masthead pill row.

Immediately to the right, the dashboard content region hosts route-family workspaces. A collection workspace may render a collection sidebar as its first column and a primary pane as its second. The workspace grid is fluid and must not be nested in a centered/max-width page wrapper. Readable-width limits belong inside the primary pane.

`docs/UI/CollectionWorkspaceLayout.md` is canonical for geometry, shared sidebar anatomy, and the common Workflow/Recurring detail frame. Navigation uses router-native links and route-derived active state. List-display controls for participating collections live in the shell/workspace utility area associated with that collection.

Required shell primitives include `ApplicationRail`, `DashboardContent`, `CollectionWorkspace`, `CollectionSidebar`, and `EntityDetailFrame`. Workflows, Recurring, and Skills supply adapters rather than copying layout or CSS.

On tablet/mobile the rail and collection sidebar may collapse into drawers or list-to-detail flows. Non-rendered desktop controls must be absent from the accessibility tree.

---

'''
text = replace_section(text, "## 8. Shell and navigation", "## 9. Capability and endpoint discovery", shell, path)
text = replace_once(text, "2. The dashboard navigation is React-owned.", "2. The dashboard navigation is React-owned and renders as the far-left desktop application rail.", path)
text = replace_once(text, "12. Tests cover client routing, direct deep links, API fallback exclusion, navigation, query persistence, and feature-gated routes.", "12. Tests cover client routing, direct deep links, API fallback exclusion, navigation, query persistence, and feature-gated routes.\n13. Workflows, Recurring, and Skills use the shared collection-sidebar primitive at the content edge.\n14. Workflow and Recurring detail routes use the shared entity-detail frame and are never wrapped with their sidebar inside a centered page container.", path)
text = replace_once(text, "- active nav state;", "- active application-rail state and shared Workflows/Recurring/Skills navigation;\n- far-left application-rail and collection-sidebar geometry;\n- shared Workflow/Recurring detail-frame composition;", path)
save(path, text)

# Design system.
path = "docs/UI/DashboardDesignSystem.md"
text = load(path)
text = text.replace("Last updated: 2026-06-28", f"Last updated: {DATE}", 1)
text = replace_once(text, "For route ownership, runtime config, API boundaries, and workflow console architecture, see `docs/UI/WorkflowConsoleArchitecture.md`.", "For application-rail placement, shared collection sidebars, and the common entity-detail frame, see `docs/UI/CollectionWorkspaceLayout.md`. For route ownership, runtime config, API boundaries, and workflow console architecture, see `docs/UI/WorkflowConsoleArchitecture.md`.", path)
text = replace_section(text, "### 7.2 Header architecture", "### 7.3 Control deck + data slab pattern", r'''### 7.2 Application rail and workspace geometry

