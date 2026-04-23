# Shimmer Sweep Effect Story Breakdown

- Source design: `docs/UI/EffectShimmerSweep.md`
- Original source reference path: `docs/UI/EffectShimmerSweep.md`
- Story extraction date: `2026-04-23T07:15:25Z`
- Requested output mode: `jira`
- Coverage gate: `PASS - every major design point is owned by at least one story.`

## Design Summary

EffectShimmerSweep.md defines a single reusable shimmer sweep treatment for status pills in the executing workflow state. The design constrains activation to executing hosts, keeps the effect inside the existing pill without markup, text, or layout changes, derives color from existing MoonMind theme tokens, and provides an accessible reduced-motion static fallback. It explicitly excludes broader status mapping, layout, icon, polling, progress, and alternate glint/pulse treatments, while requiring visual regression confidence across states and light/dark themes.

## Coverage Points

- **DESIGN-REQ-001 - Single reusable executing-state motion treatment** (requirement, Intent): The design defines one shared shimmer sweep effect for the executing workflow state that communicates active progress without error, urgency, or instability.
- **DESIGN-REQ-002 - Narrow effect-only scope** (constraint, Scope): The design covers only the shimmer sweep in isolation and excludes status color mapping, border glints, task row layout, icons, polling, and live updates.
- **DESIGN-REQ-003 - Host activation and fallback contract** (integration, Host Contract): The effect attaches to status-pill hosts for semantic_state executing, preferably through data-state/data-effect and acceptably through is-executing, with a reduced-motion fallback trigger.
- **DESIGN-REQ-004 - Placement and text preservation** (constraint, Host Contract): The shimmer must remain inside the pill fill and border, never outside bounds, and must not mutate host text content or casing.
- **DESIGN-REQ-005 - Attachable, layout-stable design principles** (constraint, Design Principles): The treatment must attach to existing status-pill markup, preserve pill dimensions and text legibility, use existing theme tokens first, and degrade to a static highlight for reduced motion.
- **DESIGN-REQ-006 - Three-layer visual model** (requirement, Visual Model): The effect preserves the normal executing base, adds a soft diagonal moving sweep band, and includes a subtler trailing halo locked to the sweep.
- **DESIGN-REQ-007 - Motion timing and path profile** (state-model, Motion Profile): The sweep uses a 1450ms cycle, 220ms delay, easing, left-to-right travel from -135% to 135%, -18 degree angle, controlled band width, opacity, blur, smooth pacing, and no cycle overlap.
- **DESIGN-REQ-008 - Existing theme-token binding** (requirement, Theme Binding): The shimmer derives base, core, halo, and text-protection roles from existing MoonMind tokens such as --mm-accent, --mm-accent-2, --mm-panel, --mm-border, and --mm-ink.
- **DESIGN-REQ-009 - Isolation and stacking rules** (constraint, Isolation Rules): The host hides overflow, uses relative positioning, keeps effect pointer events and hit testing disabled, stacks base/shimmer/text predictably, and allows no layout shift, text reflow, or scrollbar interaction.
- **DESIGN-REQ-010 - Reduced-motion static active fallback** (requirement, Reduced Motion Behavior): Reduced-motion users receive no animated sweep and instead see a static inner highlight, subtle border emphasis, and preserved text emphasis that still reads as active.
- **DESIGN-REQ-011 - Only executing state owns shimmer** (state-model, State Matrix): The shimmer is off for idle, paused, waiting_on_dependencies, awaiting_external, succeeded, failed, and canceled states; finalizing is only an optional future variant.
- **DESIGN-REQ-012 - Premium active tone and forbidden reads** (constraint, Semantic Feel): The effect should read as focused, intelligent, and in-progress while avoiding error flash, warning pulse, disco glow, scanner beam, and loading-skeleton placeholder impressions.
- **DESIGN-REQ-013 - Shared modifier implementation shape** (integration, Implementation Shape; Hand-off Note): The preferred implementation is a pseudo-element overlay on shared status-pill selectors, with nested span overlay acceptable and extra layout-changing wrappers avoided, so list, card, and detail surfaces are consistent.
- **DESIGN-REQ-014 - Acceptance-level visual quality bar** (requirement, Acceptance Criteria): The completed effect must preserve readability, stay clipped to rounded bounds, avoid measurable layout shift, time sweeps around 1.6-1.8 seconds including delay, brighten near center, work in light and dark themes, support reduced motion, and avoid accidental non-executing activation.
- **DESIGN-REQ-015 - Reusable effect token block** (artifact, Suggested Token Block): The design proposes named CSS custom properties for sweep duration, delay, angle, band width, opacities, blur, travel bounds, and radius inset.
- **DESIGN-REQ-016 - Explicit non-goals for alternate indicators** (non-goal, Non-Goals): The design excludes spinning indicators, whole-pill opacity pulsing, animated border glint, rainbow motion, progress percentage visualization, and execution time estimation.

