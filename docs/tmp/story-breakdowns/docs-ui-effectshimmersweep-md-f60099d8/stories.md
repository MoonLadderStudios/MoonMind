# Shimmer Sweep Effect Story Breakdown

- Source design: `docs/UI/EffectShimmerSweep.md`
- Story extraction date: `2026-04-23T00:02:47Z`
- Requested output mode: `jira`
- Coverage gate: `PASS - every major design point is owned by at least one story.`

## Design Summary

The EffectShimmerSweep design defines one reusable shimmer-sweep modifier for status pills when a workflow is executing. The treatment must attach to existing pill markup, stay inside the pill bounds, preserve text and layout, derive its colors from MoonMind theme tokens, and convey calm active progress rather than alerting or placeholder loading. The design also defines reduced-motion behavior, explicit state gating so non-executing states never inherit the shimmer, visual motion parameters, isolation rules, and a hand-off expectation that the effect be shared across list, card, and detail surfaces rather than implemented page-locally.

## Coverage Points

- **DESIGN-REQ-001 - Reusable executing-state shimmer treatment** (requirement, Intent): Define one reusable shimmer sweep for the executing workflow state that communicates active progress without error, urgency, or instability.
- **DESIGN-REQ-002 - Effect scope excludes broader status and layout changes** (non-goal, Scope; Non-Goals): The work covers only the shimmer effect and excludes status color mapping, border glints, layout changes, icon changes, polling/live updates, spinners, whole-pill pulsing, rainbow motion, percentages, and execution-time estimation.
- **DESIGN-REQ-003 - Host contract attaches as a status-pill modifier** (integration, Host Contract; Hand-off Note): The effect attaches to an existing status-pill host when workflow_state is executing and motion is not reduced, preferably through data-state/data-effect hooks, with .is-executing as an acceptable fallback, and should be shared across list, card, and detail surfaces.
- **DESIGN-REQ-004 - Text content is preserved and remains primary** (constraint, Host Contract; Design Principles; Implementation Shape; Acceptance Criteria): The host text remains source content with no mutation or case change; text renders above overlays and remains readable throughout the sweep with only minimal color shift.
- **DESIGN-REQ-005 - Overlay stays inside bounds without layout change** (constraint, Host Contract; Design Principles; Isolation Rules; Acceptance Criteria): The shimmer is inside the fill and border, never outside bounds, uses hidden overflow and relative positioning, and causes no layout shift, dimension changes, reflow, or scrollbar interaction.
- **DESIGN-REQ-006 - Layered visual model uses base, sweep band, and halo** (requirement, Visual Model; Implementation Shape): The normal executing pill appearance is preserved while a soft diagonal luminous band and wider dim trailing halo travel together, preferably rendered as pseudo-element overlays.
- **DESIGN-REQ-007 - Motion profile and pacing match declared values** (requirement, Motion Profile; Suggested Token Block; Acceptance Criteria): The sweep repeats infinitely with a 1450 ms cycle, 220 ms delay, declared easing, left-to-right travel from -135% to 135%, -18 degree angle, 24% band width, specified opacity/blur values, no cycle overlap, and a visible idle gap; the complete sweep reads roughly every 1.6 to 1.8 seconds including delay.
- **DESIGN-REQ-008 - Theme binding derives from existing MoonMind tokens** (constraint, Theme Binding; Design Principles; Acceptance Criteria): The effect derives base tint, core, halo, and text-protection roles from existing MoonMind theme tokens before adding new tokens and must look intentional in light and dark themes.
- **DESIGN-REQ-009 - Isolation and interaction safety are enforced** (security, Isolation Rules): Effect overlays have pointer-events and hit testing disabled, maintain base/shimmer/text z-index ordering, and do not interfere with host interaction or scrolling.
- **DESIGN-REQ-010 - Reduced-motion users receive a static active treatment** (requirement, Reduced Motion Behavior; Host Contract; Acceptance Criteria): When motion preference is reduce, animation is disabled and replaced with a static inner highlight and subtle border emphasis while executing still reads as active without requiring animation.
- **DESIGN-REQ-011 - Only executing state enables shimmer sweep** (state-model, State Matrix; Host Contract; Acceptance Criteria): The shimmer is on only for executing; idle, paused, waiting_on_dependencies, awaiting_external, succeeded, failed, and canceled are off, with finalizing only an optional future variant.
- **DESIGN-REQ-012 - Semantic feel avoids alert and placeholder readings** (constraint, Semantic Feel; Intent): The effect should read as focused, intelligent, and in-progress while avoiding error flash, warning pulse, disco glow, scanner beam, and loading-skeleton-placeholder interpretations.

## Ordered Story Candidates

### STORY-001: Add shared executing status-pill shimmer sweep modifier

