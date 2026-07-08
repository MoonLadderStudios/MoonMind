# Workspace Rail & Segmented-Control Unification — Implementation Plan

> **Doc class:** disposable execution scaffolding under `docs/tmp/` (per the *Canonical docs are durable and declarative* principle). Delete when the work lands. All line numbers are anchors against `frontend/src/styles/dashboard.css` at the time of writing and may drift — re-grep the selector before editing.

## Purpose

Three aesthetic fixes for the workflows UI, grouped into two coherent changes:

1. **Q1 — Unify the left rail.** Make the split-view sidebar read as the *same* "Workflow" column as the table's first column (edge-to-edge lines/header, aligned titles), leaving only the status icon and the selection indicator as intentional differences.
2. **Q2 — Stop the create-page content from shifting** when the sidebar is toggled, whenever there is room for both.
3. **Q3 — Unify the two segmented controls** (detail tabs vs. create-page SKILL/TOOL/PRESET) into one system with two intensity tiers.

Q1 and Q2 both modify the shared shell and are shipped together (Solution A). Q3 is independent (Solution B).

## Key facts that shape the plan

- `.workflow-workspace-shell` (dashboard.css ~7245) is the **shared** 2-column grid used by **both** the create page (`workflow-start.tsx`) and the detail page (`workflow-detail.tsx`). Any change affects both — verify both.
- The content card centers with `.dashboard-surface--page` `margin-inline:auto` (~8337) **inside grid column 2**, i.e. within the space left after the sidebar → the ~10.625rem shift.
- `--workflow-start-primary-offset` (~7255) already equals that displacement `(20rem + 1.25rem)/2` and is consumed by the floating submit bar `.queue-floating-bar` (`left: calc(50% + offset)`, ~7264). Treat this var as the single source of truth for "how far content is displaced from viewport center" and drive the bar from it.
- The table reaches the screen edge via `.workflow-list-data-slab .queue-table-wrapper` (~2116): `width: calc(100% + (bleed*2)); margin-inline: calc(bleed * -1)` with `--workflow-list-slab-bleed-inline: 1rem` (~2001), cancelling `.dashboard-root`'s `1rem` inline padding. On mobile the same token is zeroed (~2840). The sidebar has **no** bleed — that is the whole Q1 gap.
- Divider mismatch: table body rows use the generic `th,td { border-bottom: 1px solid rgb(var(--mm-border) / 0.65) }` (~1949); the sidebar rows and both headers use `--workflow-list-divider-color = rgb(var(--mm-border) / 0.72)` (~85).
- The create-page control's CSS is shared **verbatim** with `.settings-nav-*` via grouped selectors (~5512–5720). Editing it also restyles Settings nav.
- Row height (`4rem`), header height (`2.75rem`), header background/underline, and column width (`20rem`) are already shared `--workflow-list-*` tokens and already match between rail and table.

## Open decisions (recommended defaults in **bold**; plan assumes these)

