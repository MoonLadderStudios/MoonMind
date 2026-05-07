# Shimmer Sweep Effect — Current Implementation Design

## Intent

Define the shared Mission Control shimmer treatment used by active status pills.

The effect communicates **active progress** without implying error, urgency, or instability. It should feel like a premium “thinking” shimmer: focused, calm, phase-locked, and readable at small sizes.

## Scope

This document covers the current shimmer sweep effect for Mission Control status pills.

It defines:

- the active status-pill host contract
- the shared moving light-field model
- fill, border, and text mask behavior
- reduced-motion, forced-colors, and unsupported text-mask fallbacks
- the React/CSS implementation shape that keeps the effect reusable across list, card, and detail surfaces

It does not define:

- the full status color system for every workflow state
- task row, task card, or task detail layout
- polling, live-update, or workflow execution behavior
- unrelated shimmer effects such as segmented-control thumb shimmer or masked conic border beams

## Current Source of Truth

```yaml
source_of_truth:
  component:
    path: frontend/src/components/ExecutionStatusPill.tsx
    responsibility:
      - render normal status text for non-active states
      - render accessible active labels with visual glyph spans
      - expose data-label for the text-clipped shimmer mask

  selector_helper:
    path: frontend/src/utils/executionStatusPillClasses.ts
    responsibility:
      - normalize status strings
      - attach shimmer metadata only for active shimmer states
      - preserve the existing status class contract

  stylesheet:
    path: frontend/src/styles/mission-control.css
    responsibility:
      - define shimmer tokens
      - define the shared moving light field
      - render fill, border, and text masks
      - define reduced-motion, forced-colors, and unsupported text-mask fallbacks

  feature_contract:
    path: specs/301-shared-additive-shimmer/contracts/status-pill-shimmer.md
    responsibility:
      - preserve implementation verification expectations
```

## Host Contract

```yaml
host:
  kind: status-pill
  semantic_states:
    active_shimmer:
      - executing
      - planning
    non_active_shimmer:
      - queued
      - running
      - initializing
      - finalizing
      - awaiting_action
      - awaiting_external
      - waiting
      - waiting_on_dependencies
      - succeeded
      - completed
      - failed
      - canceled
      - cancelled
      - neutral

  metadata_boundary: executionStatusPillProps(status)

  active_metadata:
    className:
      required:
        - status
        - status-running
      one_of:
        - is-executing
        - is-planning
    data-state:
      one_of:
        - executing
        - planning
    data-effect: shimmer-sweep
    data-shimmer-label: normalized_visible_label

  active_selectors:
    - '.status-running[data-effect="shimmer-sweep"]'
    - '.status-running.is-executing'
    - '.status-running.is-planning'

  trigger:
    normalized_status_in:
      - executing
      - planning

  fallback_trigger:
    same_active_metadata: true
    motion_preference_equals: reduce

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

`running`, `initializing`, and `finalizing` continue to use the `status-running` color treatment, but they do **not** receive shimmer metadata unless a future design explicitly opts them into `data-effect="shimmer-sweep"` or an active shimmer class.

## Design Principles

```yaml
principles:
  - active_states_are_explicit_not_inferred_from_color_class_alone
  - motion_must_read_as_activity_not_alert
  - text_legibility_must_remain_primary
  - fill_border_and_text_must_share_one_moving_light_field
  - animation_must_be_attachable_to_existing_status_pill_markup
  - effect_must_not_change_layout_or_pill_dimensions
  - effect_must_use_existing_theme_tokens_before_new_tokens_are_added
  - effect_must_degrade_to_static_or_simpler_treatments_when_user_or_browser_requires_it
```

## Visual Model

The current shimmer is a **shared additive light field** exposed through clipped regions. The fill shimmer, border glint, and text shimmer are not independent animations. They all read from the same gradient token and keyframe path so they remain phase-locked.

```yaml
layers:
  base_status_pill:
    role: preserve the normal status-running active tint
    selector_owner: executionStatusPillProps(status)
    opacity_behavior: constant
    motion: none

  shared_light_field:
    role: moving luminous diagonal band with subtle trailing halo
    css_token: --mm-executing-moving-light-gradient
    keyframes: mm-status-pill-shimmer
    shape: two layered soft-edged linear gradients
    travel: top-left-to-bottom-right visual sweep using inverse background-position endpoints
    angle_deg: -18
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

## Motion Profile

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

## Theme Binding

The shimmer derives from the existing MoonMind theme vocabulary.

```yaml
theme_binding:
  source_tokens:
    - --mm-accent
    - --mm-accent-2
    - --mm-panel
    - --mm-border
    - --mm-ink

  implemented_tokens:
    --mm-executing-moving-light-gradient:
      role: shared halo/core gradient used by fill, border, and text masks
      sources:
        halo: --mm-accent
        core: --mm-accent-2

    --mm-executing-letter-halo:
      role: fallback glyph-pulse text shadow color
      source: --mm-accent-2

    --mm-executing-letter-bright:
      role: fallback glyph-pulse bright text color
      source: color-mix(--mm-accent-2, white)

  derived_roles:
    executing_base_tint:
      from: --mm-accent-2
      intent: calm-active

    shimmer_core:
      from: --mm-accent-2
      intent: coolest-brightest point

    shimmer_halo:
      from: --mm-accent
      intent: atmospheric blend

    text_protection:
      from: inherited status text color plus base visible glyph spans
      intent: keep readable text under the decorative overlay
```

## React Rendering Contract

`ExecutionStatusPill` renders non-active states as a simple status span.

