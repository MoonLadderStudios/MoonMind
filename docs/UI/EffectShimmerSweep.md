# Shimmer Sweep Effect — Status-Aware Implementation Design
Status: Active  
Owners: MoonMind Engineering  
Last updated: 2026-07-11

## 1. Intent

Define the shared dashboard shimmer treatment used by active and transition workflow status pills.

The effect communicates **active progress, preparation, or wrap-up** without implying error, urgency, or instability. It should feel like a premium “thinking” shimmer: focused, calm, phase-locked, and readable at small sizes.

The **approved sweep palette is fixed and status-agnostic**: a translucent accent halo (`--mm-accent` at 14%) beneath a translucent accent-2 core (`--mm-accent-2` at 34%), giving the subtle purple/cyan two-tone edge interaction operators reviewed and approved (MM-1036 as shipped). Only the **glyph fallback letter treatment** is status-aware (`currentColor`-derived), because that is the MM-1048 portion that actually rendered and was accepted.

History note: MM-1048 proposed re-tinting the whole sweep from the pill hue with brighter mixes (30% halo, fully opaque whitened core), but the same change broke gradient resolution, so that treatment never rendered. When the shimmer was later restored, the latent MM-1048 palette appeared for the first time and was rejected by the operator as far brighter and thicker than the approved look. The values in this document are the operator-approved on-screen aesthetic; do not change them without an operator-reviewed visual baseline update.

---

## 2. Scope

This document covers the dashboard status-pill shimmer sweep effect.

It defines:

- the status-pill host contract
- the shared moving light-field model
- the fixed approved sweep palette and the status-derived letter treatment
- fill, border, and text mask behavior
- the approved motion path
- reduced-motion, forced-colors, and unsupported text-mask fallbacks
- the React/CSS implementation shape that keeps the effect reusable across list, card, and detail surfaces

It does not define:

- the full status color system for every workflow state
- workflow row, workflow card, or workflow detail layout
- polling, live-update, or workflow execution behavior
- unrelated shimmer effects such as segmented-control thumb shimmer or masked conic border beams

For the complete status color rationale, see `docs/UI/WorkflowStatusColorSemantics.md`.

---

## 3. Current source of truth

```yaml
source_of_truth:
  component:
    path: frontend/src/components/ExecutionStatusPill.tsx
    responsibility:
      - render normal status text for non-shimmer states
      - render accessible active labels with visual glyph spans for shimmer states
      - expose data-label for the text-clipped shimmer mask

  selector_helper:
    path: frontend/src/utils/executionStatusPillClasses.ts
    responsibility:
      - normalize status strings
      - attach shimmer metadata only for explicitly opted-in shimmer states
      - preserve the exact status color class contract

  stylesheet:
    path: frontend/src/styles/dashboard.css
    responsibility:
      - define shimmer geometry, timing, and hue tokens
      - define the shared moving light field
      - render fill, border, and text masks
      - define reduced-motion, forced-colors, and unsupported text-mask fallbacks

  color_semantics:
    path: docs/UI/WorkflowStatusColorSemantics.md
    responsibility:
      - define which exact status hue each pill uses
      - the sweep palette itself is fixed (section 8); only the glyph-fallback letter hue follows the pill color
```

---

## 4. Host contract

