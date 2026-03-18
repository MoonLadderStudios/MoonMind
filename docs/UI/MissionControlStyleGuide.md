# Mission Control Style Guide

Status: Active  
Owners: MoonMind Engineering  
Last Updated: 2026-03-18

## 1. Purpose

Style reference for the MoonMind Mission Control UI: Tailwind CSS integration, design tokens, dark mode, component recipes (buttons, glass, status chips), responsive patterns, and visual direction.

This document is both:

- A source of truth for what is already in production.
- A design and implementation guide for upcoming UI polish.

For architecture, routes, runtime config, and Temporal integration, see `docs/UI/MissionControlArchitecture.md`.

## 2. Background / Current State

The dashboard currently ships as:

- Template: `api_service/templates/task_dashboard.html`
- Renderer: `api_service/static/task_dashboard/dashboard.js`
- Source CSS: `api_service/static/task_dashboard/dashboard.tailwind.css`
- Served CSS: `api_service/static/task_dashboard/dashboard.css`

Current implementation status:

- [x] Tailwind + PostCSS toolchain exists.
- [x] `dashboard.css` is generated from `dashboard.tailwind.css`.
- [x] Tokenized light palette is live (`--mm-*` tokens).
- [x] Purple/cyan/pink visual refresh is live for light mode.
- [x] Existing semantic class names remain stable (`masthead`, `panel`, `route-nav`, `status-*`, etc).
- [x] Dark token overrides are live in CSS.
- [x] Manual theme toggle is present in template/JS with persistence.
- [x] Mobile queue/table refinements are live (queue cards on sub-768px viewports, tables preserved on ≥768px).

Accuracy note: `tailwind.config.cjs` still uses `darkMode: "class"`, and Phase 3 now ships a `.dark` token scope plus first-paint bootstrap and runtime preference resolution.

## 3. Goals and Non-Goals

### 3.1 Goals

1. Keep public asset path stable:
   - Continue serving `/static/task_dashboard/dashboard.css`.
2. Keep migration low-risk:
   - No framework rewrite.
   - No class-name churn in `dashboard.js` output.
3. Deliver a modern 2026 visual style:
   - Dark mode first-class.
   - Purple-forward brand direction.
   - Warm yellow/orange highlight signals where useful.
   - Liquid-glass depth with practical performance.
4. Maintain operational readability:
   - High contrast for logs, tables, forms, status chips, and focus states.

### 3.2 Non-Goals

- Introducing React/Vue/Svelte or a JS bundler.
- Rewriting routing, auth, or API contracts.
- Replacing semantic classes with Tailwind utility strings in JS-rendered HTML.

## 4. Visual Direction: MoonMind 2026 (Dark, Futuristic, Glass)

MoonMind UI should feel like an operations console:

- Futuristic: clean typography, high signal-to-noise, restrained glow.
- Purple-forward: violet is the identity color for action and selection.
- Warm highlights: amber/orange for warnings, urgent callouts, and high-attention states.
- Liquid glass: translucent layers with blur and edge-light, never muddy.
- Readable first: data density and contrast take priority over effects.

### 4.1 Recommended Palette and Role Mapping

Use token values as RGB triplets so alpha blending remains flexible.

| Role | Token | Light (current) | Dark (target) | Notes |
| --- | --- | --- | --- | --- |
| App background | `--mm-bg` | `248 247 255` | `8 8 16` | Dark theme keeps slight violet bias |
| Panel surface | `--mm-panel` | `255 255 255` | `20 18 34` | Glass base for cards/panels |
| Primary text | `--mm-ink` | `18 20 32` | `237 236 255` | Keep strong readability |
| Muted text | `--mm-muted` | `95 102 122` | `174 170 204` | For metadata only |
| Borders | `--mm-border` | `214 220 235` | `92 84 136` | Slightly luminous in dark |
| Primary accent | `--mm-accent` | `139 92 246` | `167 139 250` | Core purple |
| Secondary accent | `--mm-accent-2` | `34 211 238` | `125 249 255` | Live/streaming/system energy |
| Warm accent | `--mm-accent-warm` | `244 114 182` | `249 115 22` | Orange emphasis in dark mode |
| Success | `--mm-ok` | `34 197 94` | `74 222 128` | Success state |
| Warning / hot highlight | `--mm-warn` | `245 158 11` | `251 191 36` | Primary yellow/orange signal |
| Error | `--mm-danger` | `244 63 94` | `251 113 133` | Failure/cancelled state |