- Short name: `executing-shimmer-modifier`
- Source reference: `docs/UI/EffectShimmerSweep.md` sections Intent, Host Contract, Design Principles, Visual Model, Implementation Shape, Hand-off Note
- Why: This delivers the core reusable visual treatment and keeps it attached to the existing status-pill component instead of duplicating page-local animations.
- Description: As a Mission Control user, I need executing status pills to show a reusable shimmer sweep modifier so active workflow progress is visible across task list, card, and detail surfaces without changing existing pill text or layout.
- Independent test: Render representative executing status pills in list, card, and detail contexts and assert the shared modifier hooks are present, text content and case are unchanged, overlay layers render above the base and below text, pill dimensions do not change before/after enabling the modifier, and the shimmer is clipped to the rounded pill bounds.
- Dependencies: None
- Needs clarification: None

Acceptance criteria:
- Executing status pills can opt into the effect through data-state/data-effect hooks or the existing .is-executing class without extra layout wrappers.
- The normal executing pill base appearance remains visible beneath the overlay.
- The overlay includes a soft diagonal sweep band and a wider dim trailing halo locked to the same travel path.
- Status text remains readable, unmutated, case-preserved, and visually above the overlay for the full sweep.
- The effect remains inside the rounded fill and border and produces no measurable layout shift, dimension change, text reflow, or scrollbar interaction.
- The implementation is shared by status-pill styling so list, card, and detail surfaces can read consistently.

Scope:
- Add the shimmer sweep as a shared status-pill modifier for the executing workflow state.
- Support preferred hooks [data-state="executing"][data-effect="shimmer-sweep"] and acceptable .is-executing attachment where needed by existing markup.
- Render the sweep with overlay elements such as ::before for the trailing halo and ::after for the sweep band, or an equivalent nested span overlay when pseudo-elements are not viable.
- Keep host text sourced from existing content, preserve case, render text above overlays, and avoid text mutation.
- Ensure the overlay stays within the pill fill and border and does not change host dimensions.

Out of scope:
- Changing task row, card, or detail page layout.
- Changing status icons or status text labels.
- Adding polling, live-update, or execution progress-percentage behavior.
- Implementing border-glint, lens-flare, spinner, or whole-pill pulse variants.

Owned design coverage:
- DESIGN-REQ-001: Owns the reusable executing-state shimmer treatment.
- DESIGN-REQ-003: Owns modifier attachment hooks and shared status-pill placement.
- DESIGN-REQ-004: Owns text preservation and stacking above overlay layers.
- DESIGN-REQ-005: Owns inside-bounds rendering and zero layout shift for the shared modifier.
- DESIGN-REQ-006: Owns the base, sweep band, and trailing halo visual layer structure.

Handoff: Specify a story that implements the shared executing status-pill shimmer modifier with overlay layers, stable attachment hooks, preserved text, clipped bounds, and no layout shift across existing status-pill surfaces.

### STORY-002: Bind shimmer motion and color to MoonMind theme tokens

- Short name: `shimmer-motion-theme`
- Source reference: `docs/UI/EffectShimmerSweep.md` sections Motion Profile, Theme Binding, Semantic Feel, Suggested Token Block, Acceptance Criteria
- Why: The effect quality depends on the exact motion profile, theme binding, and semantic restraint; otherwise the shimmer can read as an alert, scanner, or disconnected decoration.
- Description: As a Mission Control user, I need the executing shimmer to move and glow with the declared timing, pacing, and MoonMind theme-derived colors so the treatment feels premium, calm, and active in both light and dark themes.
- Independent test: Use frontend unit or visual tests to inspect computed CSS variables/keyframes for duration, delay, easing, travel, angle, opacity, and blur values, then capture light and dark theme screenshots or visual assertions confirming centerline brightness, text contrast, and calm active semantics.
- Dependencies: STORY-001
- Needs clarification: None

Acceptance criteria:
- One complete sweep reads roughly every 1.6 to 1.8 seconds including the idle delay.
- The sweep travels left-to-right from off-pill start to off-pill end at the declared diagonal angle.
- The sweep band uses the declared relative width, soft edge intent, core opacity, halo opacity, and blur.
- There is no visual overlap between cycles and an idle gap is perceptible.
- The shimmer derives from existing MoonMind theme tokens before adding any new effect-specific tokens.
- The effect looks intentional in both light and dark themes and does not wash out the text.
- The animation reads as focused active progress, not error, warning, scanner, disco, or skeleton placeholder behavior.

Scope:
- Implement the declared sweep timing, delay, easing, travel positions, angle, band width, opacity, blur, and no-overlap idle gap behavior.
- Expose or use effect tokens matching the suggested token block where local conventions support CSS custom properties.
- Derive executing base tint, shimmer core, shimmer halo, and text-protection roles from existing MoonMind theme tokens.
- Tune light and dark theme rendering so the brightest moment occurs near the pill center and brightens without washing out text.
- Verify the semantic feel avoids warning, error, disco, scanner, and loading-skeleton readings.

Out of scope:
- Introducing a disconnected new color palette for executing status.
- Changing the status color mapping for non-executing workflow states.
- Adding finalizing-state variants beyond leaving room for future work.