```yaml
host:
  kind: status-pill
  metadata_boundary: executionStatusPillProps(status)

  shimmer_eligibility:
    explicit_only: true
    current_normalized_statuses:
      - executing
      - running
      - initializing
      - planning
      - finalizing

  active_metadata:
    className:
      required:
        - status
        - exact status color class such as status-running, status-planning, or status-finalizing
        - state modifier class such as is-executing, is-planning, or is-finalizing
    data-state: normalized_status
    data-effect: shimmer-sweep
    data-shimmer-label: normalized_visible_label

  active_selectors:
    canonical:
      - '.status[data-effect="shimmer-sweep"]'
    compatibility:
      - '.status-running.is-executing'

  non_shimmer_statuses:
    examples:
      - queued
      - scheduled
      - awaiting_slot
      - awaiting_action
      - awaiting_external
      - waiting
      - waiting_on_dependencies
      - succeeded
      - completed
      - failed
      - canceled
      - cancelled
      - no_commit
      - neutral

placement:
  inside_fill: true
  inside_border: true
  outside_bounds: clipped_by_host

text:
  source: host-content
  accessible_label_source: aria-label
  visual_label_source: grapheme_spans
  text_mask_source: data-label
  mutation: none
  case: preserve_normalized_label_case
```

Eligibility must come from `executionStatusPillProps(status)`, not from broad color classes. A pill using `status-running`, `status-planning`, or any other color class must not inherit shimmer unless the helper explicitly adds `data-effect="shimmer-sweep"`.

---

## 5. Design principles

```yaml
principles:
  - active_states_are_explicit_not_inferred_from_color_class_alone
  - sweep_palette_is_the_fixed_operator_approved_accent_two_tone
  - only_the_glyph_fallback_letter_treatment_is_status_aware
  - motion_must_read_as_activity_not_alert
  - text_legibility_must_remain_primary
  - fill_border_and_text_must_share_one_moving_light_field
  - one_shared_effect_model_serves_all_shimmer_statuses
  - effect_must_not_change_layout_or_pill_dimensions
  - effect_must_degrade_to_static_or_simpler_treatments_when_user_or_browser_requires_it
```

The implementation should remain additive. Do not create separate per-status pseudo-element stacks, keyframes, or React wrappers just to change color.

---

## 6. Visual model

The shimmer is a **shared additive light field** exposed through clipped regions. The fill shimmer, border glint, and text shimmer are not independent animations. They all read from the same gradient token and keyframe path so they remain phase-locked.

```yaml
layers:
  base_status_pill:
    role: preserve the normal exact status pill tint
    selector_owner: executionStatusPillProps(status)
    examples:
      executing: status-running / live cyan
      planning: status-planning / near-execution blue
      initializing: status-initializing / near-execution blue
      finalizing: status-finalizing / finalization slate
    opacity_behavior: constant
    motion: none

  shared_light_field:
    role: moving luminous diagonal band with subtle trailing halo
    gradient_token: --mm-status-moving-light-gradient
    gradient_scope: declared on the shimmer host rule (`.status[data-effect="shimmer-sweep"], .status-running.is-executing`), never on `:root`, because its halo/core color inputs only exist at the pill scope
    color_inputs:
      halo: --mm-status-shimmer-halo
      core: --mm-status-shimmer-core
    keyframes: mm-status-pill-shimmer
    shape: two layered soft-edged linear gradients
    travel: slightly horizontally biased diagonal sweep using inverse background-position endpoints
    angle_deg_target: -20
    blend_mode:
      preferred: plus-lighter
      fallback: screen

  fill_mask:
    role: broad interior shimmer across the pill fill
    source: shared_light_field
    selector: active_host::before
    opacity: 0.62
    z_index: 1

  border_mask:
    role: border-ring glint where the shared light field crosses the pill edge
    source: shared_light_field
    selector: active_host::after
    geometry:
      inset: calc(-1 * --mm-executing-border-glint-outset)
      ring_width: --mm-executing-border-glint-width
      clipping: mask-composite exclude / -webkit-mask-composite xor
    opacity: --mm-executing-border-glint-opacity
    z_index: 2

  text_mask:
    role: foreground glyph shimmer where the shared light field crosses the label
    source: shared_light_field
    selector: active_host .status-letter-wave::after
    content: attr(data-label)
    clipping:
      - background-clip: text
      - -webkit-background-clip: text
      - transparent text fill
    emphasis: subtle brightening through filter and additive blend
    z_index: 3 via status-letter-wave

  glyph_pulse_fallback:
    role: compatibility fallback when text clipping is unsupported
    selector: active_host .status-letter-wave__glyph
    default_path: false
    enabled_only_under: '@supports not ((background-clip: text) or (-webkit-background-clip: text))'
    keyframes: mm-executing-letter-brighten
```

