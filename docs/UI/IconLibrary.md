# Icon Library
Status: Active Decision
Owners: MoonMind Engineering
Last Updated: 2026-06-23
Jira: MM-734

## 1. Purpose

MoonMind uses `lucide-react` as the default icon library for Mission Control and other React UI surfaces.

MoonMind's frontend is a Vite + React + TypeScript + Tailwind application. Existing UI icons are commonly hand-authored inline SVGs using a 24x24 view box, `currentColor`, rounded strokes, and `strokeWidth="2"`. A clean outline SVG component library fits that visual language better than an icon font or mixed icon pack.

This document records the default icon choice, allowed fallbacks, and the correct way to include icon components in the stack.

## 2. Decision

Use `lucide-react` for standard MoonMind UI icons.

Lucide is the primary choice because it provides:

- compact 24px outline SVG icons that match the existing UI posture;
- broad coverage for navigation, settings, workflow states, actions, and status indicators;
- TypeScript-friendly React components;
- direct per-icon imports that keep bundle inclusion scoped to used icons;
- standalone optimized inline SVG output; and
- ISC licensing.

## 3. Library Comparison

| Library | Recommendation | Why it fits MoonMind | Load-time notes |
| --- | --- | --- | --- |
| `lucide-react` | Primary pick | Clean 24px outline style; matches the existing inline SVG approach; broad enough for app, navigation, settings, workflow state, and status icons; TypeScript-friendly. | Very good when icons are imported directly by name. Only imported icons should be included in the final bundle. Avoid dynamic "import every icon" patterns. |
| `@tabler/icons-react` | Best backup / alternate primary | Larger catalog with a 24x24 grid, 2px stroke, MIT license, and React support. Useful if MoonMind needs many niche workflow, infrastructure, device, status, or category icons. | Good. The React package is built with ES modules and is intended to be tree-shakable. |
| `@heroicons/react` | Good but narrower | Polished, Tailwind-adjacent, MIT licensed, and easy to use for common UI actions. | Good with per-icon imports, but the smaller catalog may run out of metaphors for workflow, agent, and provider concepts. |
| `@phosphor-icons/react` | Use only when multiple weights or more personality are needed | Flexible weights such as thin, light, regular, bold, fill, and duotone can support richer illustration-like states. | Tree-shakable, but some bundlers may eagerly process a very large module set in development unless per-file imports are used. Less attractive than Lucide or Tabler for MoonMind's Vite setup. |
| `react-icons` | Use sparingly, not as the main library | Useful for one-off ecosystem icons from many packs. | ES module imports can include only used icons, but mixed icon families can make the product visually inconsistent. |
| `simple-icons` | Brand logos only | Appropriate for provider and vendor logos such as GitHub, Docker, and adjacent ecosystem tools. | Fine for a few brand icons. It is not a general UI icon set, and brand trademark rules still apply. |

## 4. Stack Inclusion

Install Lucide as a normal frontend dependency when the first Lucide-backed UI change is made:

```bash
npm install lucide-react
```

Import icons directly by name inside the route, page, or component that renders them:

```tsx
import { KeyRound } from 'lucide-react';

export function ProviderProfileIconRow() {
  return (
    <div className="flex items-center gap-2">
      <KeyRound className="size-5" aria-hidden="true" />
      <span>Provider credentials</span>
    </div>
  );
}
```

Default sizing should use Tailwind size utilities such as `size-4`, `size-5`, or `size-6`, depending on the component density. Icons should inherit text color via `currentColor`, and decorative icons must use `aria-hidden="true"`. Icon-only buttons must provide an accessible label through the button or tooltip pattern used by the surrounding component.

## 5. Policy

Use this precedence for icon choices:

- Lucide for all standard UI icons.
- Tabler only when missing domain-specific metaphors become frequent enough that a second outline set is justified.
- Simple Icons only for brand and vendor logos.
- Existing bespoke SVGs only when they represent product-specific artwork or a shape that the shared icon libraries cannot express cleanly.

Do not add icon fonts. MoonMind already loads IBM Plex Sans and IBM Plex Mono; an icon font would add unnecessary font loading complexity and would not match the existing inline SVG implementation style.

Do not use broad dynamic icon registries or all-icon imports:

```tsx
import * as Icons from 'lucide-react';
```

Do not build CMS-style dynamic icon lookup unless the registry is deliberately small, curated, and reviewed for bundle impact. Mission Control already uses route-level lazy loading, so direct imports in page and route components keep icon code naturally scoped to the chunks that need it.

## 6. Adoption Guidance

When replacing hand-authored inline SVGs, choose Lucide icons that preserve the existing visual posture: 24px outline, rounded cap and join, `currentColor`, and 2px stroke. Prefer a close semantic match over a decorative icon.

If Lucide does not cover a repeated domain concept, document the missing metaphor before adding another icon library. If the need is a brand mark, use Simple Icons or an approved brand asset rather than a general UI icon approximation.
