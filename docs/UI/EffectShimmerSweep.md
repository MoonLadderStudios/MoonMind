# Shimmer Sweep Effect — Declarative Design

## Intent

Define a single reusable motion treatment for the `executing` workflow state.

The effect must communicate **active progress** without implying error, urgency, or instability. It should feel closer to a premium “thinking” shimmer than to a loading skeleton.

## Scope

This document covers **only** the shimmer sweep effect in isolation.

It does not define:
- status color mapping for other workflow states
- border-glint or lens-flare variants
- task row layout changes
- icon changes
- polling or live-update behavior

## Host Contract

```yaml
host:
  kind: status-pill
  semantic_state: executing
  attachment_mode: modifier
  trigger:
    workflow_state_equals: executing
    motion_preference_not: reduce
  fallback_trigger:
    workflow_state_equals: executing
    motion_preference_equals: reduce

hooks:
  preferred:
    data-state: executing
    data-effect: shimmer-sweep
  acceptable:
    class: is-executing

placement:
  inside_fill: true
  inside_border: true
  outside_bounds: false

text:
  source: host-content
  mutation: none
  case: preserve
```

## Design Principles

```yaml
principles:
  - motion_must_read_as_activity_not_alert
  - text_legibility_must_remain_primary
  - animation_must_be_attachable_to_existing_status_pill_markup
  - effect_must_not_change_layout_or_pill_dimensions
  - effect_must_use_existing_theme_tokens_before_new_tokens_are_added
  - effect_must_degrade_to_a_static_highlight_when_motion_is_reduced
```

## Visual Model

The effect is composed of three layers, with an optional fourth foreground text layer on hosts that can safely render the label as glyph spans.

```yaml
layers:
  base:
    role: preserve normal executing pill appearance
    opacity_behavior: constant
    motion: none

  sweep_band:
    role: moving luminous diagonal band
    shape: soft-edged stripe
    travel: top-left-to-bottom-right
    angle_deg: -18
    blend_intent: brighten, not wash out

  trailing_halo:
    role: soft atmospheric bloom behind the sweep band
    shape: wider and dimmer than the core band
    travel: locked_to_sweep_band
    emphasis: subtle

  text_brightening:
    role: foreground glyph emphasis as the sweep crosses the label
    shape: short per-glyph brightness pulse with neighboring halo
    travel: visually_aligned_to_sweep_band
    emphasis: subtle
    required_for_hosts_with_glyph_markup: true
```

## Motion Profile

```yaml
motion:
  cycle:
    duration_ms: 1650
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

  band:
    width_pct: 24
    soft_edge_pct: 38
    core_opacity: 0.34
    halo_opacity: 0.14
    blur_px: 6

  pacing:
    entry: continuous
    midpoint: brightest_at_text_centerline
    exit: continuous

  continuity:
    allow_overlap_between_cycles: false
    idle_gap_present: false
    center_pause_present: false
```

## Theme Binding

The shimmer should derive from the existing MoonMind theme vocabulary instead of inventing a disconnected color.

```yaml
theme_binding:
  source_tokens:
    - --mm-accent
    - --mm-accent-2
    - --mm-panel
    - --mm-border
    - --mm-ink

  derived_roles:
    executing_base_tint:
      from: --mm-accent
      intent: calm-active

    shimmer_core:
      from: --mm-accent-2
      intent: coolest-brightest point

    shimmer_halo:
      from: [--mm-accent, --mm-accent-2]
      intent: atmospheric blend

    text_protection:
      from: --mm-panel
      intent: maintain local contrast beneath moving light
```

## Isolation Rules

```yaml
isolation:
  host_overflow: hidden
  host_positioning: relative
  effect_pointer_events: none
  effect_hit_testing: none
  effect_z_index:
    base: 0
    shimmer: 1
    text: 2
  layout_shift_allowed: false
  text_reflow_allowed: false
  scrollbar_interaction: none
```

## Reduced Motion Behavior

```yaml
reduced_motion:
  animation: disabled
  replacement:
    static_inner_highlight: true
    subtle_border_emphasis: true
    text_emphasis: keep
  preserved_signals:
    - executing_still_reads_as_active
    - no_animation_required_for_comprehension
```

## State Matrix

```yaml
state_matrix:
  idle:
    shimmer_sweep: off

  executing:
    shimmer_sweep: on

  paused:
    shimmer_sweep: off

  waiting_on_dependencies:
    shimmer_sweep: off

  awaiting_external:
    shimmer_sweep: off

  finalizing:
    shimmer_sweep: optional_future_variant

  succeeded:
    shimmer_sweep: off

  failed:
    shimmer_sweep: off

  canceled:
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

## Implementation Shape

This section stays declarative but provides enough structure to guide implementation.

```yaml
implementation_shape:
  host_selector:
    any_of:
      - [data-state="executing"][data-effect="shimmer-sweep"]
      - .is-executing

  rendering_strategy:
    preferred: pseudo-element overlay
    acceptable: nested span overlay
    avoid: extra wrapper that changes layout
    note: oversized CSS background layers may need inverse background-position values so the visible sweep starts at the top-left and exits at the bottom-right

  overlay_elements:
    - ::before as trailing_halo
    - ::after as sweep_band

  text_strategy:
    text_must_render_above_overlay: true
    text_color_shift_during_pass: minimal_only
    glyph_brightening:
      strategy: split_visible_label_into_grapheme_spans
      animation: css_only
      timing: use_shorter_letter_cycle_duration
      edge_padding_chars: 3
      accessibility:
        parent_exposes_complete_label: true
        visual_glyphs_are_hidden_from_assistive_tech: true
      avoid:
        - javascript_animation_loop
        - font_weight_animation
        - rerendering_react_on_frame_interval
```

## Acceptance Criteria

```yaml
acceptance_criteria:
  - the executing pill remains readable at all times during the sweep
  - the shimmer never escapes the rounded bounds of the pill
  - the shimmer produces no measurable layout shift
  - one complete shimmer sweep occurs roughly every 2.2 seconds with no center pause or idle delay
  - the brightest moment occurs near the center of the pill, not at the edges
  - supported glyph-rendered hosts brighten letters in sequence on a shorter cycle than the shimmer sweep
  - the effect looks intentional in both light and dark themes
  - reduced-motion users see a static active treatment with no animated sweep
  - non-executing states never inherit the shimmer accidentally
```

## Suggested Token Block

```yaml
effect_tokens:
  --mm-executing-sweep-cycle-duration: 2200ms
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
  --mm-executing-letter-cycle-duration: 1500ms
```

## Non-Goals

```yaml
non_goals:
  - spinning_indicators
  - pulsing_whole_pill_opacity
  - animated_border_glint
  - multi_color_rainbow_motion
  - progress_percentage_visualization
  - execution_time_estimation
```

## Hand-off Note

If this is implemented in MoonMind, the effect should be added as a **shared status-pill modifier** rather than a page-local animation so that list, card, and detail surfaces all read consistently.
