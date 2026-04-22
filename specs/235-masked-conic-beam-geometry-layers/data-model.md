# Data Model: Masked Conic Beam Geometry and Layers

## MaskedConicBorderBeam Surface

Represents the reusable wrapper that owns decorative active-state border geometry around arbitrary child content.

Fields and observable contract:
- `active`: Controls whether beam and glow layers render.
- `borderRadius`: Root radius input used to derive the outer rounded rectangle and adjusted inner radius.
- `borderWidth`: Border thickness input used as the ring mask inset.
- `speed`: Orbit duration shared by beam, glow, and trail variants.
- `trail`: Selects no, soft, or defined trailing footprint without changing speed.
- `glow`: Selects whether the optional glow layer renders.
- `children`: Host content that remains outside the decorative mask and animation.

Validation rules:
- Active state renders decorative layers separately from content.
- Inactive state renders no moving beam or glow layer.
- Decorative layers are `aria-hidden`.
- Host content remains readable and semantically unaffected.

## Border Ring Mask

Represents the visible region for the animated beam.

Fields:
- `outerRadius`: Configured border radius.
- `innerInset`: Derived from `borderWidth`.
- `innerRadius`: Derived from `borderRadius - borderWidth`, clamped by CSS behavior to avoid negative optical radius.
- `visibleRegion`: Outer rounded rectangle minus inset inner rounded rectangle.

Validation rules:
- The inner inset equals the configured border width.
- The inner radius is derived from the configured radius and width.
- The mask excludes the content box from the animated beam and glow layers.

## Beam Footprint

Represents the angular conic-gradient distribution for active motion.

Fields:
- `headArc`: Default 12deg bright head.
- `tailArc`: Default 28deg trailing tail.
- `transparentRegion`: The majority of the orbit outside the head and tail.
- `tailColor`: Lower-opacity trailing color.
- `headColor`: Bright glint color.
- `glowColor`: Lower-opacity glow color derived from the same footprint.

Validation rules:
- The main beam uses a mostly transparent conic gradient.
- The beam has soft tail, bright head, and fade back to transparency.
- Glow uses the same footprint family at lower opacity and blur.
- Trail variants modify footprint only, not speed.

## State Transitions

- `inactive -> active`: Root remains stable, beam layer renders, glow renders only when enabled, content remains unchanged.
- `active -> inactive`: Beam and glow layers are removed; content remains unchanged.
- `trail soft -> trail defined/none`: Beam footprint changes; speed remains unchanged.
- `glow low/medium -> glow off`: Glow layer is removed; beam layer remains active when `active` is true.
