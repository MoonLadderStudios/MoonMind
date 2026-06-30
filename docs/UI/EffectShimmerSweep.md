# Shimmer Sweep Effect — Status-Aware Implementation Design
Status: Active  
Owners: MoonMind Engineering  
Last updated: 2026-06-29

## 1. Intent

Define the shared dashboard shimmer treatment used by active and transition workflow status pills.

The effect communicates **active progress, preparation, or wrap-up** without implying error, urgency, or instability. It should feel like a premium “thinking” shimmer: focused, calm, phase-locked, readable at small sizes, and tied to the status pill’s semantic color.

The shimmer must **not** always render in the executing/cyan hue. If a `planning` pill is blue, its shimmer should read as blue. If a `finalizing` pill is slate, its shimmer should read as slate. `executing` and `running` remain cyan because their pill color remains the live/executing hue.

---

## 2. Scope

This document covers the dashboard status-pill shimmer sweep effect.

It defines:

- the status-pill host contract
- the shared moving light-field model
- status-derived shimmer hue binding
- fill, border, and text mask behavior
- the small horizontal-bias motion refinement
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
      - require status-aware shimmer hue when shimmer is enabled
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
  - shimmer_hue_matches_the_status_pill_hue
  - executing_hue_is_not_reused_for_non_executing_statuses
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
    gradient_token:
      current_compatibility_name: --mm-executing-moving-light-gradient
      preferred_future_alias: --mm-status-moving-light-gradient
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

The token name `--mm-executing-moving-light-gradient` may remain for compatibility, but the colors inside that gradient should no longer be hard-coded to executing tokens. A later cleanup may add the neutral alias `--mm-status-moving-light-gradient` once tests and downstream references are migrated.

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
    start_y_pct_target: 120
    end_x_pct: -135
    end_y_pct_target: -120
    y_behavior: diagonal_travel
    horizontal_bias: horizontal travel delta (270%) exceeds vertical travel delta (240%)
    angle_deg_target: -20
    previous_values:
      angle_deg: -24
      start_y_pct: 128
      end_y_pct: -128
    keyframes: mm-status-pill-shimmer
    note: small refinement only; do not turn the shimmer into a scanner beam or horizontal loading bar

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

The desired motion change is intentionally small: the sweep should read **a little less vertical and a little more horizontal** than the previous `-24deg` / `±128%` vertical-travel treatment. The first implementation target is `-20deg` with a modest vertical-travel reduction to `±120%`; visual QA may keep the travel endpoints unchanged if the angle adjustment alone is sufficient.

---

## 8. Status hue binding

The shimmer hue derives from the same semantic hue as the status pill. Prefer one shared status-derived token pair over copied per-status shimmer rules.

```yaml
status_hue_binding:
  source_of_truth: exact status pill color class
  implementation_preference:
    - use currentColor or status-local CSS custom properties on the host
    - feed those values into the shared gradient token
    - let ::before, ::after, and .status-letter-wave::after inherit the same hue inputs

  expected_hues:
    executing:
      pill_class: status-running
      shimmer_hue: live/executing cyan
      token: --mm-accent-2
    running:
      pill_class: status-running
      shimmer_hue: live/executing cyan
      token: --mm-accent-2
    initializing:
      pill_class: status-initializing
      shimmer_hue: near-execution blue
      token: --mm-status-setup
    planning:
      pill_class: status-planning
      shimmer_hue: near-execution blue
      token: --mm-status-setup
    finalizing:
      pill_class: status-finalizing
      shimmer_hue: finalization slate
      token: --mm-status-finalizing
```

Suggested CSS shape:

```css
.status[data-effect="shimmer-sweep"] {
  --mm-status-shimmer-halo: color-mix(in srgb, currentColor 30%, transparent);
  --mm-status-shimmer-core: color-mix(in srgb, currentColor 70%, white 30%);
  --mm-status-shimmer-letter-halo: color-mix(in srgb, currentColor 32%, transparent);
  --mm-status-shimmer-letter-bright: color-mix(in srgb, currentColor 68%, white 32%);
}
```