Owned design coverage:
- DESIGN-REQ-007: Owns declared motion timing, travel, angle, opacity, blur, and pacing values.
- DESIGN-REQ-008: Owns derivation from MoonMind theme tokens and light/dark theme quality.
- DESIGN-REQ-012: Owns the semantic feel guardrails for calm active progress.

Handoff: Specify a story that tunes the shimmer keyframes, timing, token binding, and theme rendering so the shared modifier matches the declarative motion and semantic quality contract.

### STORY-003: Gate shimmer by workflow state and reduced-motion preference

- Short name: `shimmer-state-accessibility`
- Source reference: `docs/UI/EffectShimmerSweep.md` sections Host Contract, Reduced Motion Behavior, State Matrix, Isolation Rules, Scope, Non-Goals
- Why: State gating and reduced-motion behavior prevent accidental shimmer on non-executing states and ensure the active signal remains understandable without animation.
- Description: As a Mission Control user with or without reduced-motion preferences, I need the shimmer to appear only for executing workflows and degrade to a static active treatment when motion is reduced so status remains accurate and accessible.
- Independent test: Run status-state rendering tests for every state in the matrix with normal and reduced-motion media settings, asserting only executing/non-reduced receives animation, executing/reduced receives static active styling with no animation, all other states have no shimmer, and overlay pointer-events/hit-testing remain disabled.
- Dependencies: STORY-001
- Needs clarification: None

Acceptance criteria:
- Normal-motion executing status pills show the shimmer sweep.
- Reduced-motion executing status pills show a static active treatment with no animated sweep.
- Reduced-motion active treatment includes static inner highlight and subtle border emphasis while keeping text emphasis.
- Non-executing states do not receive shimmer or reduced-motion shimmer fallback styling accidentally.
- Effect overlays do not intercept pointer events, hit testing, or scrolling.
- No out-of-scope indicators, layout changes, icon changes, progress percentages, ETAs, or live-update behavior are introduced.

Scope:
- Apply shimmer animation only when workflow state equals executing and motion preference is not reduce.
- Disable shimmer animation for prefers-reduced-motion and provide a static inner highlight plus subtle border emphasis that still reads as active.
- Ensure idle, paused, waiting_on_dependencies, awaiting_external, succeeded, failed, and canceled states never inherit shimmer styling accidentally.
- Keep finalizing shimmer behavior out of scope except for avoiding accidental activation.
- Disable pointer events and hit testing for effect overlays and preserve base/shimmer/text z-index ordering.
- Add regression coverage for explicit non-goals so unrelated status mappings, icons, layout, live updates, progress percentages, and timing estimates are not introduced as part of this effect.

Out of scope:
- Adding animated behavior for finalizing or any non-executing state.
- Adding progress percentage, ETA, live-update, polling, icon, or broader layout behavior.
- Creating spinner, whole-pill pulse, rainbow, border-glint, or lens-flare alternatives.

Owned design coverage:
- DESIGN-REQ-002: Owns explicit scope exclusions and non-goal regression protection.
- DESIGN-REQ-009: Owns pointer-event, hit-testing, z-index, and interaction isolation constraints.
- DESIGN-REQ-010: Owns reduced-motion fallback and no-animation comprehension.
- DESIGN-REQ-011: Owns the state matrix and executing-only activation behavior.

Handoff: Specify a story that enforces executing-only shimmer activation, reduced-motion static fallback, interaction-safe overlays, and regression checks for non-executing states and explicit non-goals.

## Coverage Matrix

- DESIGN-REQ-001 -> STORY-001
- DESIGN-REQ-002 -> STORY-003
- DESIGN-REQ-003 -> STORY-001
- DESIGN-REQ-004 -> STORY-001
- DESIGN-REQ-005 -> STORY-001
- DESIGN-REQ-006 -> STORY-001
- DESIGN-REQ-007 -> STORY-002
- DESIGN-REQ-008 -> STORY-002
- DESIGN-REQ-009 -> STORY-003
- DESIGN-REQ-010 -> STORY-003
- DESIGN-REQ-011 -> STORY-003
- DESIGN-REQ-012 -> STORY-002

## Dependencies

- STORY-001 has no dependencies and should run first because it establishes the shared modifier surface.
- STORY-002 depends on STORY-001 because motion and theme tuning require the shared modifier to exist.
- STORY-003 depends on STORY-001 because state and reduced-motion gating apply to the shared modifier.

## Out Of Scope

- Broader workflow state color mapping is excluded because the source design only defines the shimmer sweep effect.
- Border-glint, lens-flare, spinner, rainbow, whole-pill pulse, progress percentage, ETA, icon, polling, live-update, and row-layout changes are excluded because they are explicit non-goals.
- `spec.md` generation and `specs/` directory creation are excluded from breakdown and belong to a later specify step.

## Coverage Gate

PASS - every major design point is owned by at least one story.