## Ordered Story Candidates

### STORY-001: Attach executing shimmer as a shared status-pill modifier

- Short name: `executing-shimmer-host`
- Source reference: `docs/UI/EffectShimmerSweep.md`
- Source sections: Intent, Scope, Host Contract, State Matrix, Implementation Shape, Non-Goals, Hand-off Note
- Why: This establishes the correct activation surface and prevents the effect from becoming a page-local or broad status-state change.
- Description: As a Mission Control user, I need executing status pills to opt into one shared shimmer modifier so active workflow progress is visible consistently without changing status text, icons, task row layout, or update behavior.
- Independent test: Render representative status pills in list, card, and detail contexts and assert that executing hosts receive the shimmer modifier while idle, paused, waiting, awaiting_external, succeeded, failed, and canceled hosts do not; assert text content, casing, dimensions, and row layout remain unchanged.
- Dependencies: None
- Needs clarification: None

Acceptance criteria:
- Executing status-pill hosts can activate the shimmer through the preferred data-state/data-effect selector.
- Existing .is-executing hosts can activate the same shared modifier when needed.
- The modifier does not mutate host text content, casing, icon choice, task row layout, polling, or live-update behavior.
- The shared modifier is available to list, card, and detail status-pill surfaces rather than being page-local.
- Non-executing states never inherit the shimmer accidentally.

Scope:
- Attach the shimmer sweep to existing status-pill markup for executing state only.
- Support the preferred [data-state="executing"][data-effect="shimmer-sweep"] hook and the acceptable .is-executing hook where existing markup requires it.
- Preserve host text source, casing, content, dimensions, and layout.
- Implement the effect as a shared status-pill modifier that can apply consistently across list, card, and detail surfaces.
- Keep the change isolated from status color mapping, icons, task row layout, polling, and live-update behavior.

Out of scope:
- Changing status colors for non-executing workflow states.
- Adding progress percentages, execution time estimates, polling behavior, or live-update semantics.
- Introducing border-glint, lens-flare, spinner, pulse, or icon variants.

Owned design coverage:
- DESIGN-REQ-001: Owns the single shared executing-state treatment.
- DESIGN-REQ-002: Owns the narrow effect-only boundary and excludes unrelated UI behavior.
- DESIGN-REQ-003: Owns the host selector and activation contract.
- DESIGN-REQ-004: Owns text preservation and inside-pill placement at activation time.
- DESIGN-REQ-011: Owns state-matrix activation for executing only.
- DESIGN-REQ-013: Owns shared modifier placement across status-pill surfaces.
- DESIGN-REQ-016: Owns non-goals for alternate indicators and progress displays.

Handoff: Specify one story that adds an executing-only shared status-pill shimmer modifier, preserving text, dimensions, and unrelated status behavior while proving non-executing states do not activate it.

### STORY-002: Render the themed shimmer band and halo layers

- Short name: `themed-shimmer-layers`
- Source reference: `docs/UI/EffectShimmerSweep.md`
- Source sections: Design Principles, Visual Model, Theme Binding, Isolation Rules, Semantic Feel, Implementation Shape, Suggested Token Block
- Why: After the host contract exists, this story delivers the visible treatment while binding it to MoonMind theme vocabulary and isolation rules.
- Description: As a Mission Control user, I need the executing pill shimmer to look like a premium active progress treatment in both light and dark themes, with a luminous diagonal band and subtle halo that keep the status text readable.
- Independent test: Run visual or DOM style tests against a themed executing pill in light and dark modes, asserting the overlay layers exist, derive from expected CSS custom properties, stay below text, keep pointer events disabled, and do not wash out or obscure the label.
- Dependencies: STORY-001
- Needs clarification: None

Acceptance criteria:
- The executing pill keeps its normal base appearance underneath the overlay.
- The shimmer core appears as a soft diagonal bright band and the halo appears wider and dimmer behind it.
- The effect derives color roles from existing MoonMind tokens, with no disconnected one-off palette.
- Text renders above the overlay and remains readable in light and dark themes.
- Overlay hit testing and pointer events are disabled.
- Reusable effect token names cover the suggested tunable values or equivalent implementation variables.

