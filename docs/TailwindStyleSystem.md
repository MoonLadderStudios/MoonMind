# Tailwind Style System

Status: Draft (implementation-ready)  
Owners: MoonMind Engineering  
Last Updated: 2026-02-18  

## 1. Purpose

Introduce **Tailwind CSS** as the styling system for the MoonMind **Tasks Dashboard** while keeping the current dashboard architecture intact (FastAPI-served HTML shell + vanilla JS renderer + static CSS).

This doc describes:

- The **file layout** and **build pipeline** needed to compile Tailwind into the existing `dashboard.css` asset.
- A **low-risk migration strategy** that does not require rewriting `dashboard.js` or `task_dashboard.html`.
- Guidance for making the dashboard **dark-mode**, **mobile-friendly**, and **modern/futuristic** (MoonMind branding with **purple** + glass effects).

## 2. Background / Current State

The dashboard currently ships as:

- HTML shell: `api_service/templates/task_dashboard.html` :contentReference[oaicite:0]{index=0}
- JS renderer: `api_service/static/task_dashboard/dashboard.js` :contentReference[oaicite:1]{index=1}
- CSS stylesheet: `api_service/static/task_dashboard/dashboard.css` :contentReference[oaicite:2]{index=2}

The template links directly to `/static/task_dashboard/dashboard.css` and `/static/task_dashboard/dashboard.js` :contentReference[oaicite:3]{index=3}. The JS renders page content client-side and uses semantic class names such as `masthead`, `route-nav`, `panel`, and dynamic status classes like `status status-${normalized}` :contentReference[oaicite:4]{index=4} :contentReference[oaicite:5]{index=5}.

This makes Tailwind adoption primarily a **CSS build system** change, not a frontend framework change.

## 3. Goals and Non-Goals

### 3.1 Goals

1. Keep the public asset path stable:
   - Continue serving `/static/task_dashboard/dashboard.css`.
2. Add Tailwind as a **compile step** so we can:
   - Use utilities for new UI features.
   - Use `@apply` to define reusable component classes (optional).
   - Add consistent tokens (colors, shadows, radii, blur) to support a “liquid glass” direction.
3. Enable **theme + UX upgrades**:
   - **Dark mode** (user toggle + system default)
   - **Mobile-friendly layouts** (responsive tables/forms/nav)
   - **Modern/futuristic look** (MoonMind purple accent, tasteful gradients, translucency, blur)

### 3.2 Non-Goals

- Introducing React/Vue/Svelte, Vite, or a JS bundler.
- Rewriting dashboard rendering logic in `dashboard.js`.
- Changing routing, auth, or API contracts.

## 4. Visual Direction: MoonMind (Futuristic, Purple, Liquid Glass)

MoonMind’s UI should feel:

- **Futuristic**: crisp typography, high contrast, subtle “neon” accents, soft glows.
- **Glass-like**: semi-transparent surfaces, blur behind panels, luminous borders.
- **Purple-forward**: purple as the primary accent; cyan/pink as secondary highlights (sparingly).
- **Operationally readable**: tables, logs, status chips must remain high legibility.

### 4.1 Recommended palette

Primary:
- **Violet/Purple** for actions, links, selected states.
Secondary:
- **Cyan** for “running/live/streaming” and system highlights.
- **Pink/Magenta** for emphasis accents (sparingly).
Status colors:
- Queued: amber
- Running: cyan/sky
- Awaiting action: violet (brand-consistent)
- Succeeded: green
- Failed/cancelled: red/rose

### 4.2 Performance note on glass

Blur (`backdrop-filter`) is expensive if used on very large surfaces or animated. Prefer:
- A small number of major glass containers (masthead, nav, panel)
- Avoid animating blur or large translucent overlays
- Provide fallbacks when `backdrop-filter` is unavailable

## 5. Approach Overview (Recommended)

### 5.1 Keep existing semantic class names

Safest path is to keep semantic classes currently used by the template and JS:

- `dashboard-root`, `masthead`, `route-nav`, `panel`, `toolbar`, `card`, `grid-2`, etc. :contentReference[oaicite:6]{index=6} :contentReference[oaicite:7]{index=7}
- Status chips: `status status-queued|running|awaiting_action|succeeded|failed|cancelled` :contentReference[oaicite:8]{index=8}