The gradient token is `--mm-status-moving-light-gradient` and it is declared inside the shimmer host rule. Declaring it on `:root` is a regression: the halo/core custom properties it references are only defined on shimmer pills, so a root-scoped declaration computes to the guaranteed-invalid value and every consuming layer renders `background-image: none` even though the animation keeps running.

---

## 7. Motion profile

```yaml
motion:
  cycle:
    duration_ms: 2600
    css_token: --mm-executing-sweep-cycle-duration
    repeat: infinite
    repeat_delay_ms: 0
    easing: linear

  path:
    start_x_pct: 135
    start_y_pct: 160
    end_x_pct: -135
    end_y_pct: -160
    y_behavior: diagonal_travel
    angle_deg: -18
    keyframes: mm-status-pill-shimmer
    note: >
      These are the approved on-screen values (MM-1036 as shipped). MM-1048's
      -20deg / +/-120% horizontal-bias refinement never rendered (its own
      commit broke gradient resolution) and is retired. Do not turn the shimmer
      into a scanner beam or horizontal loading bar.

  band:
    width_pct: 24
    height_pct: 180
    halo_width_multiplier: 10
    core_width_multiplier: 9.1667
    halo_opacity: 0.14
    core_opacity: 0.34
    layer_offset_x_pct: -12
    layer_offset_y_pct: -10

  pacing:
    entry: continuous
    midpoint: brightest_at_text_centerline
    exit: continuous

  continuity:
    allow_overlap_between_cycles: false
    idle_gap_present: false
    center_pause_present: false

  text_mask:
    timing_source: shared_light_field_keyframes
    duration_source: --mm-executing-sweep-cycle-duration
    phase_locked_to_fill_and_border: true

  glyph_fallback:
    timing_source: --mm-executing-letter-cycle-duration
    sweep_start_ratio: 0.20
    sweep_travel_ratio: 0.18
    direction: 1
    note: fallback glyph brightening is not the primary path and exists only for browsers without text clipping support
```

The motion profile is settled: `-18deg` with `±160%` vertical travel is the approved on-screen path. Any future motion refinement must ship with an operator-reviewed visual baseline update and matching computed-style test changes in the same PR.

---

## 8. Shimmer palette

The **sweep palette is fixed and status-agnostic** — the operator-approved MM-1036 look. Only the glyph-fallback letter treatment derives from the pill hue.

```yaml
shimmer_palette:
  sweep:
    halo: rgb(var(--mm-accent) / var(--mm-executing-sweep-halo-opacity))   # purple @ 14%
    core: rgb(var(--mm-accent-2) / var(--mm-executing-sweep-core-opacity)) # cyan @ 34%
    translucency: every gradient stop stays translucent (max alpha 0.34); an opaque or whitened stop is a regression
    status_aware: false
  letters:
    halo: color-mix(in srgb, currentColor 32%, transparent)
    bright: color-mix(in srgb, currentColor 68%, white 32%)
    status_aware: true
```

Canonical CSS shape (declared on the shimmer host rule):

```css
.status[data-effect="shimmer-sweep"] {
  --mm-status-shimmer-halo: rgb(var(--mm-accent) / var(--mm-executing-sweep-halo-opacity));
  --mm-status-shimmer-core: rgb(var(--mm-accent-2) / var(--mm-executing-sweep-core-opacity));
  --mm-status-shimmer-letter-halo: color-mix(in srgb, currentColor 32%, transparent);
  --mm-status-shimmer-letter-bright: color-mix(in srgb, currentColor 68%, white 32%);
}
```