The exact percentages may be tuned for contrast, but the binding principle is stable: **the shimmer follows the pill hue**. `planning` should not receive the executing/cyan shimmer merely because the original shimmer was created for `executing`.

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
    hue_tokens: derive from currentColor or status-local variables

  rendering_strategy:
    fill_mask: host::before
    border_mask: host::after
    text_mask: .status-letter-wave::after
    fallback_glyphs: .status-letter-wave__glyph only under unsupported text clipping

  shared_animation:
    gradient_token: --mm-executing-moving-light-gradient until neutral alias migration
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

Changing hue and angle should remain a low-risk CSS token update. It should not add DOM, change layout, or add animation work beyond the existing pseudo-element/text-mask animation.

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
  executing:
    shimmer_sweep: on
    shimmer_hue: live/executing cyan

  running:
    shimmer_sweep: on
    shimmer_hue: live/executing cyan

  initializing:
    shimmer_sweep: on
    shimmer_hue: near-execution blue

  planning:
    shimmer_sweep: on
    shimmer_hue: near-execution blue

  finalizing:
    shimmer_sweep: on
    shimmer_hue: finalization slate

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
  --mm-executing-sweep-angle: -20deg
  --mm-executing-sweep-band-width: 24%
  --mm-executing-sweep-band-height: 180%
  --mm-executing-sweep-halo-width-multiplier: 10
  --mm-executing-sweep-core-width-multiplier: 9.1667
  --mm-executing-sweep-core-opacity: 0.34
  --mm-executing-sweep-halo-opacity: 0.14
  --mm-executing-sweep-start-x: 135%
  --mm-executing-sweep-start-y: 120%
  --mm-executing-sweep-end-x: -135%
  --mm-executing-sweep-end-y: -120%
  --mm-executing-sweep-layer-offset-x: -12%
  --mm-executing-sweep-layer-offset-y: -10%
  --mm-executing-border-glint-outset: 1px
  --mm-executing-border-glint-width: 3px
  --mm-executing-border-glint-opacity: 0.95
  --mm-executing-letter-cycle-duration: var(--mm-executing-sweep-cycle-duration)
  --mm-executing-letter-sweep-start-ratio: 0.2
  --mm-executing-letter-sweep-travel-ratio: 0.18
  --mm-executing-letter-sweep-direction: 1
  --mm-status-shimmer-halo: status-derived halo color
  --mm-status-shimmer-core: status-derived core color
  --mm-status-shimmer-letter-halo: status-derived fallback glyph halo
  --mm-status-shimmer-letter-bright: status-derived fallback glyph bright color
  --mm-executing-moving-light-gradient: shared halo/core linear-gradient stack using status-derived inputs
```

The `--mm-executing-*` geometry names are retained for compatibility even though the visual effect is now status-aware. Do not rename them in isolation unless all tests and references are migrated together.

---

## 15. Acceptance criteria

```yaml
acceptance_criteria:
  - only statuses explicitly listed by executionStatusPillProps receive shimmer metadata
  - executing, running, initializing, planning, and finalizing receive data-effect shimmer-sweep while enabled
  - non-shimmer statuses do not render glyph-wave markup
  - active shimmer hosts match the shared selector contract and remain reusable across list, card, and detail surfaces
  - the shimmer hue follows the status pill hue rather than always using executing cyan
  - planning and initializing shimmer as near-execution blue, not live/executing cyan
  - fill, border, and text masks use the same moving light-field token and keyframe animation
  - the shimmer angle/path reads slightly less vertical and more horizontal than the previous -24deg treatment
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
      - status-derived shimmer color variables exist on the shimmer host
      - shared gradient token uses status-derived color variables instead of hard-coded executing colors
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
