# Research: Executing Text Brightening Sweep

## DESIGN-REQ-001 Physical Sweep Plus Foreground Layer

Decision: Preserve the existing host-level `mm-status-pill-shimmer` background animation and remove the old text pseudo-element from the requested task-list path in favor of real glyph spans.
Evidence: `frontend/src/styles/mission-control.css` already defines executing host metadata, sweep gradients, geometry, and `--mm-executing-sweep-cycle-duration`.
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

Decision: Use the CSS token duration with the configured sweep fallback as the outer cycle, but run the foreground text wave through a shorter active phase inside that cycle. Calculate per-glyph phase ratios from glyph index/count, start the first glyph at the configured active-window ratio, sweep across the word using a smaller travel ratio, then leave the glyphs inactive for the rest of the cycle. Set direction to left-to-right/top-left-to-bottom-right to match the visible sweep described by the canonical UI design.
Evidence: Existing CSS moves from `--mm-executing-sweep-start-x: 135%` to `--mm-executing-sweep-end-x: -135%`, but the oversized background layers mean equal cycle duration alone does not produce equal perceived sweep speed.
Rationale: The foreground wave should feel phase-locked with the physical sweep; a faster text active window plus idle tail matches the visual sweep better than stretching glyph delays across the full outer cycle.
Alternatives considered: Right-to-left phase order. Rejected because the visible sweep direction is left-to-right/top-left-to-bottom-right even though oversized background-position tokens move inversely.
Test implications: Render tests assert every glyph receives index/count values; CSS tests assert the shared duration token plus the active-window start and travel ratios.

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

## DESIGN-REQ-007 Centralized Status Detection

Decision: Keep executing detection delegated to `executionStatusPillProps()` from inside `ExecutionStatusPill`.
Evidence: `frontend/src/components/ExecutionStatusPill.tsx` calls `executionStatusPillProps(status)` before deciding whether to render plain text or glyph-wave markup.
Rationale: The existing helper is the shared selector contract for `data-state="executing"`, `data-effect="shimmer-sweep"`, `data-shimmer-label`, and non-executing isolation. Reusing it avoids duplicating state normalization inside the component.
Alternatives considered: Recompute executing status in the component. Rejected because it would create a second state-classification path.
Test implications: Task-list integration tests assert the existing executing metadata remains present and non-executing pills remain plain.

## DESIGN-REQ-008 Task-List Table And Card Replacement

Decision: Replace only the requested task-list table and card status span call sites with `ExecutionStatusPill` while preserving status source precedence.
Evidence: `frontend/src/entrypoints/tasks-list.tsx` renders `ExecutionStatusPill` in both `.queue-table-cell-status` and `.queue-card-status` with `row.rawState || row.state || row.status`.
Rationale: The source request selected the two task-list surfaces and explicitly said data plumbing should not change.
Alternatives considered: Update every status-pill surface in the app. Rejected as broader than the selected single story.
Test implications: Task-list integration tests inspect both table and card executing pills for glyph markup and non-executing pills for plain rendering.
