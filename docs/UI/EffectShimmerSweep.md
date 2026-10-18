# Shimmer Sweep Effect

The shared status-pill shimmer communicates active progress, preparation, or wrap-up with calm, readable motion. It is not an error flash, loading skeleton, percentage indicator, or time estimate. Normal status color and text remain understandable without animation.

## Appearance

The approved sweep palette is fixed across eligible statuses: an accent halo (`--mm-accent`, 14% opacity) under an accent-2 core (`--mm-accent-2`, 34%). All gradient stops remain translucent. Only the fallback glyph brightening uses the pill's `currentColor`; changing the sweep itself to an opaque or whitened status-derived color produces a brighter, thicker effect and is a visual change, not a repair.

Fill, border glint, and text use one phase-locked moving light field. The sweep is a continuous 2.6-second linear cycle at `-28deg`, traveling from `(135%, 160%)` to `(-135%, -160%)`, with no center pause or idle gap. The border glint overlaps the physical ring; host clipping keeps decoration inside the rounded pill. The treatment must not change dimensions or shift layout.

[Dashboard CSS](../../frontend/src/styles/dashboard.css) owns the exact geometry, blend, opacity, and timing tokens, including the retained `--mm-executing-*` names. Review visual/computed-style evidence when deliberately changing appearance; copying token blocks into page-local styles creates competing implementations.

## Shared component and eligibility

Use [ExecutionStatusPill and domain-specific pills](../../frontend/src/components/ExecutionStatusPill.tsx) on list, card, and detail surfaces. Status vocabulary helpers determine explicit shimmer eligibility; broad color classes alone do not opt in. The component dispatches among workflow, step, and integration vocabularies, so a status word from one domain is not a universal animation rule.

[workflowStatus.ts](../../frontend/src/status/workflowStatus.ts) owns workflow normalization, eligibility, and `enableMotion`; [stepStatus.ts](../../frontend/src/status/stepStatus.ts) and [integrationStatus.ts](../../frontend/src/status/integrationStatus.ts) own their domains. [executionStatusPillClasses.ts](../../frontend/src/utils/executionStatusPillClasses.ts) retains compatibility exports. Consult these owners for the current status sets rather than maintaining a second state matrix.

The canonical host selector is `.status[data-effect="shimmer-sweep"]`; CSS also retains `.status-running.is-executing` compatibility. The shared component renders a complete accessible parent label and hides decorative grapheme spans from assistive technology. `data-label` supplies the text-clipped overlay. Non-shimmer pills render ordinary text. There is no animation loop or frame-by-frame React state.

## Why the gradient is pill-scoped

Declare `--mm-status-moving-light-gradient` on the shimmer host, alongside its halo/core inputs. Defining it on `:root` is unsafe: the referenced custom properties exist only on descendant pills, so the root declaration computes to the guaranteed-invalid value. Consumers then render `background-image: none` even while animation continues.

The host's fill pseudo-element, masked border pseudo-element, and text-clipped overlay consume that same gradient and `mm-status-pill-shimmer` keyframes. This keeps all three regions synchronized without separate per-status stacks. Isolation and clipping contain additive blending; `screen` is the fallback when `plus-lighter` is unsupported.

## Accessibility and fallbacks

- Reduced motion disables sweep and glyph animations, removes the text-mask content, and leaves a static active treatment. Status comprehension must not depend on motion.
- Forced colors uses readable system text (`ButtonText`) and removes decorative masks/animation.
- Unsupported text clipping removes the overlay and enables staggered glyph brightening. This is the only status-derived letter shimmer path and is not the primary effect.
- The complete label is exposed once through `aria-label`; visual glyphs and the decorative overlay do not create repeated announcements. Grapheme segmentation preserves combined characters where `Intl.Segmenter` is available.

## Sources and verification

- [Workflow status color semantics](./WorkflowStatusColorSemantics.md) explains base status hues; it does not redefine the fixed sweep palette.
- [Component tests](../../frontend/src/components/ExecutionStatusPill.test.tsx) and [selector tests](../../frontend/src/utils/executionStatusPillClasses.test.ts) cover rendering and eligibility boundaries.
- [Browser shimmer tests](../../frontend/src/browser/statusPillShimmer.browser.test.tsx) cover computed styles and fallback behavior. Use rendered evidence in light/dark themes and accessibility modes for actual visual changes.
