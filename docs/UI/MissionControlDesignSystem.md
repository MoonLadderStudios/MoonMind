# Mission Control Design System
Status: Active  
Owners: MoonMind Engineering  
Last updated: 2026-04-20

## 1. Purpose

Define the desired-state design system for the MoonMind Mission Control UI: visual language, design tokens, surface hierarchy, layout rules, motion, component behavior, and page composition. This document is declarative. It describes what Mission Control should look and feel like when it is correct.

For route ownership, runtime config, API boundaries, and task-oriented architecture, see `docs/UI/MissionControlArchitecture.md`.

---

## 2. Product expression

Mission Control should feel like **mission control with nightlife lighting**:

- **Space-program structure** provides the backbone: disciplined layout, telemetry-like metadata, modular panels, strong hierarchy, and operational readability.
- **Liquid glass** provides the elevated control language: floating rails, sticky utilities, overlays, nav emphasis, and premium interaction surfaces.
- **Synthwave lighting** provides the atmosphere: purple-forward identity, magenta/violet/cyan bloom, horizon gradients, and restrained energy.
- **Cyberpunk details** provide edge in small doses: segmented corners, bracketed labels, scanner hovers, warning accents, and high-priority telemetry.

Mission Control is **not** a novelty sci-fi HUD. It is a professional operator console. Readability, hierarchy, and scan speed take priority over spectacle.

---

## 3. Design principles

### 3.1 Functional first

Dense data, forms, logs, and tables must stay clear and readable. Decorative effects are only correct when they improve hierarchy and perceived quality without reducing comprehension.

### 3.2 Matte for content, glass for controls

Mission Control should not make every surface translucent. Dense reading and editing regions stay grounded. Floating, sticky, or elevated controls may use glass.

### 3.3 One hero effect per page

Each page should have a single primary spectacle or premium-effect surface. Examples:

- `/tasks/list`: control deck or sticky table/header treatment
- `/tasks/new`: floating launch rail at the bottom
- detail pages: a single elevated observability or action shell

The rest of the page should support that focal effect rather than compete with it.

### 3.4 Brighten on hover, do not darken

Interactive elements should feel more alive on hover. Hover states should generally increase brightness, border light, or glow rather than collapsing into darker states.

### 3.5 Motion should be restrained and intentional

Use short, premium transitions and small scale changes. Avoid bouncy motion, large translations, or constant shimmer.

---

## 4. Surface hierarchy

Mission Control uses a strict surface hierarchy.

| Surface type | Use | Visual posture |
| --- | --- | --- |
| **Atmosphere** | Page background, horizon lines, ambient color fields | Low-contrast gradients, subtle glow, optional star/noise texture |
| **Matte data slab** | Tables, dense cards, long-form content, logs, form bodies | Opaque or near-opaque, crisp borders, low reflection |
| **Satin form surface** | Standard inputs, textareas, selects, secondary cards | Slight sheen, inset depth, stronger inner contrast |
| **Glass control surface** | Nav rails, sticky toolbars, utility bars, elevated panels | Translucent fill, blur, border light, soft shadow |
| **liquidGL hero surface** | Small number of premium elevated controls | Real-time refraction, specular highlight, obvious separation from content |
| **Accent/live surface** | Active states, executing badges, selected pills, critical actions | Controlled neon energy, never used as a page-wide base |

### 4.1 Matte data slabs

Use matte slabs for:

- data tables
- log viewers
- long text entry
- main form bodies
- detail evidence panels
- dense task metadata regions

These surfaces should privilege crispness over translucency.

### 4.2 Glass control surfaces

Use glass for:

- mastheads
- floating action rails
- sticky control decks
- modals and drawers
- popovers and menus
- compact utility chips
- selected navigation shells

Glass surfaces should feel elevated above content, not interchangeable with it.

### 4.3 liquidGL hero surfaces

Mission Control now uses **liquidGL** for premium liquid-glass treatment on select elevated surfaces. liquidGL is an enhancement layer, not the baseline styling system.

Rules:

1. Use liquidGL only on **bounded, elevated, fixed or sticky surfaces** where refraction materially improves the experience.
2. The element must remain fully legible and correctly styled **without** liquidGL.
3. The CSS shell defines the component’s layout, border radius, padding, border, shadow, and fallback appearance. liquidGL enhances that shell rather than replacing it.
4. Do not use liquidGL on dense tables, large cards, long forms, or large scrolling containers.
5. Do not use liquidGL on large textareas or any surface where refraction would reduce editing comfort.
6. Prefer one liquidGL hero surface per page; two is the maximum when the hierarchy is unmistakable.
7. When performance, browser support, or user settings require it, fall back to the standard CSS glass treatment.