Highlight usage guidance:

- Keep purple as the dominant accent (roughly 60-70% of accent usage).
- Use yellow/orange highlights sparingly (roughly 10-20%) for signal hierarchy.
- Reserve cyan for live/running/stream feedback.

### 4.2 Liquid Glass Rules (Practical)

1. Use blur on a small set of structural surfaces only:
   - `masthead`, major `panel`, optional sticky nav strip.
2. Keep interior data containers more opaque than outer shells.
3. Use border light and shadow depth, not heavy glow floods.
4. Do not animate blur.
5. Provide fallback when `backdrop-filter` is unavailable.

Recommended ranges:

- Glass opacity: `0.55` to `0.78`
- Border alpha: `0.35` to `0.75`
- Blur radius: `14px` to `20px`
- Glow alpha: `<= 0.45`

## 5. Styling Approach

### 5.1 Keep Semantic Class Names

Continue styling existing classes used by template and JS:

- `dashboard-root`, `masthead`, `route-nav`, `panel`, `card`, `toolbar`, `grid-2`
- Status chips: `status`, `status-queued`, `status-running`, `status-awaiting_action`, `status-succeeded`, `status-failed`, `status-cancelled`

This keeps JS stable and avoids dynamic utility-generation problems.

### 5.2 Compile into Existing Output File

Build flow:

- Input: `api_service/static/task_dashboard/dashboard.tailwind.css`
- Output: `api_service/static/task_dashboard/dashboard.css`

Do not hand-edit `dashboard.css` during normal development.

### 5.3 Token-First Theming

- Theme behavior should be controlled by tokens (`--mm-*`).
- Semantic classes consume tokens.
- Dark mode flips tokens via `.dark`; components adapt automatically.

## 6. Tailwind Configuration

### 6.1 `tailwind.config.cjs`

Current config expectations:

- `content` includes template and dashboard JS.
- `darkMode: "class"` is enabled.
- `corePlugins.preflight` remains `false` to avoid reset regressions.
- Token colors map to `rgb(var(--mm-*) / <alpha-value>)`.

Reference snippet:

```js
module.exports = {
  content: [
    "./api_service/templates/task_dashboard.html",
    "./api_service/static/task_dashboard/**/*.js",
  ],
  darkMode: "class",
  corePlugins: { preflight: false },
  theme: {
    extend: {
      colors: {
        "mm-bg": "rgb(var(--mm-bg) / <alpha-value>)",
        "mm-panel": "rgb(var(--mm-panel) / <alpha-value>)",
        "mm-ink": "rgb(var(--mm-ink) / <alpha-value>)",
        "mm-muted": "rgb(var(--mm-muted) / <alpha-value>)",
        "mm-border": "rgb(var(--mm-border) / <alpha-value>)",
        "mm-accent": "rgb(var(--mm-accent) / <alpha-value>)",
        "mm-accent-2": "rgb(var(--mm-accent-2) / <alpha-value>)",
        "mm-accent-warm": "rgb(var(--mm-accent-warm) / <alpha-value>)",
        "mm-ok": "rgb(var(--mm-ok) / <alpha-value>)",
        "mm-warn": "rgb(var(--mm-warn) / <alpha-value>)",
        "mm-danger": "rgb(var(--mm-danger) / <alpha-value>)",
      },
    },
  },
};
```