Scope:
- Preserve the normal executing pill base layer.
- Render a soft-edged diagonal sweep band and a wider dimmer trailing halo, preferably using ::after and ::before pseudo-elements.
- Derive base tint, shimmer core, shimmer halo, and text-protection colors from existing MoonMind theme tokens before introducing any new effect token.
- Keep shimmer inside fill and border, with text stacked above the overlay and minimal text color shift during the pass.
- Expose reusable effect custom properties for duration, delay, angle, band width, opacity, blur, travel bounds, and radius inset where implementation needs tunable tokens.

Out of scope:
- Animating the motion profile beyond static positioning needed to render the layers.
- Adding disconnected colors outside the existing MoonMind theme vocabulary.
- Implementing alternate border-glint, lens-flare, scanner, or rainbow effects.

Owned design coverage:
- DESIGN-REQ-005: Owns theme-token-first, text-legible, layout-stable design principles for visual rendering.
- DESIGN-REQ-006: Owns the base, sweep band, and trailing halo layers.
- DESIGN-REQ-008: Owns theme binding for base tint, shimmer core, halo, and text protection.
- DESIGN-REQ-009: Owns overlay stacking, pointer-event, and hit-testing isolation for the rendered layers.
- DESIGN-REQ-012: Owns the premium active tone and avoids alert/disco/scanner/skeleton reads.
- DESIGN-REQ-015: Owns reusable effect token definitions for tunable visual values.

Handoff: Specify one story that renders the shimmer band and halo as themed overlay layers, with text and interaction isolation verified across light and dark theme contexts.

### STORY-003: Animate shimmer motion with reduced-motion fallback

- Short name: `shimmer-motion-fallback`
- Source reference: `docs/UI/EffectShimmerSweep.md`
- Source sections: Host Contract, Motion Profile, Reduced Motion Behavior, Acceptance Criteria, Non-Goals
- Why: This makes the effect communicate activity without urgency while satisfying accessibility requirements for users who disable motion.
- Description: As a user watching an executing workflow, I need the shimmer to move with a calm sweep cadence when motion is allowed and become a static active highlight when reduced motion is requested.
- Independent test: Use CSS animation tests or browser-based checks to assert animation timing, delay, path endpoints, center brightness positioning, and no overlap under normal motion, then emulate prefers-reduced-motion and assert no animated sweep is active while the static active treatment remains visible.
- Dependencies: STORY-001, STORY-002
- Needs clarification: None

Acceptance criteria:
- The sweep travels left-to-right from the configured off-pill start to off-pill end without escaping visible rounded bounds.
- The animation cadence totals roughly 1.6 to 1.8 seconds per sweep including delay.
- The brightest moment occurs near the center of the pill rather than at either edge.
- The sweep uses soft entry and smooth fade exit with no overlap between cycles.
- When reduced motion is requested, the animated sweep is disabled and a static active highlight remains.
- The reduced-motion treatment still communicates executing as active without requiring animation for comprehension.

Scope:
- Animate the sweep left-to-right from -135% to 135% with a fixed vertical path and -18 degree angle.
- Use the specified 1450ms duration, 220ms delay, cubic-bezier(0.22, 1, 0.36, 1) easing, band width, opacity, blur, and smooth entry/exit pacing.
- Prevent cycle overlap and preserve an idle gap between sweeps.
- Ensure one complete sweep occurs roughly every 1.6 to 1.8 seconds including delay and reaches peak brightness near the text centerline.
- Disable animation for reduced-motion users and provide a static active treatment with inner highlight, subtle border emphasis, and preserved text emphasis.

Out of scope:
- Adding progress percentages, runtime time estimates, or variable speed based on execution duration.
- Animating non-executing states or finalizing future variants.
- Whole-pill opacity pulses, spinners, or warning-style flashes.

Owned design coverage:
- DESIGN-REQ-007: Owns declared duration, delay, easing, path, band, pacing, and continuity values.
- DESIGN-REQ-010: Owns disabled animation and static reduced-motion active fallback.
- DESIGN-REQ-012: Owns calm activity semantics during motion and reduced-motion states.
- DESIGN-REQ-014: Owns timing, center-brightness, reduced-motion, and bounds-related acceptance criteria for animation behavior.

Handoff: Specify one story that adds the declared shimmer animation cadence and reduced-motion static fallback, with tests proving timing, center emphasis, no cycle overlap, and disabled animation under reduced motion.