MM-1048's status-derived sweep hue binding (`color-mix` from `currentColor`, whitened opaque core) is retired: it never rendered while it was in the tree, and when finally revealed it read as a different, far brighter effect than the approved design. If a status-aware sweep is ever wanted, it must be proposed as a new visual change with operator review and updated computed-style baselines — not reintroduced as part of a repair.

---

## 9. React rendering contract

`ExecutionStatusPill` renders non-shimmer states as a simple status span.

```tsx
<span {...executionStatusPillProps(status)}>{label}</span>
```

For shimmer states, it renders an accessible parent label plus hidden visual glyph markup.

```tsx
<span {...executionStatusPillProps(status)} aria-label={label}>
  <span className="status-letter-wave" aria-hidden="true" data-label={label}>
    <span className="status-letter-wave__glyph">...</span>
  </span>
</span>
```

```yaml
text_rendering:
  label_source: formatStatusLabel(status)
  active_visual_markup:
    parent:
      aria-label: complete_label
    visual_container:
      class: status-letter-wave
      aria-hidden: true
      data-label: complete_label
    glyphs:
      class: status-letter-wave__glyph
      split_strategy:
        preferred: Intl.Segmenter(granularity: grapheme)
        fallback: Array.from(label)
      whitespace_strategy: render_space_as_non_breaking_space
      inline_custom_properties:
        - --mm-letter-count
        - --mm-letter-index

accessibility:
  complete_label_exposed_once: true
  visual_glyphs_hidden_from_assistive_tech: true
  text_mask_overlay_is_decorative: true
  non_shimmer_statuses_do_not_render_glyph_wave_markup: true
```

No React changes are needed solely to vary shimmer hue or angle. Those are CSS-token concerns.

---

## 10. CSS implementation contract

```yaml
implementation_shape:
  host_selector:
    canonical: '.status[data-effect="shimmer-sweep"]'
    compatibility: '.status-running.is-executing'

  host_rules:
    position: relative
    overflow: hidden
    isolation: isolate
    background_color: preserve exact status class background
    sweep_palette_tokens: fixed accent halo/core declared on the host (see section 8)
    letter_hue_tokens: derive from currentColor

  rendering_strategy:
    fill_mask: host::before
    border_mask: host::after
    text_mask: .status-letter-wave::after
    fallback_glyphs: .status-letter-wave__glyph only under unsupported text clipping

  shared_animation:
    gradient_token: --mm-status-moving-light-gradient (declared on the shimmer host rule)
    keyframes: mm-status-pill-shimmer
    duration_token: --mm-executing-sweep-cycle-duration
    timing_function: linear

  compositing:
    preferred: mix-blend-mode plus-lighter
    fallback: mix-blend-mode screen

  avoid:
    - per_status_duplicate_keyframes
    - per_status_duplicate_pseudo_element_stacks
    - javascript_animation_loop
    - requestAnimationFrame
    - setInterval_or_setTimeout_animation
    - font_weight_animation
    - React_state_updates_per_animation_frame
    - page_local_status_pill_wrappers
```

Palette and angle changes stay CSS-token updates — no DOM, layout, or animation-model changes — but they are **never** low-ceremony: the approved values are pinned by computed-style browser tests and require an operator-reviewed baseline update in the same PR.

---

## 11. Fallback and accessibility behavior