### 6.2 `postcss.config.cjs`

```js
module.exports = {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
};
```

## 7. Theme Tokens and Liquid-Glass Foundations

### 7.1 Current Shipped Light Tokens (accurate as of 2026-02-19)

```css
:root {
  color-scheme: light;
  --mm-bg: 248 247 255;
  --mm-panel: 255 255 255;
  --mm-ink: 18 20 32;
  --mm-muted: 95 102 122;
  --mm-border: 214 220 235;
  --mm-accent: 139 92 246;
  --mm-accent-2: 34 211 238;
  --mm-accent-warm: 244 114 182;
  --mm-ok: 34 197 94;
  --mm-warn: 245 158 11;
  --mm-danger: 244 63 94;
  --mm-shadow: 0 18px 32px -26px rgb(10 8 30 / 0.55);
}
```

### 7.2 Dark Token Overrides

```css
.dark {
  color-scheme: dark;
  --mm-bg: 8 8 16;
  --mm-panel: 20 18 34;
  --mm-ink: 237 236 255;
  --mm-muted: 174 170 204;
  --mm-border: 92 84 136;
  --mm-accent: 167 139 250;
  --mm-accent-2: 125 249 255;
  --mm-accent-warm: 249 115 22;
  --mm-ok: 74 222 128;
  --mm-warn: 251 191 36;
  --mm-danger: 251 113 133;
  --mm-shadow: 0 24px 52px -34px rgb(0 0 0 / 0.88);
}
```

### 7.3 Atmospheric Background Layers

Keep dark mode visually rich, not flat black.

```css
body {
  background:
    radial-gradient(circle at 8% 0%, rgb(var(--mm-accent) / 0.18), transparent 44%),
    radial-gradient(circle at 98% 0%, rgb(var(--mm-accent-2) / 0.14), transparent 42%),
    radial-gradient(circle at 50% 100%, rgb(var(--mm-accent-warm) / 0.10), transparent 52%),
    rgb(var(--mm-bg));
}

.dark body {
  background:
    radial-gradient(circle at 12% -6%, rgb(var(--mm-accent) / 0.36), transparent 46%),
    radial-gradient(circle at 95% -8%, rgb(var(--mm-accent-2) / 0.22), transparent 44%),
    radial-gradient(circle at 50% 112%, rgb(var(--mm-warn) / 0.18), transparent 56%),
    rgb(var(--mm-bg));
}
```

### 7.4 Core Glass Utilities

```css
@layer components {
  .mm-glass {
    border: 1px solid rgb(var(--mm-border) / 0.55);
    background: rgb(var(--mm-panel) / 0.62);
    box-shadow: var(--mm-shadow);
    backdrop-filter: blur(18px) saturate(130%);
    -webkit-backdrop-filter: blur(18px) saturate(130%);
  }

  .mm-glass-strong {
    border: 1px solid rgb(var(--mm-border) / 0.72);
    background: rgb(var(--mm-panel) / 0.74);
    box-shadow: var(--mm-shadow), 0 0 0 1px rgb(var(--mm-accent) / 0.20);
  }

  @supports not ((backdrop-filter: blur(2px)) or (-webkit-backdrop-filter: blur(2px))) {
    .mm-glass,
    .mm-glass-strong {
      background: rgb(var(--mm-panel) / 0.92);
    }
  }
}
```

## 8. Dark Mode Implementation

### 8.1 Template Updates (`task_dashboard.html`)

1. Update viewport:

```html
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
```

2. Add a toggle in the masthead controls:

```html
<button class="theme-toggle secondary" type="button" aria-label="Toggle dark mode">
  Theme
</button>
```

### 8.2 Theme Preference Logic (`dashboard.js`)

Use local preference first, system preference second.

