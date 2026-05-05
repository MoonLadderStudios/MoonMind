# Data Model: Shared Additive Shimmer Masks

This story introduces no persistent data model, database table, API payload, or workflow history shape. The entities below describe UI contract state only.

## Active Status Pill

Purpose: A Mission Control status element that represents active execution or planning and opts into the shimmer-sweep visual treatment.

Fields:
- `status`: Source status string rendered as the visible label after existing normalization.
- `data-state`: Active semantic state, expected to be `executing` or `planning` for this story.
- `data-effect`: Effect opt-in marker, expected to be `shimmer-sweep` for active shimmer hosts.
- `className`: Existing status classes, including `status-running` and either `is-executing` or `is-planning`.
- `aria-label`: Complete label exposed to assistive technology.
- `data-label`: Complete label mirrored on `.status-letter-wave` for the clipped text mask.

Validation rules:
- Active shimmer metadata is attached only by `executionStatusPillProps()`.
- Non-active statuses must not receive `data-effect="shimmer-sweep"`.
- `aria-label`, visible glyph text, and `data-label` must represent the same label.

State transitions:
- Non-active to active: active metadata appears and the shared mask layers become eligible to render.
- Active to non-active: active metadata disappears and mask layers no longer match.
- Reduced motion: active metadata remains, but all shimmer animation is disabled.

## Shared Light Field

Purpose: The single moving gradient source used by fill, border, and text masks.

Fields:
- `--mm-executing-moving-light-gradient`: Shared gradient token.
- `--mm-executing-sweep-cycle-duration`: Shared animation duration.
- `--mm-executing-sweep-start-*` / `--mm-executing-sweep-end-*`: Shared motion endpoints.
- `--mm-executing-sweep-layer-offset-*`: Shared halo/core offset.

Validation rules:
- Fill, border, and text masks must use the same gradient token.
- Fill, border, and text masks must use the same keyframe animation and duration.
- Fallback glyph brightening may use the same cycle duration but is not the primary mask path.

State transitions:
- Running animation in normal motion.
- Static positioning in reduced motion.
- Fallback glyph pulse only when primary text clipping is unsupported.

## Mask Treatment

Purpose: A clipped visual region that exposes the shared light field.

Fields:
- `fill_mask`: Status-pill interior mask rendered through `::before`.
- `border_mask`: Status-pill border ring mask rendered through `::after`.
- `text_mask`: Label-shaped mask rendered through `.status-letter-wave::after`.
- `glyph_pulse_fallback`: Existing per-glyph brightening fallback for unsupported text clipping.

Validation rules:
- Mask layers must not change layout dimensions.
- Mask layers must not receive pointer events.
- Text mask must not replace the accessible label.
- Forced-colors mode must disable decorative mask content.