```yaml
reduced_motion:
  trigger: '@media (prefers-reduced-motion: reduce)'
  animation: disabled
  active_metadata_preserved: true
  replacement:
    static_host_background_gradient: true
    pseudo_element_animation: none
    text_mask_animation: none
    text_mask_content: none
    glyph_animation: none !important
    glyph_shadow_filter: none !important
  preserved_signals:
    - active_status_still_reads_as_active
    - no_animation_required_for_comprehension

unsupported_text_clipping:
  trigger: '@supports not ((background-clip: text) or (-webkit-background-clip: text))'
  text_mask:
    content: none
  glyph_fallback:
    animation_name: mm-executing-letter-brighten
    animation_duration: --mm-executing-letter-cycle-duration
    animation_delay: derived_from_letter_phase
    hue: status-derived letter bright/halo variables

unsupported_additive_blend:
  trigger: '@supports not (mix-blend-mode: plus-lighter)'
  replacement:
    mix_blend_mode: screen

forced_colors:
  trigger: '@media (forced-colors: active)'
  text_color: ButtonText
  decorative_masks:
    content: none
    animation: none
  glyph_animation: none
```

---

## 12. State matrix

```yaml
state_matrix:
  # The sweep palette is identical for every shimmer state (fixed accent
  # two-tone); only the glyph-fallback letter hue follows the pill color.
  executing:
    shimmer_sweep: on

  running:
    shimmer_sweep: on

  initializing:
    shimmer_sweep: on

  planning:
    shimmer_sweep: on

  finalizing:
    shimmer_sweep: on

  queued:
    shimmer_sweep: off

  scheduled:
    shimmer_sweep: off

  awaiting_slot:
    shimmer_sweep: off

  paused:
    shimmer_sweep: off

  waiting:
    shimmer_sweep: off

  waiting_on_dependencies:
    shimmer_sweep: off

  awaiting_action:
    shimmer_sweep: off

  awaiting_external:
    shimmer_sweep: off

  succeeded:
    shimmer_sweep: off

  completed:
    shimmer_sweep: off

  failed:
    shimmer_sweep: off

  canceled:
    shimmer_sweep: off

  cancelled:
    shimmer_sweep: off

  no_commit:
    shimmer_sweep: off
```

---

## 13. Semantic feel

```yaml
tone:
  emotional_read: focused, intelligent, in-progress
  forbidden_reads:
    - error_flash
    - warning_pulse
    - disco_glow
    - scanner_beam
    - loading-skeleton-placeholder
    - rainbow_loading_skeleton

  similarity_targets:
    - codex-thinking-adjacent
    - premium-terminal-ui
    - ambient-sci-fi-control-surface
```

---

## 14. Target token block

```yaml
effect_tokens:
  --mm-executing-sweep-cycle-duration: 2600ms
  --mm-executing-sweep-angle: -18deg
  --mm-executing-sweep-band-width: 24%
  --mm-executing-sweep-band-height: 180%
  --mm-executing-sweep-halo-width-multiplier: 10
  --mm-executing-sweep-core-width-multiplier: 9.1667
  --mm-executing-sweep-core-opacity: 0.34
  --mm-executing-sweep-halo-opacity: 0.14
  --mm-executing-sweep-start-x: 135%
  --mm-executing-sweep-start-y: 160%
  --mm-executing-sweep-end-x: -135%
  --mm-executing-sweep-end-y: -160%
  --mm-executing-sweep-layer-offset-x: -12%
  --mm-executing-sweep-layer-offset-y: -10%
  --mm-executing-border-glint-outset: 1px
  --mm-executing-border-glint-width: 3px
  --mm-executing-border-glint-opacity: 0.95
  --mm-executing-letter-cycle-duration: var(--mm-executing-sweep-cycle-duration)
  --mm-executing-letter-sweep-start-ratio: 0.2
  --mm-executing-letter-sweep-travel-ratio: 0.18
  --mm-executing-letter-sweep-direction: 1
  --mm-status-shimmer-halo: rgb(var(--mm-accent) / var(--mm-executing-sweep-halo-opacity))
  --mm-status-shimmer-core: rgb(var(--mm-accent-2) / var(--mm-executing-sweep-core-opacity))
  --mm-status-shimmer-letter-halo: status-derived fallback glyph halo (currentColor mix)
  --mm-status-shimmer-letter-bright: status-derived fallback glyph bright color (currentColor mix)
  --mm-status-moving-light-gradient: shared halo/core linear-gradient stack (pill-scoped, never :root)
```