```js
function initTheme() {
  const key = "moonmind.theme";
  const root = document.documentElement;
  const media = window.matchMedia?.("(prefers-color-scheme: dark)");
  const stored = localStorage.getItem(key);

  const apply = (mode) => {
    root.classList.toggle("dark", mode === "dark");
    root.dataset.theme = mode;
  };

  if (stored === "dark" || stored === "light") {
    apply(stored);
  } else {
    apply(media?.matches ? "dark" : "light");
  }

  const button = document.querySelector(".theme-toggle");
  button?.addEventListener("click", () => {
    const next = root.classList.contains("dark") ? "light" : "dark";
    apply(next);
    localStorage.setItem(key, next);
  });

  media?.addEventListener?.("change", (event) => {
    if (!localStorage.getItem(key)) {
      apply(event.matches ? "dark" : "light");
    }
  });
}
```

Call `initTheme()` near the top of the dashboard IIFE.

### 8.3 Prevent Theme Flash on Load

Set the theme class before CSS paints by adding a tiny inline script in `<head>`:

```html
<script>
(() => {
  const key = "moonmind.theme";
  const stored = localStorage.getItem(key);
  const prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  const useDark = stored ? stored === "dark" : prefersDark;
  if (useDark) document.documentElement.classList.add("dark");
})();
</script>
```

### 8.4 Styling Strategy

- Prefer token swaps and semantic classes.
- Avoid scattering many `dark:*` utility variants in JS-generated markup.
- Keep focus indicators highly visible in both themes.

## 9. Component Recipes (Modern + Glass + Purple)

### 9.1 Containers

- `masthead` and primary `panel` use `mm-glass` or `mm-glass-strong`.
- Inner cards remain less transparent than outer shell.
- Keep content rhythm tight but readable (`0.75rem` to `1rem` spacing).

### 9.2 Typography

- Continue `IBM Plex Sans` for body, `Barlow Condensed` for headline, and `IBM Plex Mono` for logs/code.
- Slight uppercase + tracking for overline labels only.
- Avoid thin weights on glass backgrounds.

### 9.3 Navigation Pills (Glass + Glow + Grow)

Navigation pills (`.route-nav a`) should feel like small glass chips:

- Always slightly translucent so the background subtly influences the pill.
- Hover/active should get brighter and add a restrained glow (never darker).
- Motion uses scale (grow), not translateY (no "rising" effect).

Recommended interaction rules:

- Base: translucent panel fill + thin border.
- Hover/active: slightly higher fill alpha + edge glow + `transform: scale(1.02)`.
- Active text must remain high-contrast in both themes.

### 9.4 Buttons (Glow + Grow System)

Buttons must follow one consistent interaction model across:
Add Step, Apply, Create, Promote, Dismiss, Cancel, Back, etc.

#### 9.4.1 Principles

1. Hover gets lighter/brighter (never darker).
2. All buttons are slightly translucent so underlying glass/gradients influence them.
3. Glow is driven by the button's "action color" (purple for default, green for create/commit, red for danger).
4. Motion is scale-based:
   - Hover: grow (for example, `scale(1.03)`).
   - Active/press: subtle press via scale (for example, `scale(0.99)`), still no translate.

#### 9.4.2 Variants and When to Use Them

- Default (brand action): purple (`--mm-accent`) for most actions.
- Secondary: glass surface for safe/navigation actions (Cancel, Back, View Details).
- Commit/Create: green (`--mm-action-primary`) for "this will enqueue/apply a real action" flows (Create, Promote-to-task).
- Danger: red (`--mm-danger`) for destructive actions (Dismiss, Cancel job).

Implementation note (current pattern):

- Commit/danger actions use a shared action-button class that sets `--queue-action-color` and derives fill/glow from it.
- `--queue-action-color` defaults to `--mm-action-primary`; danger overrides set it to `--mm-danger`.

#### 9.4.3 Interaction State Contract (Must Be True for All Variants)

Base (idle):

- Background uses alpha < 1.0 (recommended `0.80-0.92`).
- Thin outline present (1px) so the button edge stays readable on glass.

Hover:

