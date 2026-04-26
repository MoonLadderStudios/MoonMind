# Data Model: Channel-Owned Overlay Intent API

## Overlay Channel

Represents a named ownership lane for overlay intent.

Required values:

- PlanningMoveRange
- HoverMoveRange
- AttackTargeting
- AbilityPreview
- DangerPreview
- ActiveSelection
- TargetPreview
- GhostPath
- Debug
- LegacyCompatibility

Validation rules:

- A layer belongs to exactly one channel.
- Channel names must be stable and testable because clear semantics operate by channel identity, not only marker visual type.
- LegacyCompatibility is reserved for old marker API routing and must not be used as a generic fallback for new producers.

## Overlay Layer State

Represents desired overlay intent for one channel.

Required fields:

- channel
- marker type
- tile indexes
- reason
- style id
- priority override
- stacking flag
- visibility flag
- revision

Validation rules:

- Tile indexes are the canonical spatial input for SetOverlayLayer.
- Revision changes whenever a channel layer is set or cleared.
- Invisible layers may remain in state but must not contribute visible marker decals.
- Empty tile index sets must have deterministic state and clear behavior.

## Overlay Reducer

Represents AGridUI behavior that converts active layer state into existing marker/decal render commands.

Rules:

- Reduction uses active per-channel state as input.
- Reduction must preserve existing marker/decal renderer ownership boundaries for this story.
- Layers sharing the same marker visual type must remain independently clearable by channel.
- Priority override and stacking flag must influence deterministic output where target renderer semantics support them.

## Legacy Compatibility Route

Represents routing for old marker APIs while producers migrate later.

Rules:

- Old marker API calls continue to render through LegacyCompatibility.
- Non-approved legacy call sites emit warning diagnostics when diagnostics are enabled.
- Approved legacy call sites may render without warning noise.
- Compatibility routing must not hide or mutate unrelated channel state.

## State Transitions

- SetOverlayLayer(channel, layer): creates or replaces the desired state for that channel and advances the channel revision.
- ClearOverlayLayer(channel): clears only the desired state for that channel and advances that channel revision.
- Legacy marker API call: routes through LegacyCompatibility and participates in reducer output.
- Reduce active layers: reads current desired state and produces existing renderer-compatible marker/decal output.