The `--mm-executing-*` geometry names are retained for compatibility even though the effect serves every shimmer status. Do not rename them in isolation unless all tests and references are migrated together.

---

## 15. Acceptance criteria

```yaml
acceptance_criteria:
  - only statuses explicitly listed by executionStatusPillProps receive shimmer metadata
  - executing, running, initializing, planning, and finalizing receive data-effect shimmer-sweep while enabled
  - non-shimmer statuses do not render glyph-wave markup
  - active shimmer hosts match the shared selector contract and remain reusable across list, card, and detail surfaces
  - the sweep renders the fixed approved palette (translucent accent halo, translucent accent-2 core) with no opaque stop
  - only the glyph-fallback letter treatment derives from the pill hue
  - fill, border, and text masks use the same moving light-field token and keyframe animation
  - the sweep travels the approved -18deg / +/-160% path
  - the active pill remains readable throughout the sweep
  - the shimmer never escapes the rounded visual bounds of the pill
  - the effect produces no measurable layout shift
  - one complete shimmer sweep occurs roughly every 2.6 seconds with no center pause or idle delay
  - the border glint reads as a ring overlapping the physical border, not as a purely interior hairline
  - glyph-rendered active labels expose a text-clipped shimmer overlay through data-label
  - per-glyph brightening is inactive by default and used only as an unsupported text-clipping fallback
  - the effect looks intentional in both light and dark themes
  - reduced-motion users see a static active treatment with no animated sweep
  - forced-colors users receive readable system text and no decorative shimmer masks
```

---

## 16. Verification expectations

```yaml
verification:
  css_contract_tests:
    assert:
      - shimmer color variables exist on the shimmer host and carry the approved fixed accent palette
      - shared gradient token consumes those host-scoped variables (never :root-scoped)
      - fill mask uses shared gradient and mm-status-pill-shimmer
      - border mask uses shared gradient, mm-status-pill-shimmer, and ring mask geometry
      - text mask uses shared gradient, mm-status-pill-shimmer, content attr(data-label), and text clipping
      - target angle and horizontal-bias tokens are documented in CSS tests
      - glyph fallback is disabled by default
      - unsupported text clipping enables glyph fallback with status-derived bright/halo variables
      - reduced-motion disables animation and removes text-mask content
      - forced-colors disables decorative mask content

  selector_boundary_tests:
    assert:
      - executing receives data-effect shimmer-sweep
      - running receives data-effect shimmer-sweep
      - initializing receives data-effect shimmer-sweep
      - planning receives data-effect shimmer-sweep
      - finalizing receives data-effect shimmer-sweep
      - non-shimmer statuses do not receive data-effect shimmer-sweep
      - status class mapping remains compatible with existing status styles

  render_tests:
    assert:
      - active workflow-list and workflow-detail pills preserve text content
      - active pills expose aria-label once
      - active visual glyphs are aria-hidden
      - non-shimmer pills render without glyph-wave markup
```

---

## 17. Non-goals

```yaml
non_goals:
  - spinning_indicators
  - pulsing_whole_pill_opacity
  - multi_color_rainbow_motion
  - progress_percentage_visualization
  - execution_time_estimation
  - using broad status color classes as shimmer triggers
  - making glyph pulse the primary text shimmer path
  - duplicating the shimmer implementation per status
```

---

## 18. Hand-off note

The shimmer sweep is implemented as a **shared status-pill modifier**. New surfaces should render `ExecutionStatusPill` or use the same `executionStatusPillProps()` metadata boundary instead of creating page-local shimmer markup.

New shimmer states must be added deliberately through the helper, tests, and this document before they receive `data-effect="shimmer-sweep"`. When a state receives shimmer, its shimmer hue must be status-derived by default so the visual motion reinforces the state’s semantic color instead of implying `executing`.
