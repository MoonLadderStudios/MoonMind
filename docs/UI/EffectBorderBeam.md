# Masked Conic Border Beam — Declarative Design

## Goal
Create a **standalone status effect** for an `executing` state: a subtle, premium-looking beam that appears to travel **only along the border** of an element. The beam should feel alive and directional without turning the whole surface into a spinner.

This spec defines **only the border-beam effect in isolation**.

---

## Design intent
- Convey **active execution**.
- Read as **intentional motion on the perimeter**, not as a full-card shimmer.
- Feel **precise, modern, and slightly magical**.
- Stay legible around content and work on both dark and light themes.
- Avoid looking like a loading spinner or an error pulse.

---

## Effect summary
The effect is a **rotating conic highlight** that is:
- clipped to the **border ring only**,
- visible through a **mask**,
- softened with a slight glow,
- optionally paired with a faint secondary trailing beam.

Visually, the user perceives:
1. a dim resting border,
2. a brighter traveling glint,
3. a soft bloom that spills just outside the edge,
4. a continuous orbit with no visible seam.

---

## Component contract

### Name
`MaskedConicBorderBeam`

### Inputs
- `active: boolean`
- `borderRadius: token | px`
- `borderWidth: token | px`
- `speed: slow | medium | fast | seconds`
- `intensity: subtle | normal | vivid`
- `theme: neutral | brand | success | custom`
- `direction: clockwise | counterclockwise`
- `trail: none | soft | defined`
- `glow: off | low | medium`
- `reducedMotion: auto | off | minimal`

### Output
A border-only animated visual treatment suitable for wrapping any rectangular UI surface.

---

## Visual model

### Base layers
1. **Host surface**
   - The actual content container.
   - No animation required.

2. **Resting border ring**
   - A low-contrast stroke defining the perimeter.
   - Always visible while `active = true`.

3. **Animated conic beam**
   - A conic gradient rotated around the center.
   - Most of the gradient is transparent.
   - Only a narrow bright wedge is visible.

4. **Outer glow layer**
   - Optional soft bloom derived from the same beam.
   - Slight blur and lower opacity.
   - Sits outside or straddles the border edge.

5. **Optional trailing beam**
   - A second broader, dimmer wedge behind the main glint.
   - Adds motion richness without increasing speed.

---

## Geometry and masking

### Ring shape
The animated beam must be visible **only in the border ring**.

Use a mask that subtracts the inner rectangle from the outer rectangle:
- **Outer shape** = full rounded rect
- **Inner shape** = inset rounded rect
- **Visible region** = outer minus inner

### Inset rule
- Inner inset equals `borderWidth`.
- Inner radius should be adjusted so corners remain optically consistent.

### Beam arc size
Recommended visible arc for the bright head:
- **8° to 18°** for a sharp premium glint
- **20° to 36°** for a softer beam

Default:
- bright head: **12°**
- trailing tail: **28°**

### Mask constraint
The beam must never fill the interior content area.
The effect should still read correctly on a transparent or blurred card.

---

## Motion behavior

### Primary motion
- The conic beam rotates around the component center.
- Motion is linear and continuous.
- Preferred default direction: **clockwise**.

### Recommended timing
- `slow` = 4.8s per orbit
- `medium` = 3.6s per orbit
- `fast` = 2.8s per orbit

Default:
- **3.6s linear infinite**

### Acceleration
Avoid easing for the orbit itself.
A constant sweep reads as a circulating energy path rather than an object speeding up and slowing down.

### Entry / exit
When activated:
- fade beam opacity from 0 to target over **160–240ms**
- optionally ramp glow slightly later by **40–80ms**

When deactivated:
- fade beam and glow out over **120–180ms**

### Optional secondary modulation
A very slight opacity breathing can make the effect feel less mechanical:
- amplitude: ±8% opacity
- duration: 1.8–2.4s
- keep subtle enough that the beam still feels continuous

---

## Color behavior

### Resting border
- neutral and low contrast
- should establish the perimeter even when the beam is on the far side

### Beam head
- highest luminance point
- should read like a polished glint rather than a solid neon band

### Tail
- broader and dimmer than the head
- should fade smoothly into transparency

### Suggested token roles
- `--beam-border-base`
- `--beam-head-color`
- `--beam-tail-color`
- `--beam-glow-color`
- `--beam-opacity`
- `--beam-glow-opacity`

### Theme guidance
- **neutral**: white or cool silver on dark; charcoal/silver on light
- **brand**: use a single brand hue with white-hot center
- **success**: cool green/cyan hybrid works well for “executing” without implying completion