Then implement those classes using Tailwind (`@apply`) + a small amount of bespoke CSS for gradients, blur, and glow.

This avoids:
- Big JS markup churn
- Tailwind “purge” issues caused by dynamic utility class names

### 5.2 Compile into the existing output file

Tailwind should compile:

- Input: `api_service/static/task_dashboard/dashboard.tailwind.css`
- Output (served): `api_service/static/task_dashboard/dashboard.css`

The HTML template stays unchanged (still references `dashboard.css`). :contentReference[oaicite:9]{index=9}

## 6. File Layout

Add these files:

```text
package.json                     # add tailwind dev deps + scripts (repo root)
package-lock.json                # commit for reproducible builds
tailwind.config.cjs              # Tailwind config (repo root)
postcss.config.cjs               # PostCSS config (repo root)

api_service/static/task_dashboard/
├── dashboard.js                 # existing (unchanged initially)
├── dashboard.css                # GENERATED output (served)
└── dashboard.tailwind.css       # NEW source input for tailwind build
````

Optional helper scripts:

```text
tools/
└── build-dashboard-css.sh       # convenience wrapper for CI/dev
```

## 7. Tailwind Configuration

### 7.1 `tailwind.config.cjs` (dark mode + token-based colors)

Key points:

* `content` MUST include the dashboard template + JS (the UI is rendered in JS).
* `darkMode: "class"` so we can toggle by adding `dark` to `<html>`.
* Prefer **CSS variables as RGB triplets** so Tailwind opacity utilities work (`bg-mm-accent/20`, etc).

```js
// tailwind.config.cjs
/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./api_service/templates/task_dashboard.html",
    "./api_service/static/task_dashboard/**/*.js",
  ],
  darkMode: "class",
  corePlugins: {
    // Phase 1 safety: keep false to avoid surprise resets; consider enabling later.
    preflight: false,
  },
  theme: {
    extend: {
      colors: {
        // Token colors (support alpha): use like bg-mm-bg, text-mm-ink, border-mm-border/40
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
      borderRadius: {
        mm: "0.9rem",
      },
      boxShadow: {
        mm: "var(--mm-shadow)",
        // optional “neon” glow for selected/active states
        mmGlow: "0 0 0 1px rgb(var(--mm-accent) / 0.55), 0 10px 40px -20px rgb(var(--mm-accent) / 0.65)",
      },
      backdropBlur: {
        mm: "18px",
      },
      transitionTimingFunction: {
        mm: "cubic-bezier(.2,.8,.2,1)",
      },
    },
  },
  safelist: [
    // Keep small and intentional. Prefer semantic classes for dynamic states.
  ],
};
```

### 7.2 `postcss.config.cjs`

```js
// postcss.config.cjs
module.exports = {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
};
```

## 8. Theme Tokens (Light + Dark) and “Liquid Glass” Foundations

Create `api_service/static/task_dashboard/dashboard.tailwind.css` as the source of truth.

### 8.1 Token sets

Define tokens as RGB triplets (space-separated) to enable alpha:

```css
/* api_service/static/task_dashboard/dashboard.tailwind.css */

@tailwind base;
@tailwind components;
@tailwind utilities;

