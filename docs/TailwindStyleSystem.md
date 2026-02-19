# Tailwind Style System

Status: Active guidance (Phases 1-3 shipped, Phase 4+ planned)  
Owners: MoonMind Engineering  
Last Updated: 2026-02-19

## 1. Purpose

Introduce Tailwind CSS for the MoonMind Tasks Dashboard without changing the dashboard architecture (FastAPI HTML shell + vanilla JS renderer + static CSS).

This document is both:

- A source of truth for what is already in production.
- A design and implementation guide for upcoming dark mode and modern UI polish.

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
- [ ] Mobile-specific nav/table refinements are not yet complete.

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

### 4.1 Recommended palette and role mapping

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

### 4.2 Liquid glass rules (practical)

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

## 5. Approach Overview (Recommended)

### 5.1 Keep semantic class names

Continue styling existing classes used by template and JS:

- `dashboard-root`, `masthead`, `route-nav`, `panel`, `card`, `toolbar`, `grid-2`
- Status chips: `status`, `status-queued`, `status-running`, `status-awaiting_action`, `status-succeeded`, `status-failed`, `status-cancelled`

This keeps JS stable and avoids dynamic utility-generation problems.

### 5.2 Compile into existing output file

Build flow:

- Input: `api_service/static/task_dashboard/dashboard.tailwind.css`
- Output: `api_service/static/task_dashboard/dashboard.css`

Do not hand-edit `dashboard.css` during normal development.

### 5.3 Token-first theming

- Theme behavior should be controlled by tokens (`--mm-*`).
- Semantic classes consume tokens.
- Dark mode flips tokens via `.dark`; components adapt automatically.

## 6. File Layout

```text
package.json
package-lock.json
tailwind.config.cjs
postcss.config.cjs

api_service/templates/
└── task_dashboard.html

api_service/static/task_dashboard/
├── dashboard.js
├── dashboard.tailwind.css   # source of truth
└── dashboard.css            # generated + served

tools/
└── build-dashboard-css.sh   # optional helper
```

## 7. Tailwind Configuration

### 7.1 `tailwind.config.cjs`

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

### 7.2 `postcss.config.cjs`

```js
module.exports = {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
};
```

## 8. Theme Tokens and Liquid-Glass Foundations

### 8.1 Current shipped light tokens (accurate as of 2026-02-19)

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

### 8.2 Target dark token overrides (Phase 3)

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

### 8.3 Atmospheric background layers

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

### 8.4 Core glass utilities

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

## 9. Dark Mode Implementation

### 9.1 Template updates (`task_dashboard.html`)

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

### 9.2 Theme preference logic (`dashboard.js`)

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

### 9.3 Prevent theme flash on load

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

### 9.4 Styling strategy

- Prefer token swaps and semantic classes.
- Avoid scattering many `dark:*` utility variants in JS-generated markup.
- Keep focus indicators highly visible in both themes.

## 10. Mobile-Friendly System (Responsive + Touch + Tables)

### 10.1 Layout targets

- Mobile: 360-430px, single-column first.
- Tablet: 768px, selective two-column layouts.
- Desktop: 1024-1440px with max-width container.

### 10.2 Navigation on small screens

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

### 10.4 Touch targets and forms

- Minimum 44px tap target height.
- Keep `:focus-visible` styles obvious.
- Avoid very low-contrast placeholders in dark mode.

## 11. Component Recipes (Modern + Glass + Purple)

### 11.1 Containers

- `masthead` and primary `panel` use `mm-glass` or `mm-glass-strong`.
- Inner cards remain less transparent than outer shell.
- Keep content rhythm tight but readable (`0.75rem` to `1rem` spacing).

### 11.2 Typography

- Continue `IBM Plex Sans` for body, `Barlow Condensed` for headline, and `IBM Plex Mono` for logs/code.
- Slight uppercase + tracking for overline labels only.
- Avoid thin weights on glass backgrounds.

### 11.3 Navigation pills

- Base: translucent panel token.
- Hover/active: purple edge glow.
- Active text must keep strong contrast in both themes.

### 11.4 Buttons (purple primary, warm highlight option)

Primary:

- Purple background (`--mm-accent`) with subtle glow on hover.

Secondary:

- Glass surface, neutral text, accent border on hover.

Warning/highlight action:

- Use `--mm-warn` or `--mm-accent-warm` for yellow/orange emphasis.
- Reserve for destructive confirmation flows or urgent operator actions.

### 11.5 Status chips

Map status consistently:

- `queued` -> amber/yellow (`--mm-warn`)
- `running` -> cyan (`--mm-accent-2`)
- `awaiting_action` -> purple (`--mm-accent`)
- `succeeded` -> green (`--mm-ok`)
- `failed/cancelled` -> rose/red (`--mm-danger`)

Keep chip fills translucent and borders slightly stronger than fills.

### 11.6 Live output pane

For `.queue-live-output`:

- Dark translucent background even in light mode.
- Mono font + slightly elevated line-height.
- Subtle inset border and optional faint purple edge.
- Preserve long-line wrapping and scanability.

## 12. Build Commands (Local + CI)

### 12.1 `package.json` scripts (current)

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

1. `npm install`
2. During CSS work: `npm run dashboard:css:watch`
3. Before commit: `npm run dashboard:css:min`

### 12.3 CI consistency check

1. `npm ci`
2. `npm run dashboard:css:min`
3. `git diff --exit-code -- api_service/static/task_dashboard/dashboard.css`

## 13. Migration Plan (Phased, Accurate to Current Status)

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
- Keep motion subtle (opacity/translate only).

## 14. Validation Checklist

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

- [ ] Nav remains usable on small screens without excessive vertical wrapping
- [ ] Data tables scroll horizontally without layout break
- [ ] Touch targets meet minimum size guidelines

## 15. Troubleshooting

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

## 16. Related Documents

- `docs/TaskUiArchitecture.md`
- `docs/TaskUiStrategy1ThinDashboard.md`
- `specs/025-tailwind-dashboard/`
- `api_service/templates/task_dashboard.html`
- `api_service/static/task_dashboard/dashboard.js`
- `api_service/static/task_dashboard/dashboard.tailwind.css`
- `api_service/static/task_dashboard/dashboard.css`