---

## Recommended default tuning

```yaml
MaskedConicBorderBeam:
  active: true
  borderRadius: 16px
  borderWidth: 1.5px
  speed: 3.6s
  direction: clockwise
  intensity: normal
  trail: soft
  glow: low
  reducedMotion: auto
  colors:
    borderBase: "color-mix(in oklab, currentColor 18%, transparent)"
    head: "rgba(255,255,255,0.95)"
    tail: "rgba(255,255,255,0.28)"
    glow: "rgba(255,255,255,0.22)"
  beam:
    headArc: 12deg
    tailArc: 28deg
    transparentGap: 320deg
  transitions:
    enter: 200ms ease-out
    exit: 140ms ease-in
```

---

## Declarative rendering rules

### Rule 1 — rest state
If `active = false`:
- show no moving beam
- allow the host border to remain static if desired

### Rule 2 — active state
If `active = true`:
- render the resting border ring
- render one conic-gradient beam masked to the ring
- rotate the beam continuously around center
- optionally render one softened glow companion layer

### Rule 3 — border-only visibility
At all times:
- mask out the full interior of the component
- preserve content readability
- avoid overlaying text with the animated beam

### Rule 4 — subtlety threshold
The effect is decorative status communication, not the primary focus target.
It must not outshine primary buttons, selected states, or error states.

### Rule 5 — optical continuity
At corners:
- motion must appear smooth
- border radius must be honored
- beam thickness must remain visually consistent around the full perimeter

---

## Pseudostructure

```md
Component: MaskedConicBorderBeam

Host
  Content Surface
  Border Ring (static)
  Beam Layer (animated conic gradient)
    Mask: outer rounded rect - inner rounded rect
  Glow Layer (optional, blurred)
    Mask: same ring or slightly expanded ring
```

---

## Gradient composition

### Main beam
Construct a conic gradient with:
- large transparent region
- soft lead-in
- bright narrow head
- soft trailing fade
- return to transparency

Example conceptual stop sequence:
- 0°–300° transparent
- 300°–320° soft tail rise
- 320°–332° bright head
- 332°–360° fade out

This sequence then rotates as a whole.

### Glow beam
Same angular footprint, but:
- lower opacity
- slightly wider head/tail
- blurred
- optionally expanded 1–2px beyond the border bounds

---

## Motion variants

### Variant A — precision glint
- narrow bright head
- minimal tail
- subtle glow
- best for premium, technical UI

### Variant B — energized beam
- wider tail
- more obvious outer bloom
- slightly faster orbit
- best when execution state should be more noticeable

### Variant C — dual-phase orbit
- one bright head
- one very faint offset companion 120°–180° behind
- richer but still elegant

Preferred default for MoonMind-style execution state:
- **Variant A with a soft trail**

---

## Reduced motion behavior

If `reducedMotion = auto` and the user prefers reduced motion:
- stop orbital rotation
- keep a static illuminated segment on one edge or corner
- optionally pulse opacity very gently every 2.4–3.2s

If `reducedMotion = minimal`:
- no movement
- slightly brighter static border ring only

Do not replace the beam with rapid pulsing.

---

## Accessibility and UX constraints
- The effect must never be the only indicator of execution state.
- Pair with a text label such as `Executing`.
- Avoid strong red/orange pulses that may imply warning.
- Keep average luminance low enough to prevent distraction in dense lists.
- The animation should remain distinguishable at small sizes without becoming noisy.

---

## Performance guidance
- Prefer a single rotating pseudo-element or composited layer.
- Animate **transform/rotation** or an equivalent angle variable when possible.
- Avoid layout-triggering animation.
- Blur/glow should remain modest to prevent excessive paint cost.
- The effect should degrade gracefully on lower-power devices by disabling the glow first.

---

## Acceptance criteria
- The center/content area remains fully unaffected by the animated beam.
- The border visibly communicates ongoing activity.
- The beam travels smoothly around all four sides and rounded corners.
- The beam reads as a glint/energy sweep, not a spinner.
- The effect works on dark and light surfaces.
- Reduced-motion mode yields a static but still meaningful active state.

---

## Non-goals
- No full-card shimmer
- No filling background gradient
- No spinner icon replacement
- No completion pulse or success burst
- No content-area masking beyond the border ring

---

## Implementation note
The cleanest mental model is:
> “Render a conic-gradient highlight over the full box, then mask it down to just the border ring.”

That model preserves portability across CSS, canvas, SVG, and motion systems while keeping the effect definition declarative.
