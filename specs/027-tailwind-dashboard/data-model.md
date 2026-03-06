# Data Model: Tailwind Style System Phase 2

## ThemeTokens
- **Purpose**: Central palette definitions consumed by Tailwind + vanilla CSS.
- **Fields**:
  - `--mm-bg`, `--mm-panel`, `--mm-ink`, `--mm-muted`, `--mm-border` (base surfaces/typography)
  - `--mm-accent`, `--mm-accent-2`, `--mm-accent-warm` (primary/secondary highlights)
  - `--mm-ok`, `--mm-warn`, `--mm-danger` (status hues)
  - `--mm-shadow` (box-shadow string reused across components)
- **Relationships**: Referenced by `SurfaceStyle` and `StatusStyle` entities; future Tailwind config will also map these tokens for utility classes.

## SurfaceStyle
- **Purpose**: Semantic selectors (masthead, panels, nav pills, cards, buttons) composed from theme tokens and consistent blur/radius/shadow rules.
- **Attributes**:
  - `selector`: `.masthead`, `.panel`, `.route-nav a`, `.card`, `.btn`
  - `background`: mixture of `rgb(var(--mm-panel) / alpha)` and gradient overlays
  - `border`: `rgb(var(--mm-border) / alpha)`
  - `effects`: `box-shadow: var(--mm-shadow)` and optional glow states
- **Relationships**: Consumes `ThemeTokens`; ensures `dashboard.js` semantic classes remain stable.

## StatusStyle
- **Purpose**: Normalized badge styles for queued/running/awaiting_action/succeeded/failed/cancelled states.
- **Attributes**:
  - `status`: enumerated state name from `dashboard_view_model`
  - `color`: token reference (`--mm-warn`, `--mm-accent-2`, etc.)
  - `background`: translucent fill derived from the same token (14% alpha)
  - `border`: `rgb(var(--token) / 0.35)` for clarity on glass surfaces
- **Relationships**: Bound to the `.status.status-{state}` classes emitted by `dashboard.js`.

## BuildArtifact
- **Purpose**: Tracks generated CSS output to ensure reproducibility.
- **Attributes**:
  - `source`: `dashboard.tailwind.css`
  - `output`: `dashboard.css`
  - `script`: `npm run dashboard:css:min`
  - `distribution`: `/static/task_dashboard/dashboard.css`
- **Relationships**: Dependent on `ThemeTokens` staying in sync with docs.