### STORY-004: Guard shimmer quality across states, themes, and layouts

- Short name: `shimmer-regression-guards`
- Source reference: `docs/UI/EffectShimmerSweep.md`
- Source sections: Host Contract, Isolation Rules, Reduced Motion Behavior, State Matrix, Acceptance Criteria, Non-Goals
- Why: The effect is visual and reusable, so acceptance depends on cross-surface verification as much as the implementation itself.
- Description: As a MoonMind maintainer, I need regression coverage for the shimmer effect so future UI changes cannot make it unreadable, layout-shifting, out-of-bounds, or accidentally active on non-executing states.
- Independent test: Run the frontend unit/visual test target for status pills with normal and reduced-motion modes, light and dark themes, multiple states, and representative list/card/detail layouts; assert no layout shift, no text reflow, no out-of-bounds shimmer, and no accidental activation outside executing.
- Dependencies: STORY-001, STORY-002, STORY-003
- Needs clarification: None

Acceptance criteria:
- Automated checks cover executing and every listed non-executing state in the state matrix.
- The executing label remains readable at all sampled points during the sweep.
- The pill dimensions and surrounding layout do not shift when the effect activates or animates.
- The shimmer is clipped to rounded pill bounds and does not interact with scrollbars.
- Light and dark theme snapshots or style assertions show an intentional active treatment.
- Reduced-motion checks prove the static active fallback is present without animation.

Scope:
- Add focused regression coverage for readability throughout the sweep and reduced-motion fallback.
- Verify the shimmer stays clipped inside rounded pill bounds and produces no measurable layout shift or text reflow.
- Verify light and dark theme rendering keeps the effect intentional and not washed out.
- Verify all non-executing state examples stay shimmer-free and finalizing remains out of scope unless a later variant is explicitly added.
- Document or encode the out-of-scope alternate indicator constraints in the relevant tests or review fixtures.

Out of scope:
- Changing the shimmer implementation beyond fixes required to satisfy the regression checks.
- Adding the optional future finalizing variant.
- Creating specs, tasks, or implementation plans during breakdown.

Owned design coverage:
- DESIGN-REQ-004: Verifies placement and text preservation across rendered surfaces.
- DESIGN-REQ-009: Verifies overflow, positioning, stacking, layout-shift, text-reflow, and scrollbar isolation.
- DESIGN-REQ-011: Verifies state matrix behavior for every listed non-executing state.
- DESIGN-REQ-014: Owns full acceptance-level regression coverage.
- DESIGN-REQ-016: Verifies excluded alternate indicators are not introduced as fallback behavior.

Handoff: Specify one story that adds regression coverage and any required implementation tightening for shimmer readability, clipping, state isolation, theme quality, layout stability, and reduced-motion behavior.

## Coverage Matrix

- **DESIGN-REQ-001** -> STORY-001
- **DESIGN-REQ-002** -> STORY-001
- **DESIGN-REQ-003** -> STORY-001
- **DESIGN-REQ-004** -> STORY-001, STORY-004
- **DESIGN-REQ-005** -> STORY-002
- **DESIGN-REQ-006** -> STORY-002
- **DESIGN-REQ-007** -> STORY-003
- **DESIGN-REQ-008** -> STORY-002
- **DESIGN-REQ-009** -> STORY-002, STORY-004
- **DESIGN-REQ-010** -> STORY-003
- **DESIGN-REQ-011** -> STORY-001, STORY-004
- **DESIGN-REQ-012** -> STORY-002, STORY-003
- **DESIGN-REQ-013** -> STORY-001
- **DESIGN-REQ-014** -> STORY-003, STORY-004
- **DESIGN-REQ-015** -> STORY-002
- **DESIGN-REQ-016** -> STORY-001, STORY-004

## Dependencies

- STORY-001 depends on: None
- STORY-002 depends on: STORY-001
- STORY-003 depends on: STORY-001, STORY-002
- STORY-004 depends on: STORY-001, STORY-002, STORY-003

## Out-of-Scope Items and Rationale

- Status color mapping, task row layout changes, icon changes, polling, and live-update behavior are outside this design because the source document scopes only the shimmer sweep effect in isolation.
- Border-glint, lens-flare, spinner, whole-pill pulse, rainbow, progress percentage, and execution-time treatments are excluded so the executing state keeps one calm shimmer treatment instead of competing indicators.
- A finalizing shimmer variant remains optional future work and is not included in the executing-state story set.

## Coverage Gate

PASS - every major design point is owned by at least one story.