Recommended liquidGL targets:

- the Create Task floating launch rail
- compact sticky utility rails
- modals and drawers with bounded dimensions
- selected high-value popovers or command surfaces
- optional focused nav emphasis when it does not compete with the page hero surface

---

## 5. Color system and token contract

Tokens must be defined as RGB triplets so alpha composition stays flexible.

### 5.1 Core tokens

| Role | Token | Light | Dark | Guidance |
| --- | --- | --- | --- | --- |
| App background | `--mm-bg` | `248 247 255` | `8 8 16` | Slight violet bias even in neutrals |
| Primary surface | `--mm-panel` | `255 255 255` | `20 18 34` | Base shell and card surface |
| Primary text | `--mm-ink` | `18 20 32` | `237 236 255` | Must remain highly legible |
| Muted text | `--mm-muted` | `95 102 122` | `174 170 204` | Use for metadata only |
| Border | `--mm-border` | `214 220 235` | `92 84 136` | Slightly luminous in dark mode |
| Brand accent | `--mm-accent` | `130 72 246` | `130 72 246` | Default active/selected brand color |
| Live accent | `--mm-accent-2` | `34 211 238` | `125 249 255` | Live activity, streaming, executing energy |
| Warm accent | `--mm-accent-warm` | `244 114 182` | `249 115 22` | Callouts and stylized emphasis |
| Success | `--mm-ok` | `34 197 94` | `74 222 128` | Completed / healthy states |
| Warning | `--mm-warn` | `245 158 11` | `251 191 36` | Queue, caution, attention |
| Danger | `--mm-danger` | `244 63 94` | `251 113 133` | Failure, destructive actions |
| Commit action | `--mm-action-primary` | `21 147 118` | `26 153 123` | Real-action create/apply/launch pathways |
| Shadow | `--mm-shadow` | `0 18px 32px -26px rgb(10 8 30 / 0.55)` | `0 24px 52px -34px rgb(0 0 0 / 0.88)` | Base elevation shadow |

### 5.2 Accent balance

Accent usage should follow a clear hierarchy:

- **Purple / violet** is the primary identity color and should account for most accent usage.
- **Cyan** is reserved for live, executing, streaming, or high-energy system states.
- **Amber / orange** is reserved for warning, queueing, caution, or high-attention utility signals.
- **Red / rose** is reserved for failure and destructive actions.
- **Green / teal** is reserved for create, commit, promote, apply, or complete states.

### 5.3 Background atmosphere

The application background should never be flat black. Use layered radial gradients with restrained bloom so large dark regions feel intentional and alive.

The preferred posture is:

- violet energy near the upper-left or brand side
- cyan energy near the opposite edge
- warm/orange energy near the lower horizon
- all gradients soft enough that content remains dominant

Optional subtle star/noise texture is allowed in large empty background fields when it stays nearly invisible.

---

## 6. Typography and iconography

### 6.1 Type system

Mission Control uses:

- **IBM Plex Sans** for body copy, headings, labels, buttons, and UI text
- **IBM Plex Mono** for IDs, timestamps, runtime values, telemetry, logs, versions, and exact technical metadata

### 6.2 Hierarchy

- Headings should be bold enough to remain stable on dark/glass surfaces.
- Overline labels may use small uppercase styling with mild tracking.
- Avoid very thin weights, especially over gradients or glass.
- Favor strong contrast over stylistic subtlety.

### 6.3 Mono numerics and telemetry

The following should prefer mono styling or tabular numerics:

- task IDs
- timestamps
- runtime names and versions
- counts and durations
- pagination metrics
- technical labels and compact telemetry

This is a core part of the space-program feel.

### 6.4 Icon posture

Icons should be compact, crisp, and utility-first. Use icons to improve scanability, not to decorate every label.

Best candidates for icons:

- navigation pills
- filter controls
- telemetry chips
- action buttons with strong verbs
- drawer/modal utilities

---

## 7. Layout system

### 7.1 Shell widths

Mission Control uses two desktop width modes:

- **Constrained shell** for mastheads, forms, compact controls, and narrative content: roughly `1100-1280px`
- **Data-wide shell** for task tables, evidence-heavy detail regions, and comparison views: roughly `1500-1800px` or fluid width with generous side gutters

The page shell may transition between these two modes within the same route.

### 7.2 Header architecture

The Mission Control masthead uses a **three-zone layout**:

- **Left:** brand block
- **Center:** primary navigation pills, visually centered against the viewport
- **Right:** utilities such as version badge, environment state, future user/system controls

The navigation must be centered as a layout decision, not merely centered inside the leftover space beside the logo.

### 7.3 Control deck + data slab pattern

For list and console-heavy pages, use a two-part structure:

1. **Control deck** above the content for filters, utility controls, result counts, live toggles, chips, and page-level tools
2. **Data slab** below for the table, rows, or evidence surface

Do not merge filters and dense data into one giant undifferentiated card when separation improves hierarchy.

### 7.4 Utility cluster placement

Empty top-right desktop space should normally be used for compact utilities rather than left blank. Good uses include:

- live updates toggle
- result counts
- executing / queued / failed counts
- page size selector
- pagination controls
- active filter summary

### 7.5 Spacing posture

Spacing should feel disciplined rather than sparse. Prefer a tight but breathable rhythm. Large empty regions must look intentional, not unfinished.

Guidance:

- compact utility groups: `8-12px` gaps
- input/control spacing: `12-16px`
- card internal spacing: `16-24px`
- major section separation: `24-40px`

---

## 8. Glass system

### 8.1 CSS glass foundation

The default glass system is token-driven CSS and must exist even when liquidGL is disabled or unsupported.

Core requirements:

- translucent fill
- luminous 1px border
- controlled shadow separation
- `backdrop-filter` blur and saturation where supported
- near-opaque fallback where unsupported

Recommended ranges:

- fill alpha: `0.55-0.78`
- border alpha: `0.35-0.75`
- blur radius: `14-20px`
- glow alpha: `<= 0.45`

### 8.2 Glass layering rule

Outer shells may be glassy. Inner editing surfaces should usually be more grounded.

Example:

- floating dock shell: glass or liquidGL
- inputs inside dock: darker inset wells with crisp text and lower blur

This layered contrast is what makes the outer shell feel convincingly premium.

### 8.3 Edge light and shadow

Prefer edge highlights, shadow separation, and a small amount of specular light over broad glow floods. Broad neon fog weakens hierarchy and legibility.

### 8.4 liquidGL implementation stance

liquidGL should be treated as a **premium enhancement** for a small number of elevated surfaces.

Requirements:

- the component must still ship a full CSS appearance without JavaScript enhancement
- the liquidGL layer must not be the only source of edge definition or text contrast
- any target surface must be stable in shape and bounded in size
- overflow, z-index, and stacking behavior must be explicit so helper layers do not clip unexpectedly
- liquidGL should not become the default for all `.panel` or `.card` surfaces

### 8.5 Fallback posture

When liquidGL or `backdrop-filter` is unavailable, the UI must gracefully fall back to token-based CSS glass or matte surfaces. Mission Control must remain coherent and premium-looking without advanced rendering.

---

## 9. Interaction and motion

### 9.1 Timing

Preferred transition ranges:

- hover/focus transitions: `120-180ms`
- small state changes: `140-200ms`
- no long lingering animations for routine controls

### 9.2 Motion model

Use small scale changes and glow changes. Prefer:

- hover: `scale(1.02-1.03)`
- active press: `scale(0.98-0.99)`

Avoid `translateY` lift behavior on core buttons and nav pills. Mission Control should feel precise, not floaty.

### 9.3 Live-state motion

Executing/live surfaces may use very subtle pulse or telemetry motion. Examples:

- a restrained pulse on executing status chips
- a soft scanner pass on row hover
- a faint live indicator flicker near real-time controls

These effects must remain low-amplitude.

### 9.4 Reduced motion

A reduced-motion path is required. Pulses, shimmer, scanner effects, and highlight drift should be removed or significantly softened when the user requests reduced motion.

---

## 10. Component system

### 10.1 Masthead and navigation

Navigation pills should feel like compact control chips rather than generic tabs.

Rules:

- active pills must be unmistakable
- hover and active states should brighten, not darken
- spacing between icon and label must be deliberate
- active pills may use soft bloom and a slightly stronger shell
- the masthead should include a refined horizon separator below it