- Increase brightness by increasing alpha and/or adding a subtle white highlight overlay.
- Add a glow using the action color.
- `transform: scale(...)` only (no translateY).

Active (pressed):

- Slight scale down (still scale only).
- Glow can reduce slightly to feel pressed in.

Focus-visible:

- Keep a clear outline ring (high contrast in both themes).

Disabled:

- Lower opacity and disable transform/glow.

#### 9.4.4 Thin Outline Rationale

A 1px outline plus edge-light inset highlight keeps buttons legible on:

- bright glass panels,
- dark mode glass,
- gradient backgrounds.

Without the outline, buttons visually blend into the panel surface.

#### 9.4.5 CSS Recipe (`dashboard.tailwind.css`)

Use this as the source-of-truth interaction recipe when editing `api_service/static/task_dashboard/dashboard.tailwind.css`:

```css
/* Button interaction system: Glow + Grow (no translateY) */

:root {
  --mm-btn-alpha: 0.88;
  --mm-btn-alpha-hover: 0.94;   /* hover gets brighter (more opaque), not darker */
  --mm-btn-alpha-active: 0.90;
  --mm-btn-scale-hover: 1.03;
  --mm-btn-scale-active: 0.99;
}

/* Use a single "action color" concept for glow/focus.
   queue-action already uses --queue-action-color; others fall back to --mm-accent. */
button,
.button {
  --mm-btn-color: var(--queue-action-color, var(--mm-accent));
  background: rgb(var(--mm-btn-color) / var(--mm-btn-alpha));
  transform: scale(1);
  will-change: transform;
  transition: transform 140ms cubic-bezier(.2,.8,.2,1), box-shadow 140ms ease, border-color 140ms ease, background 140ms ease;
}

button:hover,
.button:hover {
  background: rgb(var(--mm-btn-color) / var(--mm-btn-alpha-hover));
  box-shadow:
    0 0 0 1px rgb(255 255 255 / 0.28),
    0 0 0 4px rgb(var(--mm-btn-color) / 0.16),
    0 18px 34px -18px rgb(var(--mm-btn-color) / 0.60);
  transform: scale(var(--mm-btn-scale-hover));
}

button:active,
.button:active {
  background: rgb(var(--mm-btn-color) / var(--mm-btn-alpha-active));
  transform: scale(var(--mm-btn-scale-active));
}

/* Secondary stays glassy + translucent */
button.secondary,
.button.secondary {
  --mm-btn-color: var(--mm-panel);
  background: rgb(var(--mm-panel) / 0.72);
  border-color: rgb(var(--mm-border) / 0.85);
}

button.secondary:hover,
.button.secondary:hover {
  background: rgb(var(--mm-panel) / 0.78);
  border-color: rgb(var(--mm-accent) / 0.85);
  box-shadow:
    0 0 0 1px rgb(var(--mm-accent) / 0.28),
    0 18px 34px -22px rgb(var(--mm-accent) / 0.40);
}

/* Action buttons (Create/Promote/Dismiss) keep their gradient,
   but hover must get brighter + glow, and use scale not translate. */
.queue-action,
.queue-submit-primary {
  background:
    linear-gradient(150deg,
      rgb(var(--queue-action-color) / 0.86) 0%,
      rgb(var(--queue-action-color) / 0.96) 100%);
}

.queue-action:hover,
.queue-submit-primary:hover {
  background:
    linear-gradient(150deg,
      rgb(var(--queue-action-color) / 0.94) 0%,
      rgb(var(--queue-action-color)) 100%);
  transform: scale(var(--mm-btn-scale-hover));
}

/* Reduced motion */
@media (prefers-reduced-motion: reduce) {
  button,
  .button {
    transition: none;
    transform: none !important;
  }
}
```

#### 9.4.6 Optional Dynamic Edge-Light Outline (Liquid Glass Vibe)

