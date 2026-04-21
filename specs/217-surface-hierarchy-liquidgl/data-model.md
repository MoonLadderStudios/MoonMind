# Data Model: Surface Hierarchy and liquidGL Fallback Contract

This story does not introduce persisted data. The relevant runtime model is a UI styling contract.

## Surface Role

- `matte-data`: Dense data, logs, evidence, tables, and nested dense panels. Near-opaque and crisp.
- `satin-form`: Inputs, textareas, selects, and editing surfaces. Slightly dimensional but grounded.
- `glass-control`: Elevated controls, nav rails, utility bars, and toolbars. Token-driven glass with fallback.
- `liquidgl-hero`: Explicit bounded liquidGL-enhanced targets. Full CSS shell with standard glass fallback.
- `accent-live`: Active, executing, selected, and critical states. Accent energy without page-wide dominance.

## State Rules

- `glass-control` falls back to near-opaque glass when `backdrop-filter` is unsupported.
- `liquidgl-hero` is opt-in and never inherited by default `.panel` or `.card` surfaces.
- Dense and editing surfaces remain readable in light and dark themes.
- Nested dense surfaces use quieter, more opaque weights than their parent shells.