```tsx
<span {...executionStatusPillProps(status)}>{label}</span>
```

For active shimmer states, it renders an accessible parent label plus hidden visual glyph markup.

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
  non_active_statuses_do_not_render_glyph_markup: true
```

## CSS Implementation Contract

```yaml
implementation_shape:
  host_selector:
    any_of:
      - '.status-running[data-effect="shimmer-sweep"]'
      - '.status-running.is-executing'
      - '.status-running.is-planning'

  host_rules:
    position: relative
    overflow: hidden
    isolation: isolate
    background_color: rgb(var(--mm-accent-2) / 0.14)

  rendering_strategy:
    fill_mask: host::before
    border_mask: host::after
    text_mask: .status-letter-wave::after
    fallback_glyphs: .status-letter-wave__glyph only under unsupported text clipping

  shared_animation:
    gradient_token: --mm-executing-moving-light-gradient
    keyframes: mm-status-pill-shimmer
    duration_token: --mm-executing-sweep-cycle-duration
    timing_function: linear

  compositing:
    preferred: mix-blend-mode plus-lighter
    fallback: mix-blend-mode screen

  avoid:
    - javascript_animation_loop
    - requestAnimationFrame
    - setInterval_or_setTimeout_animation
    - font_weight_animation
    - React_state_updates_per_animation_frame
    - page_local_status_pill_wrappers
```

## Isolation Rules

```yaml
isolation:
  host_overflow: hidden
  host_positioning: relative
  host_isolation: isolate
  effect_pointer_events: none
  layout_shift_allowed: false
  text_reflow_allowed: false
  z_index:
    fill_mask: 1
    border_mask: 2
    text_visual_container: 3
  scrollbar_interaction: none
```

## Fallback and Accessibility Behavior

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

## State Matrix

```yaml
state_matrix:
  queued:
    shimmer_sweep: off

  running:
    shimmer_sweep: off
    note: uses status-running color only unless explicitly opted into data-effect in the future

  planning:
    shimmer_sweep: on

  executing:
    shimmer_sweep: on

  initializing:
    shimmer_sweep: off

  finalizing:
    shimmer_sweep: off
    future_variant_allowed: true

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
```

## Semantic Feel

```yaml
tone:
  emotional_read: focused, intelligent, in-progress
  forbidden_reads:
    - error_flash
    - warning_pulse
    - disco_glow
    - scanner_beam
    - loading-skeleton-placeholder

  similarity_targets:
    - codex-thinking-adjacent
    - premium-terminal-ui
    - ambient-sci-fi-control-surface
```

## Implemented Token Block

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
  --mm-executing-letter-halo: rgb(var(--mm-accent-2) / 0.32)
  --mm-executing-letter-bright: color-mix(in srgb, rgb(var(--mm-accent-2)) 68%, white 32%)
  --mm-executing-moving-light-gradient: shared halo/core linear-gradient stack
```

## Acceptance Criteria

```yaml
acceptance_criteria:
  - only normalized planning and executing statuses receive active shimmer metadata today
  - active shimmer hosts match the shared selector contract and remain reusable across list, card, and detail surfaces
  - the active pill remains readable throughout the sweep
  - the shimmer never escapes the rounded visual bounds of the pill
  - the effect produces no measurable layout shift
  - one complete shimmer sweep occurs roughly every 2.6 seconds with no center pause or idle delay
  - fill, border, and text masks use the same moving light-field token and keyframe animation
  - the border glint reads as a ring overlapping the physical border, not as a purely interior hairline
  - glyph-rendered active labels expose a text-clipped shimmer overlay through data-label
  - per-glyph brightening is inactive by default and used only as an unsupported text-clipping fallback
  - the effect looks intentional in both light and dark themes
  - reduced-motion users see a static active treatment with no animated sweep
  - forced-colors users receive readable system text and no decorative shimmer masks
  - non-active statuses never inherit the shimmer accidentally from status-running alone
```

## Verification Expectations

```yaml
verification:
  css_contract_tests:
    assert:
      - shared gradient token exists
      - fill mask uses shared gradient and mm-status-pill-shimmer
      - border mask uses shared gradient, mm-status-pill-shimmer, and ring mask geometry
      - text mask uses shared gradient, mm-status-pill-shimmer, content attr(data-label), and text clipping
      - glyph fallback is disabled by default
      - unsupported text clipping enables glyph fallback
      - reduced-motion disables animation and removes text-mask content
      - forced-colors disables decorative mask content

  selector_boundary_tests:
    assert:
      - executing receives data-effect shimmer-sweep
      - planning receives data-effect shimmer-sweep
      - non-active statuses do not receive data-effect shimmer-sweep
      - status class mapping remains compatible with existing status styles

  render_tests:
    assert:
      - active task-list and task-detail pills preserve text content
      - active pills expose aria-label once
      - active visual glyphs are aria-hidden
      - non-active pills render without glyph-wave markup
```

## Non-Goals

```yaml
non_goals:
  - spinning_indicators
  - pulsing_whole_pill_opacity
  - multi_color_rainbow_motion
  - progress_percentage_visualization
  - execution_time_estimation
  - using status-running alone as a shimmer trigger
  - making glyph pulse the primary text shimmer path
```

## Hand-off Note

The shimmer sweep is implemented as a **shared status-pill modifier**. New surfaces should render `ExecutionStatusPill` or use the same `executionStatusPillProps()` metadata boundary instead of creating page-local shimmer markup. New active states must be added deliberately through the helper, tests, and this document before they receive `data-effect="shimmer-sweep"`.
