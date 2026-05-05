# UI Contract: Shared Additive Status-Pill Shimmer

## Host Contract

An active shimmer host is any status pill matching one of these selectors:

```css
.status-running[data-effect="shimmer-sweep"]
.status-running.is-executing
.status-running.is-planning
```

Required host behavior:
- The host remains `position: relative`, `overflow: hidden`, and `isolation: isolate`.
- The host preserves existing text content, aria label, status classes, dimensions, and pointer behavior.
- The host does not introduce page-local wrappers to enable shimmer.

## Shared Light Field Contract

The shared light field is represented by:

```css
--mm-executing-moving-light-gradient
@keyframes mm-status-pill-shimmer
--mm-executing-sweep-cycle-duration
```

Required mask usage:
- Fill mask: active host `::before` uses the shared gradient and keyframes.
- Border mask: active host `::after` uses the same shared gradient and keyframes, clipped to a border ring that overlaps the physical border and a narrow inner edge.
- Text mask: `.status-letter-wave::after` uses the same shared gradient and keyframes, clipped to visible label text.

## Text Contract

`ExecutionStatusPill` must render active labels with:

```tsx
<span {...executionStatusPillProps(status)} aria-label={label}>
  <span className="status-letter-wave" aria-hidden="true" data-label={label}>
    <span className="status-letter-wave__glyph">...</span>
  </span>
</span>
```

Required text behavior:
- `aria-label` remains the complete assistive label.
- `.status-letter-wave` is hidden from assistive technology.
- `.status-letter-wave__glyph` spans preserve visible grapheme text.
- `data-label` mirrors the complete visible label for the text-mask pseudo-element.

## Fallback Contract

Reduced motion:
- Disable animation on fill mask, border mask, text mask, and glyph fallback.
- Preserve a static active highlight.

Unsupported text clipping:
- Disable `.status-letter-wave::after` content.
- Re-enable `mm-executing-letter-brighten` on `.status-letter-wave__glyph`.

Forced colors:
- Disable decorative mask content and animation.
- Preserve readable system text color.

## Verification Contract

Unit CSS contract tests must assert:
- Shared gradient token exists.
- Fill, border, and text masks use the shared gradient and keyframes.
- Border mask uses a ring mask with explicit overlap geometry, not a purely interior hairline.
- Text mask uses text clipping.
- Glyph fallback is inactive by default and enabled only under unsupported text clipping.
- Reduced-motion and forced-colors branches disable decorative animation.

Unit selector-boundary tests must assert:
- Executing and planning states receive shimmer metadata.
- Non-active states do not receive shimmer metadata.
- The helper preserves the existing status class contract.

Integration-style render tests must assert:
- Active task-list and task-detail pills receive shimmer metadata.
- Non-active states do not receive shimmer metadata.
- Active labels preserve text content, aria label, and glyph spans.
