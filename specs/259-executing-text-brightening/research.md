# Research: Executing Text Brightening Sweep

## DESIGN-REQ-001 Physical Sweep Plus Foreground Layer

Decision: Preserve the existing host-level `mm-status-pill-shimmer` background animation and remove the old text pseudo-element from the requested task-list path in favor of real glyph spans.
Evidence: `frontend/src/styles/mission-control.css` already defines executing host metadata, sweep gradients, geometry, and `--mm-executing-sweep-cycle-duration: 1650ms`.
Rationale: The source request frames the current shimmer as the physical sweep layer and asks for a second text-brightening layer.
Alternatives considered: Keep the existing CSS-only pseudo-element. Rejected because it cannot brighten individual letters.
Test implications: CSS contract test must verify the host sweep remains and glyph keyframes exist.

## DESIGN-REQ-002 CSS-Driven Browser Animation

Decision: Use CSS keyframes and per-glyph custom-property delays; do not use timers, `requestAnimationFrame`, or component state to animate.
Evidence: No runtime animation loop is added to `ExecutionStatusPill.tsx`; CSS owns repeated motion.
Rationale: Declarative CSS animation is cheaper for many table rows and stays synchronized through the shared duration token.
Alternatives considered: React interval rerender. Rejected by source requirement.
Test implications: Typecheck/lint plus code review; CSS tests assert animation names and duration token.

## DESIGN-REQ-003 Grapheme Splitting

Decision: Use `Intl.Segmenter` when available, with `Array.from` fallback.
Evidence: Component can safely handle labels beyond plain ASCII executing text.
Rationale: It prevents splitting user-perceived characters when future labels include emoji or localized graphemes.
Alternatives considered: `split('')`. Rejected because it splits UTF-16 code units.
Test implications: Task-list integration verifies executing label becomes one glyph element per current grapheme.

## DESIGN-REQ-004 Timing And Direction

Decision: Use the CSS token duration with a 1650ms fallback in styles and calculate delays against `1650` ms in component code. Set direction to right-to-left to match current sweep start/end tokens.
Evidence: Existing CSS moves from `--mm-executing-sweep-start-x: 135%` to `--mm-executing-sweep-end-x: -135%`.
Rationale: The foreground wave should feel phase-locked with the physical sweep.
Alternatives considered: Left-to-right phase order. Rejected for current token geometry.
Test implications: Render tests assert every glyph receives a millisecond delay; CSS tests assert the shared duration token.

## DESIGN-REQ-005 Accessibility

Decision: Put `aria-label` on executing pill parents and mark the visual glyph wrapper `aria-hidden="true"`.
Evidence: Per-letter spans are visual-only and would otherwise risk letter-by-letter announcements.
Rationale: Assistive technologies should receive the complete status phrase.
Alternatives considered: Let text nodes be read from glyph spans. Rejected by source requirement.
Test implications: Task-list test asserts parent label and hidden glyph wrapper.

## DESIGN-REQ-006 Reduced Motion

Decision: Extend the existing reduced-motion block to disable glyph animation, text shadow, and filter.
Evidence: Existing host animation is already disabled under `prefers-reduced-motion: reduce`.
Rationale: Letter brightening is non-essential motion and should stop when users request reduced motion.
Alternatives considered: Keep glyph color pulse with animation. Rejected by accessibility requirement.
Test implications: CSS test asserts reduced-motion glyph suppression.