Version badges and utility chips on the right should read like telemetry, not leftover labels.

### 10.2 Buttons

Mission Control uses one consistent **glow + grow** interaction system.

Rules:

- buttons are slightly translucent, never fully dead/flat by default
- hover gets brighter and slightly larger
- press state uses subtle scale-down, not translate
- focus-visible must remain crisp and high contrast
- disabled buttons lose glow and motion emphasis

Variants:

- **Default / brand**: purple
- **Secondary / safe**: glass or satin surface
- **Commit / create / apply**: `--mm-action-primary`
- **Danger**: `--mm-danger`

A thin outline and faint edge-light are required so buttons do not disappear into glass backgrounds.

### 10.3 Inputs, selects, and comboboxes

Inputs must feel designed, not browser-default.

Rules:

- give controls a distinct outer shell and a darker or more grounded inner well
- maintain generous vertical height and click target clarity
- labels should read clearly above controls
- focus uses a visible ring and stronger border light
- chevrons and affordance icons must be intentional, not tiny afterthoughts
- searchable repository or entity fields should read like **combobox/search controls**, not generic selects

Good candidates for leading icons:

- workflow
- state
- entry
- repository
- branch
- runtime

### 10.4 Control decks and filter clusters

Control decks should feel like operator consoles.

Rules:

- primary filters remain compact and high-signal
- utilities live in a separate cluster rather than cluttering the main filter row
- active filter chips should be visible and removable
- a reset/clear affordance should be intentionally placed, not hidden
- on wide screens, filters may use a single row or a 2x2 block with a utility column

### 10.5 Tables and dense list surfaces

The task list and similar operational surfaces remain **table-first on desktop**.

Rules:

- use a matte or near-opaque data slab for the table body
- use sticky table headers for long-scroll scanning
- treat the table toolbar, header, and pagination as a connected system
- use row hover feedback that feels like a subtle scanner pass, not a heavy color flood
- preserve horizontal space for comparison instead of collapsing into stacked cards on desktop
- use cards only for narrow/mobile layouts

### 10.6 Column economics

Desktop tables should prioritize comparison.

Primary list columns should center on:

- title
- normalized status
- workflow
- runtime
- started time
- duration or updated time
- compact ID

Lower-value metadata should move to detail, expansion, or secondary views when it hurts first-pass scanability.

### 10.7 Status chips

Status chips must be translucent, bordered, compact, and semantically consistent.

Preferred mapping:

- `queued` -> amber / yellow
- `running` / `executing` -> cyan
- `awaiting_action` -> purple
- `succeeded` / `completed` -> green
- `failed` / `cancelled` -> rose / red

Executing states may receive the lightest motion treatment. Finished states should be stable.

### 10.8 Floating rails and sticky utility bars

Floating rails are the best place to emphasize the liquid-glass system.

Rules:

- the rail must read as a truly elevated surface
- its shell may be glass or liquidGL
- internal controls should be more grounded than the shell
- the rail should have clear separation from underlying content via shadow, edge light, and offset
- the rail should be slightly narrower than the content column when that improves intentionality

### 10.9 Cards, panels, and sections

Cards should not all use the same styling weight.

Preferred distinction:

- page shell or section shell: light glass or satin
- control deck: glass
- data slab: matte
- nested cards inside dense regions: slightly darker, more opaque, quieter borders

### 10.10 Modals, drawers, popovers, and toasts

Transient/elevated UI is the right place for glass.

Rules:

- shell may use glass or liquidGL depending on size and importance
- text and fields inside should remain grounded and readable
- shadows and layering must make elevation obvious
- do not stack multiple competing glass effects inside one overlay

### 10.11 Background separators and horizon lines

Use subtle atmospheric separators instead of hard utilitarian dividers everywhere.

Examples:

- horizon line beneath the masthead
- soft orbital gradient behind section transitions
- restrained border glows on major surface edges

---

## 11. Page-specific composition rules

### 11.1 `/tasks/list`

The task list page should use a **control deck + data slab** structure.

Desired composition:

- a compact filter/control deck above the table
- a utility cluster on the right side of that deck
- a distinct table slab below
- pagination and page-size controls visually attached to the table system

Specific guidance:

- the region above the table should not be one giant undifferentiated card
- the upper-right area should be used for utilities and telemetry rather than left empty
- page size belongs with pagination or compact display utilities, not as a primary filter
- primary filters should stay high-signal and compact
- advanced filters may move into a secondary drawer or popover when needed
- active filters should be visible as chips
- the table header should remain sticky during scroll

### 11.2 `/tasks/new`

The create page should feel like a guided launch flow.

Desired composition:

- matte/satin step cards for the primary workflow body
- clear hierarchy across instructions, images, skill selection, presets, and action areas
- a floating launch rail at the bottom as the page’s hero premium surface

Specific guidance:

- the floating launch rail is the strongest candidate for liquidGL treatment in the product
- the rail shell may be premium glass; controls inside remain grounded and crisp
- the primary CTA should feel like a clear launch/commit action
- large textareas should remain matte and comfortable for sustained reading/editing
- small utility buttons inside step cards should align with the same button language as the rest of the system

### 11.3 Task detail and evidence-heavy pages

Detail pages should keep evidence readable and structured.

Desired composition:

- concise summary header
- compact facts rail
- steps/evidence slabs
- observability/log slab
- actions in a distinct elevated or sticky control surface when needed

Do not allow glass effects to compete with evidence density.

---

## 12. Accessibility and performance

### 12.1 Contrast

All decorative styling must maintain clear contrast for:

- labels
- table text
- placeholder text
- chips
- buttons
- focus states
- glass-over-gradient surfaces

### 12.2 Keyboard and focus

Every interactive surface must provide a visible, high-contrast `:focus-visible` state. Focus is a first-class design element, not an implementation afterthought.

### 12.3 Blur and WebGL fallbacks

The system must degrade gracefully when:

- `backdrop-filter` is unavailable
- liquidGL is unavailable or disabled
- device performance is insufficient
- user motion or power-saving preferences suggest a quieter mode

### 12.4 Performance posture

Backdrop blur, glow layers, sticky glass surfaces, and liquidGL targets should be used strategically. Performance is part of the design system.

Heavy premium effects belong on a small number of surfaces with clear value.

---

## 13. Implementation invariants

### 13.1 Semantic class stability

Continue to prefer stable semantic class names for shared Mission Control surfaces, including existing classes such as:

- `dashboard-root`
- `masthead`
- `route-nav`
- `panel`
- `card`
- `toolbar`
- `status-*`
- `queue-*` where compatibility naming still exists

Modifier classes may extend the system, but the semantic shell should remain stable.

Recommended additions include patterns such as:

- `panel--controls`
- `panel--data`
- `panel--floating`
- `panel--utility`
- `table-wrap--wide`

### 13.2 Token-first theming

Mission Control theming is token-first.

Rules:

- semantic classes and Tailwind utilities must consume `--mm-*` tokens
- avoid hardcoded opaque colors when token-based values will do
- avoid scattering many `dark:*` overrides when a token-based surface will adapt automatically
- light and dark themes should remain behaviorally identical, with tokens swapping the atmosphere and surface values

### 13.3 Tailwind and source scanning

Tailwind must scan all Mission Control sources that can contain utility classes, including:

- `api_service/templates/react_dashboard.html`
- `api_service/templates/_navigation.html`
- `frontend/src/**/*.{js,jsx,ts,tsx}`

This remains necessary because Mission Control CSS is built from source before Vite output exists.

### 13.4 Canonical styling source

The source of truth for Mission Control styling remains:

- `frontend/src/styles/mission-control.css`

Generated assets under `api_service/static/task_dashboard/dist/` are build artifacts and should not be hand-edited.

### 13.5 liquidGL enhancement contract

Any liquidGL-enabled surface must satisfy all of the following:

- the component is laid out and styled correctly without WebGL enhancement
- the enhancement is bounded to a specific target surface
- the foreground text and controls remain readable independent of refraction
- browser/performance fallback resolves to the standard CSS glass system without breaking the component
- the effect is used because the surface is elevated and interactive, not merely because the page has room for more shine

---

## 14. Summary

Mission Control’s design system is built on a simple hierarchy:

- **Space-program structure** for layout and discipline
- **Matte slabs** for dense information and editing
- **Glass and liquidGL** for elevated controls and premium floating surfaces
- **Synthwave lighting** for atmosphere and brand energy
- **Cyberpunk micro-details** for edge, never for noise

The result should feel unmistakably MoonMind: operational, futuristic, stylish, and still highly functional.