CSS cannot truly vary border width around a standard rectangle. Use gradient border opacity to create the perceived uneven edge thickness:

```css
/* Optional: perceived uneven outline via gradient border overlay */
.mm-liquid-edge {
  position: relative;
}

.mm-liquid-edge::before {
  content: "";
  position: absolute;
  inset: 0;
  border-radius: inherit;
  padding: 1px; /* actual thickness stays 1px */
  background: conic-gradient(
    from 135deg,
    rgb(255 255 255 / 0.55),
    rgb(255 255 255 / 0.10) 25%,
    rgb(255 255 255 / 0.55) 50%,
    rgb(255 255 255 / 0.10) 75%,
    rgb(255 255 255 / 0.55)
  );
  -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
  mask-composite: exclude;
  pointer-events: none;
  opacity: 0.9;
}

@supports not ((-webkit-mask-composite: xor) or (mask-composite: exclude)) {
  .mm-liquid-edge::before { display: none; }
}
```

### 9.5 Status Chips

Map status consistently:

- `queued` -> amber/yellow (`--mm-warn`)
- `running` -> cyan (`--mm-accent-2`)
- `awaiting_action` -> purple (`--mm-accent`)
- `succeeded` -> green (`--mm-ok`)
- `failed/cancelled` -> rose/red (`--mm-danger`)

Keep chip fills translucent and borders slightly stronger than fills.

### 9.6 Live Output Pane

For `.queue-live-output`:

- Dark translucent background even in light mode.
- Mono font + slightly elevated line-height.
- Subtle inset border and optional faint purple edge.
- Preserve long-line wrapping and scanability.

## 10. Responsive and Mobile-Friendly System

### 10.1 Layout Targets

- Mobile: 360-430px, single-column first.
- Tablet: 768px, selective two-column layouts.
- Desktop: 1024-1440px with max-width container.

### 10.2 Navigation on Small Screens

Switch nav pills from wrapping to horizontal scroll below `640px`.

```css
@media (max-width: 640px) {
  .route-nav {
    flex-wrap: nowrap;
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
    padding-bottom: 0.25rem;
  }
}
```

Optional: make nav sticky with a glass strip.

### 10.3 Tables

Wrap tables in a scroll container:

```html
<div class="table-wrap"><table>...</table></div>
```

```css
.table-wrap {
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
}

.table-wrap table {
  min-width: 760px;
}
```

### 10.4 Touch Targets and Forms

- Minimum 44px tap target height.
- Keep `:focus-visible` styles obvious.
- Avoid very low-contrast placeholders in dark mode.

### 10.5 Queue List Responsive Layout (shipped 2026-02-23)

- HTML contract: wrap queue listings in `<div class="queue-layouts">` with a `.queue-table-wrapper` div and `.queue-card-list` sibling. The cards list must use `<ul role="list">` and `<li class="queue-card">`.
- Table behavior: `.queue-table-wrapper` stays visible on `@media (min-width: 768px)` and still renders orchestrator/manifests rows. When non-queue sources exist, set `data-sticky-table="true"` so tablets/phones keep the table available.
- Card behavior: `.queue-card-list` is visible for `@media (max-width: 767px)` and hides on larger breakpoints. Each card uses `.queue-card-header`, `.queue-card-meta`, `.queue-card-fields`, and `.queue-card-actions` to keep typography consistent.
- Definition list contract: iterate `queueFieldDefinitions` so `<dt>/<dd>` stacks match desktop columns. `.queue-card-fields` defaults to two columns and collapses to one column below 768px.
- Buttons: reuse `.button.secondary` plus flexbox `.queue-card-actions` so "View details" spans the full card width on mobile.
- Styling source of truth: selectors live in `dashboard.tailwind.css` and compiled into `dashboard.css`. Use MoonMind tokens (`--mm-border`, `--mm-panel`, `--mm-muted`) for color/alpha tweaks to stay dark-mode compatible.

#### 10.5.1 Shared Row Definition