1. **Divider color:** scope the table's row divider to the shared token **just for the workflow list** (add one rule) rather than changing the global `th,td`. *(Bounded scope; other tables keep 0.65.)*
2. **Q2 "enough space" threshold:** **`114rem` (~1832px)** — the width at which the 72rem create card stays viewport-centered *and* a 20rem rail fits the left gutter without overlap. Below it, keep today's in-flow behavior (content shifts). Configurable via one var.
3. **Q3 structure:** **consolidate into one base class + two tier modifiers** (matches the repo's "one canonical path, no accidental duplication" rule and formalizes the currently-implicit `settings-nav` coupling). A lighter no-rename variant is noted as a fallback.
4. **Q3 emphasis motion:** **drop the two infinite animations** (shimmer + scanning border); keep the gradient thumb, glow, and springy slide as interaction-only motion.

---

# Solution A — Unified left rail + stable centered content (Q1 + Q2)

Single change to `.workflow-workspace-shell` and the sidebar; touches create + detail. **CSS-only** (no JSX required for the core).

## Target behavior

- **Wide (≥ threshold):** shell is a 3-track grid `[flexible gutter | content | flexible gutter]` with symmetric gutters, so the content card is **viewport-centered and never moves** when the rail toggles. The rail lives in the left gutter, pinned flush to the inline-start viewport edge (bg/dividers to `x:0`, content indented `1rem` to match the table's first column). Rail stays in normal flow, so its `position: sticky` keeps working.
- **Mid (768px–threshold):** fall back to today's in-flow 2-col grid (content re-centers in the remaining track — the shift returns, which is acceptable "not enough room" behavior). Rail still bleeds flush-left.
- **Mobile (<768px):** unchanged — shell becomes `display:block`, stacked; rail bleed zeroed (mirror the table's `--...-bleed-inline: 0rem` at ~2840).

## Edits

### A1. Rail bleed variables + shared token (dashboard.css)

Add to `.workflow-workspace-shell` (~7245):

```css
--mm-rail-width: var(--workflow-list-column-workflow-width);      /* 20rem */
--mm-rail-bleed: var(--workflow-list-slab-bleed-inline, 1rem);    /* 1rem, matches the table */
--mm-rail-float-min: 114rem;                                      /* Q2 threshold */
--mm-content-max: 66rem;                                          /* detail card cap (default) */
```

Override the content cap for the create page on `.workflow-start-workspace` (~7255):

```css
--mm-content-max: 72rem;
```

### A2. Shell grid → symmetric gutters (wide zone)

Replace the `grid-template-columns` on `.workflow-workspace-shell` (~7247):

```css
.workflow-workspace-shell {
  display: grid;
  grid-template-columns:
    [rail-start] minmax(0, 1fr)
    [content-start] min(var(--mm-content-max), 100% - 2rem)
    [content-end] minmax(0, 1fr);
  align-items: start;
  column-gap: 1.25rem;
  width: 100%;
  max-width: none;
}
```

Place children (sidebar → left gutter, main → center track):

```css
.workflow-workspace-sidebar,
.workflow-workspace-sidebar-slot { grid-column: rail-start; justify-self: start; width: var(--mm-rail-width); }
.workflow-start-primary,
.workflow-workspace-detail       { grid-column: content-start; }
```

(The `.workflow-workspace-sidebar-slot` placeholder is rendered only on the create page — the detail page has no slot — so its rule is harmless where absent.) Because the center track is an explicit `min(cap, 100%-2rem)` between two equal `1fr` gutters, the card is centered on the shell (≈ viewport) regardless of whether the rail slot is present. `.dashboard-surface--page`'s own `margin-inline:auto` becomes redundant but harmless; the detail `.workflow-workspace-detail max-width/margin-inline:auto` (~7492) can stay or be simplified.

### A3. Rail flush-left bleed + content re-indent

On the sidebar container and the elements that paint background/dividers:

```css
.workflow-workspace-sidebar { margin-inline-start: calc(var(--mm-rail-bleed) * -1); }   /* → viewport edge */

/* keep text at 1rem from the viewport edge, matching the table's first-column content */
.workflow-workspace-sidebar-header { padding-inline-start: 1rem; }   /* was 0.75rem (part of 0.5rem 0.75rem) */
.workflow-workspace-sidebar-row    { padding-inline-start: 1rem; }   /* was 0.55rem (part of 0.58rem 0.55rem) */
```

With `width: var(--mm-rail-width)` + `justify-self:start` (from A2), the rail box is exactly `20rem` sitting at the shell's inline-start; the `-1rem` margin shifts its box to `x:0`, so header bg and row dividers span `[0, 20rem]` — identical to the table's first column — while content sits at `1rem`. This holds in both the wide and mid zones because the rail is anchored to the shell's inline-start in both.

### A4. Unify the divider color (decision 1 — scoped)

Add near the workflow-list wrapper cell rules (~2194):

```css
.workflow-list-data-slab .queue-table-wrapper td { border-bottom-color: var(--workflow-list-divider-color); }
```

Now table rows, sidebar rows, and both headers all use `rgb(var(--mm-border) / 0.72)`.

### A5. Threshold fallback (mid zone → current 2-col)

```css
@media (max-width: 114rem) {   /* < var(--mm-rail-float-min): not enough room to keep centered */
  .workflow-workspace-shell {
    grid-template-columns: var(--mm-rail-width) minmax(0, 1fr);
  }
  .workflow-workspace-sidebar,
  .workflow-workspace-sidebar-slot { grid-column: 1; }
  .workflow-start-primary,
  .workflow-workspace-detail       { grid-column: 2; }
}
```

Keep the existing `<768px` block (shell `display:block`) and add `--mm-rail-bleed: 0rem` there so the rail doesn't bleed off-screen when stacked (mirror ~2840).

### A6. Floating submit bar (Q2 dependency — do not skip)

The bar is positioned `left: calc(50% + var(--workflow-start-primary-offset))` (~7264). In the **wide** zone content is viewport-centered, so the displacement is zero. Reuse the offset var as the source of truth:

```css
@media (min-width: 114rem) {                 /* content is viewport-centered here */
  .workflow-start-workspace {
    --workflow-start-primary-offset: 0rem;
    --workflow-start-primary-available-width: calc(100% - 2rem);
  }
}
```

These are the same values the current `data-sidebar-collapsed="true"` override already uses (~7520), so the bar behaves like today's "collapsed" state — centered, full-width-capped. In the mid zone the computed offset still applies and the bar tracks the shifted content. Rework/retire the now-redundant `[data-sidebar-collapsed]` grid-column overrides (~7512–7526): in the float model the content stays in the center track whether or not the rail renders, so only the mid-zone fallback needs the collapse-to-full-width behavior.

## Solution A tests

jsdom (Vitest) cannot verify layout/painting, so unit tests assert **structure/attributes**; geometry is verified manually.

- `frontend/src/entrypoints/workflow-start.test.tsx` — assert the shell renders `data-sidebar-collapsed` toggling and the sidebar slot/rail presence per display mode; assert no class regressions.
- `frontend/src/entrypoints/workflow-detail.test.tsx` — assert the shared shell + rail render on the detail page.
- `frontend/src/entrypoints/workflow-list.test.tsx` — assert the table still renders `queue-table-*` classes (divider change is CSS-only).
- `frontend/src/styles/dashboardBrand.test.ts` — run to confirm no token/brand assertions break.

Manual visual (running app at `cs30:7000`; use before/after screenshots):
- Create page at ≥114rem: toggle the rail → **content card does not move**.
- Table ↔ split view: left column reads continuous — lines to the screen edge, titles aligned, divider colors match; only status icon + selection differ.
- Resize across 114rem and 768px: graceful, no overlap of rail and card.
- Detail page: rail identical; content centered.

## Solution A risks

- **Shared shell** → both pages change; verify create + detail.
- `auto`/`min()` grid track sizing interacts with very wide content inside `main`; the explicit `min(cap, 100%-2rem)` center track avoids blow-out, but confirm long workflow titles/inputs don't force the track wider.
- Sticky rail inside a grid track still works (in-flow); no absolute-positioning fragility introduced.
- Backout: revert the `.workflow-workspace-shell` block + sidebar padding + the one divider rule.

---

# Solution B — Unified segmented-control system (Q3)

Goal: detail tabs and the create toggle become **one family, two tiers** — `quiet` for navigation (detail tabs), `emphasis` for input (create toggle; settings nav). Preserve semantics: detail tabs stay `<a>` + `aria-current` (navigation), the toggle stays `<fieldset>` + native radios (form input).

## Target

- **Base** (shared well, option rhythm, focus ring, border-radius, inset shadow) applies to all three controls.
- **Quiet tier** (`.segmented-nav`, detail): subtle gradient thumb + one soft glow so it reads as the same family, but keep `transform 180ms ease`, mixed-case, and **no perpetual motion**.
- **Emphasis tier** (`.queue-step-type-options`, `.settings-nav-options`): keep the gradient thumb, layered glow, springy `360ms` slide, uppercase — but **remove the two infinite animations**.

## Edits (recommended: consolidate; decision 3)

### B1. Extract a shared base

Group the common container declarations currently duplicated across `.segmented-nav` (~7686) and `.queue-step-type-options, .settings-nav-options` (~5512) into one base rule (new class `mm-segmented` added to all three containers, or a grouped selector if avoiding JSX churn):

- container: `--mm-input-well` bg, `1px solid rgb(var(--mm-border))`, `border-radius: 10px`, inset top/bottom shadows, `isolation: isolate`, `position: relative`, `display: inline-flex`.
- option: flex `1 1 0`, centered, `min-height`, focus-visible → `var(--mm-control-focus-ring)`.

Keep each tier's **thumb** rule separate (they position differently and that's fine): the nav thumb is driven by the React inline var `--segmented-nav-active-index` (`transform: translateX(calc(index * 100%))`, ~7704); the toggle thumb by CSS `:has(... input:checked)` (~5560). No need to unify the positioning mechanism.

### B2. Calm the emphasis tier (decision 4)

- Remove the shimmer animation from `.queue-step-type-options::before` (drop `animation: queue-step-type-thumb-shimmer …`, ~5555) and the `@keyframes` (~5578) if unused elsewhere. **Note:** the same keyframe is referenced a second time in a responsive `.settings-nav-options` block (~5794) — remove/keep both consistently.
- Remove the scanning-border `::after` block and its animation (`.queue-step-type-options::after` ~5593–5621; `@keyframes queue-step-type-scan` ~5623). This is the loudest "always moving" element.
- Keep the gradient thumb fill, the layered `box-shadow` glow, the springy `transition: transform 360ms cubic-bezier(...)`, `backdrop-filter`, and the active icon/label glow. Keep the `prefers-reduced-motion` block (~5632) for the remaining transition.
- **Decouple settings if desired:** if Settings nav should not inherit every emphasis tweak, split `.settings-nav-*` out of the shared thumb rule into its own tier selector at this point (removes the current accidental coupling).

### B3. Bring the quiet tier into the family

On `.segmented-nav::before` (~7704):
- swap the flat `background: rgb(var(--mm-accent) / 0.16)` for a subtle gradient, e.g. `linear-gradient(135deg, rgb(var(--mm-accent) / 0.22), rgb(var(--mm-accent-2) / 0.12))`;
- add one soft glow layer to the existing `box-shadow` (e.g. `, 0 0 10px rgb(var(--mm-accent) / 0.22)`);
- keep `transform: transition 180ms ease`, mixed-case, no infinite animation.

### B4. Minor alignment

Align the "More" trigger height to the tabs: `.td-subroute-more-trigger` `min-height` (~7769) from `2.65rem` → `2.15rem`.

### Lighter alternative (no rename)

If avoiding JSX/test churn for a first pass: skip B1's shared class; do only B2 (calm emphasis), B3 (family-ify quiet), B4. This delivers the visual unification the user asked for; defer the base-class consolidation.

## Solution B tests

- `frontend/src/entrypoints/workflow-detail.test.tsx` — assert tabs render with the nav classes + `aria-current="page"` on the active tab; update class assertions if B1 adds a base class.
- `frontend/src/entrypoints/workflow-start.test.tsx` — assert the step-type control renders the `<fieldset>` + one `<input type="radio">` per option with correct `checked`; update class assertions if renamed.
- `frontend/src/entrypoints/settings.test.tsx` — assert settings nav unaffected/updated.
- Manual visual: detail tabs (calm, same family), create toggle (still the hero, no perpetual shimmer), settings nav, and `prefers-reduced-motion` (no motion).

## Solution B risks

- **`settings-nav` coupling** — verify Settings after any emphasis-tier edit.
- Removing `@keyframes` — grep to confirm no other consumer before deleting.
- Accessibility must be preserved: keep links+`aria-current` for nav and radios+`fieldset` for input.

---

# Sequencing & PR plan

Two focused, non-draft PRs (per repo PR policy). Keep scope bounded — no opportunistic refactors.

1. **PR 1 — Workspace rail + stable centered content (Q1 + Q2).** Solution A. Reviewer focus: shared shell affects create + detail; floating-bar offset; three responsive zones.
2. **PR 2 — Unified segmented control system (Q3).** Solution B. Reviewer focus: settings-nav coupling; motion reduction; a11y preserved.

Optional: Q1's rail bleed + divider token could ship as a tiny pre-PR if incremental delivery is preferred, but bundling with Q2 avoids re-touching the shell twice.

# Testing commands (from AGENTS.md)

Targeted frontend unit runs (jsdom):

```
./tools/test_unit.sh --ui-args frontend/src/entrypoints/workflow-start.test.tsx
./tools/test_unit.sh --ui-args frontend/src/entrypoints/workflow-detail.test.tsx
./tools/test_unit.sh --ui-args frontend/src/entrypoints/workflow-list.test.tsx
./tools/test_unit.sh --ui-args frontend/src/entrypoints/settings.test.tsx
# or, after JS deps are prepared:
npm run ui:test -- frontend/src/styles/dashboardBrand.test.ts
```

These are hermetic unit tests (no Docker, no credentials). No Temporal/workflow-boundary or integration_ci coverage is required — this change is frontend styling/markup only.

# Manual verification checklist (layout is not jsdom-testable)

- [ ] Create @≥114rem: toggling the rail does not move the content card.
- [ ] Table ↔ split view: rail reads as the same column (edge bleed, aligned titles, matching divider color); only status icon + selection differ.
- [ ] Resize across 114rem and 768px: no overlap, graceful fallback.
- [ ] Detail page: rail + centering behave the same as create.
- [ ] Detail tabs feel same-family as the create toggle but calm; create toggle keeps its dynamic slide with no perpetual shimmer/scan.
- [ ] Settings nav unaffected/intended.
- [ ] `prefers-reduced-motion`: no perpetual motion; slides still acceptable.
- [ ] RTL (logical properties used throughout — confirm if RTL is supported).
```