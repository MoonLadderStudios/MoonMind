text = replace_once(text, "The recurring schedule detail page should be implemented as a schedule-flavored derivative of the workflow detail page.", "The recurring schedule detail page is a schedule adapter rendered through the same `EntityDetailFrame` as Workflow detail. The Recurring sidebar is a workspace sibling at the far-left content edge immediately right of the application rail; it is never mounted inside the frame or a centered/max-width wrapper.", path)
text = replace_section(text, "## 6. Default Detail Layout", "## 7. Data Contract", r'''## 6. Default Detail Layout

```text
┌──────────────────┬──────────────────────────┬───────────────────────────────────────────┐
│ Application rail │ Recurring sidebar        │ Shared EntityDetailFrame                  │
│ viewport far-left│ content-region far-left  │ breadcrumb + title/state + actions        │
│                  │                          │ summary/facts + tabs + main + facts rail  │
└──────────────────┴──────────────────────────┴───────────────────────────────────────────┘
```

The frame uses the same header spacing, panel rhythm, status family, action placement, summary strip, tabs, facts rail, loading states, error states, and responsive stacking as Workflow detail. Schedule-specific tabs remain Overview, Runs, Configuration, and optional Activity. Workflow-only steps, artifacts, logs, and remediation are not copied onto the schedule definition.

---

''', path)
text = replace_once(text, "- Do not make schedule details a separate product surface with a unique design system.", "- Do not make schedule details a separate product surface with a unique design system.\n- Do not render the Recurring sidebar inside the detail frame or a centered/max-width wrapper with a large left margin.", path)
save(path, text)

# One-shot transport files are not part of the final documentation diff.
for transient in [ROOT / ".github/workflows/docs-m1-shared-layout-patch.yml", *sorted((ROOT / ".github/docs-patch-parts").glob("part-*.py"))]:
    if transient.exists():
        transient.unlink()
parts_dir = ROOT / ".github/docs-patch-parts"
if parts_dir.exists():
    try:
        parts_dir.rmdir()
    except OSError:
        pass

print("Updated Milestone 1 and shared dashboard declarative contracts.")