The components parse execution records into normalized properties: Source, Queue, Runtime, Status, Created, Started, Finished.
The Table maps these into standard UI grids, while the Mobile Card iterates these definitions to format `<dt>/<dd>` lists inside vertical blocks.

- The backend exposes these rows consistently over `GET /api/queue/jobs`.
- UI CSS governs the CSS media queries hiding the Table at `max-width: 767px` and showing Cards instead.

## 11. Migration Status

### Phase 1: Toolchain + generated CSS

Status: Complete (2026-02-18)

- Tailwind/PostCSS configs and npm scripts landed.
- Build pipeline compiles `dashboard.tailwind.css` to `dashboard.css`.

### Phase 2: Tokenization + brand refresh

Status: Complete (2026-02-18)

- Legacy color vars replaced with `--mm-*`.
- Purple/cyan/pink visual direction applied in light mode.

### Phase 3: Dark mode system

Status: Complete (2026-02-19)

- `.dark` token overrides shipped in `dashboard.tailwind.css`.
- Theme toggle shipped in template with runtime persistence in `dashboard.js`.
- No-flash boot script shipped in `<head>` for pre-paint theme resolution.
- Readability and accent hierarchy validated by runtime smoke checks and unit suite.

### Phase 4: Mobile refinement

Status: Planned

- Horizontal-scroll nav on narrow widths.
- Table wrapper support with minimum widths.
- Touch target and compact spacing polish.

### Phase 5: Glass and motion polish

Status: Planned

- Expand `mm-glass` and `mm-glass-strong` usage.
- Tune glow and elevation hierarchy.
- Keep motion subtle (opacity/scale only). Avoid translateY rising on hover/click.

## 12. Validation Checklist

Current baseline:

- [x] `dashboard.css` generated from `dashboard.tailwind.css`
- [x] Semantic classes preserved for JS-rendered views
- [x] Glass fallback provided for no-`backdrop-filter` browsers
- [x] Light-mode visual QA completed in Chromium and Firefox
- [x] Focus indicators remain visible with forced-colors fallback

Dark mode release gate:

- [x] `.dark` token overrides implemented
- [x] Theme toggle exists and persists preference
- [x] System preference respected when no user override exists
- [x] No first-paint theme flash
- [x] Tables/forms/log output remain readable in dark mode
- [x] Purple remains primary accent; yellow/orange highlight usage is restrained and intentional

Mobile release gate:

- [x] Queue nav/table views remain usable on small screens without excessive wrapping (card layout shipped)
- [x] Queue tables/cards share one responsive component and scroll without layout break
- [ ] Touch targets meet minimum size guidelines

## 13. Troubleshooting

### Tailwind classes do not appear

1. Confirm `tailwind.config.cjs` `content` includes both template and JS paths.
2. Avoid dynamic utility class construction in JS; use semantic classes.
3. Confirm CSS was rebuilt (`npm run dashboard:css:min`).

### `tailwindcss: command not found`

1. Run `npm install` in repo root.
2. Verify CLI availability with `npx tailwindcss --help`.
3. As an emergency-only fallback, keep source and output synchronized manually, then regenerate as soon as CLI is available.

### Dark mode controls look wrong

1. Verify `.dark` token scope exists in `dashboard.tailwind.css`.
2. Verify toggle script runs before dashboard render logic.
3. Check `color-scheme` values for both light and dark scopes.

### Blur is too expensive or looks muddy

1. Reduce number of blurred containers.
2. Increase panel opacity slightly.
3. Remove blur from high-frequency repaint regions.

## 14. Related Documents

- `docs/UI/MissionControlArchitecture.md`
- `specs/025-tailwind-dashboard/`
- `api_service/templates/task_dashboard.html`
- `api_service/static/task_dashboard/dashboard.js`
- `api_service/static/task_dashboard/dashboard.tailwind.css`
- `api_service/static/task_dashboard/dashboard.css`