@layer base {
  /* Light theme (default) */
  :root {
    color-scheme: light;

    --mm-bg: 248 247 255;          /* faint lavender */
    --mm-panel: 255 255 255;
    --mm-ink: 18 20 32;
    --mm-muted: 95 102 122;
    --mm-border: 214 220 235;

    /* MoonMind accents */
    --mm-accent: 139 92 246;       /* violet */
    --mm-accent-2: 34 211 238;     /* cyan */
    --mm-accent-warm: 244 114 182; /* pink */

    /* Status */
    --mm-ok: 34 197 94;
    --mm-warn: 245 158 11;
    --mm-danger: 244 63 94;

    --mm-shadow: 0 18px 32px -26px rgb(10 8 30 / 0.55);
  }

  /* Dark theme overrides */
  .dark {
    color-scheme: dark;

    --mm-bg: 8 8 16;               /* near-black with a violet bias */
    --mm-panel: 18 18 30;
    --mm-ink: 236 236 252;
    --mm-muted: 170 170 195;
    --mm-border: 70 70 105;

    --mm-accent: 167 139 250;      /* slightly brighter violet */
    --mm-accent-2: 103 232 249;
    --mm-accent-warm: 248 113 200;

    --mm-ok: 74 222 128;
    --mm-warn: 251 191 36;
    --mm-danger: 251 113 133;

    --mm-shadow: 0 22px 44px -30px rgb(0 0 0 / 0.85);
  }

  * { box-sizing: border-box; }

  body {
    margin: 0;
    min-height: 100vh;
    color: rgb(var(--mm-ink));
    font-family: "IBM Plex Sans", "Segoe UI", system-ui, sans-serif;

    /* Futuristic gradient fog (tweak to taste) */
    background:
      radial-gradient(circle at 8% 0%, rgb(var(--mm-accent) / 0.18), transparent 44%),
      radial-gradient(circle at 98% 0%, rgb(var(--mm-accent-2) / 0.14), transparent 42%),
      radial-gradient(circle at 50% 100%, rgb(var(--mm-accent-warm) / 0.10), transparent 52%),
      rgb(var(--mm-bg));
  }
}
```

### 8.2 Glass surfaces (core technique)

Define a reusable “glass” class that works in both light and dark themes:

```css
@layer components {
  .mm-glass {
    @apply border rounded-mm shadow-mm;
    border-color: rgb(var(--mm-border) / 0.55);
    background: rgb(var(--mm-panel) / 0.62);
    backdrop-filter: blur(18px);
    -webkit-backdrop-filter: blur(18px);
  }

  /* Fallback when blur isn’t supported */
  @supports not ((backdrop-filter: blur(2px)) or (-webkit-backdrop-filter: blur(2px))) {
    .mm-glass {
      background: rgb(var(--mm-panel) / 0.92);
    }
  }

  /* Optional “edge glow” for selected/active containers */
  .mm-glass-glow {
    box-shadow: var(--mm-shadow), 0 0 0 1px rgb(var(--mm-accent) / 0.35);
  }
}
```

Rule of thumb:

* Use `.mm-glass` on major containers (`masthead`, `panel`, maybe sticky nav)
* Keep inner cards more opaque (improves readability)

## 9. Dark Mode Implementation

The dashboard shell is a plain HTML template , so dark mode is best implemented by toggling a `dark` class on `<html>` (Tailwind’s recommended approach when you need a manual toggle).

### 9.1 Update viewport (small improvement)

The template already sets a viewport meta tag . For better iOS behavior, prefer:

* `viewport-fit=cover` (safe areas)
* keep `initial-scale=1`

Proposed change:

```html
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
```

### 9.2 Add a theme toggle control (in `task_dashboard.html`)

Add a small button in the masthead (minimal markup, no framework). The masthead exists already. 

Example (keep class semantic; style later via Tailwind):

```html
<button class="theme-toggle" type="button" aria-label="Toggle dark mode">
  Theme
</button>
```

### 9.3 Implement theme preference in `dashboard.js`

Add a tiny, isolated module at the top of the existing IIFE (no architectural rewrite).

Behavior:

* On load: apply stored preference if present, else follow system preference.
* On toggle: flip `dark` class on `<html>`, store preference.
* Respect `prefers-color-scheme` when user has not explicitly chosen.

Pseudo-implementation (illustrative):

```js
function initTheme() {
  const key = "moonmind.theme";
  const root = document.documentElement;
  const stored = localStorage.getItem(key);

  const systemPrefersDark =
    window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;

  const shouldUseDark =
    stored === "dark" ? true : stored === "light" ? false : systemPrefersDark;

  root.classList.toggle("dark", shouldUseDark);

  const button = document.querySelector(".theme-toggle");
  if (button) {
    button.addEventListener("click", () => {
      const next = !root.classList.contains("dark");
      root.classList.toggle("dark", next);
      localStorage.setItem(key, next ? "dark" : "light");
    });
  }
}
```

### 9.4 Styling dark mode

Prefer token changes + semantic classes over sprinkling `dark:` utilities everywhere. Your CSS becomes simpler:

* `.dark` updates variables
* semantic components automatically adapt

This is especially helpful for the dashboard’s dynamic UI output and status badges. 

## 10. Mobile-Friendly System (Responsive + Touch + Tables)

The current CSS already includes a small breakpoint for `grid-2` at 900px . Tailwind should formalize and expand responsiveness.

### 10.1 Layout targets

* Mobile: 360–430px (single column, sticky nav optional)
* Tablet: 768px (two-column where useful; tables scroll)
* Desktop: 1024–1440px (max width, multi-column)

### 10.2 Navigation on small screens

The template nav is a row of pills that wraps. 
For mobile, “wrap” can become tall and push content down.

Recommended pattern:

* Switch to **horizontal scroll** pills on narrow screens
* Optionally make nav sticky with a glass background

Semantic class approach:

```css
@layer components {
  .route-nav {
    @apply mt-4 flex gap-2;
  }

  /* Mobile: horizontal scroll nav instead of wrap */
  @media (max-width: 640px) {
    .route-nav {
      flex-wrap: nowrap;
      overflow-x: auto;
      -webkit-overflow-scrolling: touch;
      padding-bottom: 0.25rem;
    }
    .route-nav::-webkit-scrollbar { height: 8px; }
  }
}
```

Optional sticky nav (mobile only):

```css
@media (max-width: 640px) {
  .route-nav {
    position: sticky;
    top: 0;
    z-index: 10;
    backdrop-filter: blur(18px);
    -webkit-backdrop-filter: blur(18px);
    background: rgb(var(--mm-bg) / 0.55);
  }
}
```

### 10.3 Tables

Tables are hard on mobile. Current table styles assume desktop width. 

Minimum viable mobile improvement:

* Wrap tables in a scrolling container (`overflow-x:auto`)
* Give tables a `min-width` that preserves column meaning
* Add row hover only on pointer devices

Add a wrapper class and use it whenever you render tables in JS:

* Render: `<div class="table-wrap"><table>...</table></div>`

CSS:

```css
@layer components {
  .table-wrap {
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
    border-radius: 0.75rem;
  }
  .table-wrap table {
    min-width: 760px;
  }

  @media (hover: none) {
    tbody tr:hover { background: inherit; }
  }
}
```

If you later want “true mobile” tables:

* Render rows as stacked cards below 640px (JS render switch)
* Keep desktop table above 640px

### 10.4 Touch targets and forms

Aim for:

* 44px minimum tap height for buttons/inputs
* Clear focus states (`:focus-visible`)
* Less dense spacing on small screens

Use semantic classes:

* `.btn`, `.btn-secondary`, `.input`, `.select`, `.textarea`

Then implement with `@apply` so they adapt in dark mode automatically.

## 11. Component Recipes (Modern + Glass + Purple)

This section provides concrete guidance for styling the existing semantic classes without rewriting the JS.

### 11.1 Page containers

The template uses `dashboard-root`, `masthead`, and `panel`. 

Recommended mappings:

```css
@layer components {
  .dashboard-root {
    @apply mx-auto max-w-6xl px-4 pb-12 pt-5;
  }

  .masthead {
    @apply mm-glass mm-glass-glow p-6;
    /* Optional gradient sheen that feels “liquid” */
    background:
      linear-gradient(
        135deg,
        rgb(var(--mm-panel) / 0.66) 0%,
        rgb(var(--mm-panel) / 0.52) 45%,
        rgb(var(--mm-panel) / 0.62) 100%
      );
  }

  .panel {
    @apply mm-glass p-4 mt-4;
    min-height: 320px;
    animation: panel-enter 220ms var(--mm-ease, ease);
  }
}
```

### 11.2 Typography (futuristic but readable)

Keep:

* Sans for most UI
* Condensed display for `h1` (already used in current CSS) 
* Mono for logs/code (already used) 

Add subtle “tech” feel:

* Slight letter spacing on eyebrow labels
* Use higher weight for section titles
* Avoid ultra-thin type on dark backgrounds

### 11.3 Navigation pills (purple-forward)

```css
@layer components {
  .route-nav a {
    @apply rounded-full border px-3 py-2 text-sm font-semibold transition;
    border-color: rgb(var(--mm-border) / 0.65);
    color: rgb(var(--mm-ink) / 0.86);
    background: rgb(var(--mm-panel) / 0.55);
    backdrop-filter: blur(14px);
    -webkit-backdrop-filter: blur(14px);
    transition-timing-function: theme(transitionTimingFunction.mm);
    transition-duration: 140ms;
  }

  .route-nav a:hover {
    box-shadow: theme(boxShadow.mmGlow);
    border-color: rgb(var(--mm-accent) / 0.65);
    transform: translateY(-1px);
  }

  .route-nav a.active {
    box-shadow: theme(boxShadow.mmGlow);
    border-color: rgb(var(--mm-accent) / 0.8);
    background: rgb(var(--mm-accent) / 0.12);
    color: rgb(var(--mm-ink));
  }
}
```

### 11.4 Buttons (purple primary + glass secondary)

Current `button` is accent blue. 
Switch to purple token and add better hover/focus:

```css
@layer components {
  .btn,
  button {
    @apply rounded-full px-4 py-2 font-bold border transition;
    border-color: transparent;
    background: rgb(var(--mm-accent) / 0.92);
    color: white;
    transition-timing-function: theme(transitionTimingFunction.mm);
    transition-duration: 140ms;
  }

  .btn:hover,
  button:hover {
    box-shadow: theme(boxShadow.mmGlow);
    background: rgb(var(--mm-accent) / 1);
    transform: translateY(-1px);
  }

  .btn:focus-visible,
  button:focus-visible {
    outline: 2px solid rgb(var(--mm-accent-2) / 0.85);
    outline-offset: 2px;
  }

  .btn.secondary,
  button.secondary {
    @apply mm-glass;
    background: rgb(var(--mm-panel) / 0.55);
    color: rgb(var(--mm-ink) / 0.9);
  }

  .btn.secondary:hover,
  button.secondary:hover {
    border-color: rgb(var(--mm-accent-2) / 0.55);
  }
}
```

### 11.5 Status chips (brand-consistent)

JS renders status badges like `status status-${normalized}`. 

Implement them as translucent chips with correct colors:

```css
@layer components {
  .status {
    @apply inline-flex items-center rounded-full border px-2 py-0.5 text-[0.74rem] font-semibold uppercase tracking-wide;
    background: rgb(var(--mm-panel) / 0.5);
  }

  .status-queued {
    color: rgb(var(--mm-warn) / 0.95);
    background: rgb(var(--mm-warn) / 0.14);
    border-color: rgb(var(--mm-warn) / 0.35);
  }

  .status-running {
    color: rgb(var(--mm-accent-2) / 0.95);
    background: rgb(var(--mm-accent-2) / 0.14);
    border-color: rgb(var(--mm-accent-2) / 0.35);
  }

  .status-awaiting_action {
    color: rgb(var(--mm-accent) / 0.95);
    background: rgb(var(--mm-accent) / 0.14);
    border-color: rgb(var(--mm-accent) / 0.35);
  }

  .status-succeeded {
    color: rgb(var(--mm-ok) / 0.95);
    background: rgb(var(--mm-ok) / 0.14);
    border-color: rgb(var(--mm-ok) / 0.35);
  }

  .status-failed,
  .status-cancelled {
    color: rgb(var(--mm-danger) / 0.95);
    background: rgb(var(--mm-danger) / 0.14);
    border-color: rgb(var(--mm-danger) / 0.35);
  }
}
```

### 11.6 Live output (terminal-like, dark-mode friendly)

There’s a live output pane class `.queue-live-output`. 
Make it feel like a futuristic terminal:

* Use darker translucent background even in light mode
* Mono font, higher line-height
* Subtle border glow

## 12. Build Commands (Local + CI)

### 12.1 `package.json` scripts (repo root)

```json
{
  "devDependencies": {
    "autoprefixer": "^10.4.0",
    "postcss": "^8.4.0",
    "tailwindcss": "^3.4.0"
  },
  "scripts": {
    "dashboard:css": "tailwindcss -i api_service/static/task_dashboard/dashboard.tailwind.css -o api_service/static/task_dashboard/dashboard.css",
    "dashboard:css:min": "tailwindcss -i api_service/static/task_dashboard/dashboard.tailwind.css -o api_service/static/task_dashboard/dashboard.css --minify",
    "dashboard:css:watch": "tailwindcss -i api_service/static/task_dashboard/dashboard.tailwind.css -o api_service/static/task_dashboard/dashboard.css --watch"
  }
}
```

### 12.2 Developer workflow

1. Install deps:

   * `npm install`
2. During styling:

   * `npm run dashboard:css:watch`
3. Before committing:

   * `npm run dashboard:css:min`
   *Note: If the local Node installation omits `npm`, install it or run the commands via Docker/CI before merging so the generated CSS stays in sync.*

### 12.3 CI consistency check (recommended)

* `npm ci`
* `npm run dashboard:css:min`
* `git diff --exit-code -- api_service/static/task_dashboard/dashboard.css`

## 13. Migration Plan (with theme/UX upgrades)

### Phase 1: Toolchain + identical output

1. Add Tailwind config + PostCSS config.
2. Add `dashboard.tailwind.css`.
3. Paste current CSS into it (or progressively port).
4. Generate `dashboard.css` and verify no regressions.

### Phase 2: Tokenize colors + purple rebrand

1. Replace `:root --accent` etc with `--mm-*` tokens.

   * Current CSS uses business-blue accent vars ; migrate to purple tokens.
2. Update `body` background gradients to purple/cyan/pink.

### Phase 3: Add dark mode

1. Enable `darkMode: "class"`.
2. Add `.dark` token overrides.
3. Add theme toggle + localStorage persistence.

### Phase 4: Mobile refinement

1. Horizontal scroll nav on mobile.
2. Table wrappers (`.table-wrap`) and minimum widths.
3. Touch target sizing + focus-visible.

### Phase 5: Glass polish

1. Convert `masthead` and `panel` to `.mm-glass`.
2. Add subtle glows for active nav, primary buttons, focused inputs.
3. Keep inner content surfaces slightly more opaque for readability.

## 14. Validation Checklist

* [ ] `/static/task_dashboard/dashboard.css` is generated from Tailwind input
* [ ] Dashboard renders correctly across routes (per contracts) 
* [ ] Dark mode:

  * [ ] toggle works (persisted)
  * [ ] system preference respected when no explicit choice
  * [ ] tables/forms/logs remain readable
* [ ] Mobile:

  * [ ] nav usable without taking over the page
  * [ ] tables scroll horizontally (no layout break)
  * [ ] forms/buttons have adequate touch sizes
* [ ] Glass:

  * [ ] blur works where supported; fallback looks fine where not
  * [ ] performance acceptable (no huge blurred overlays)
* [ ] Light-mode before/after screenshots saved to `docs/assets/task_dashboard/phase2/` and referenced inside this doc for regression history

## 15. Troubleshooting

### “My Tailwind styles don’t appear”

1. Tailwind isn’t scanning the file where your class string exists.

   * Ensure `content` includes `task_dashboard.html` and `static/task_dashboard/**/*.js`
2. Dynamic Tailwind utility class names are being constructed.

   * Prefer semantic classes + `@apply`.
3. You’re editing `dashboard.css` directly.

   * Edit `dashboard.tailwind.css` and rebuild.

### “tailwindcss: command not found”

1. Some sandboxes ship a Node binary without `npm`. Check `npm --version`; if missing, install Node+NPM locally or run the build inside Docker/CI.
2. Until the CLI is available, edit both `dashboard.tailwind.css` and `dashboard.css` together so production styles stay in sync, then regenerate via `npm run dashboard:css:min` once npm exists.
3. `tools/build-dashboard-css.sh` already checks for `tailwindcss` and emits a helpful error if the binary is absent.

### “Dark mode makes form controls look wrong”

* Ensure `color-scheme` toggles between light/dark (see token section).
* Prefer token colors for inputs and borders.

### “Blur looks bad / is slow”

* Reduce the number of blurred containers.
* Avoid animating translucent/blurred layers.
* Increase opacity slightly on low-end devices (optional conditional via media queries).

## 16. Related Documents

* `docs/TaskUiArchitecture.md`
* `docs/TaskUiStrategy1ThinDashboard.md`
* `specs/017-thin-dashboard-ui/*`
* `api_service/templates/task_dashboard.html` 
* `api_service/static/task_dashboard/dashboard.js` 
* `api_service/static/task_dashboard/dashboard.css`
