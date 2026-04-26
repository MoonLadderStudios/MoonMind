# Contract: AGridUI Overlay API

Source story: `MM-526`.

## Public Runtime Surface

AGridUI exposes a runtime-facing overlay API with these operations:

```text
SetOverlayLayer(channel, markerType, tileIndexes, reason, styleId, priorityOverride, stacking, visible)
ClearOverlayLayer(channel)
```

Contract requirements:

- `channel` must be one of the required Overlay Channel values.
- `tileIndexes` are the canonical overlay input for setting layer geometry.
- `SetOverlayLayer` stores or replaces one channel's desired layer state.
- `ClearOverlayLayer` clears only the requested channel.
- Both operations produce deterministic revision changes observable by tests or diagnostics.
- The operations must be callable from the target runtime's public/Blueprint-facing surface where applicable.

## Rendering Contract

- Active channel layers reduce into the existing marker/decal rendering path.
- The reducer must not require a controller/renderer split in this story.
- Clearing HoverMoveRange must not clear PlanningMoveRange when both map to Movement visuals.
- Existing decal pooling and idempotence behavior remains valid.

## Legacy Compatibility Contract

- Existing marker APIs remain callable.
- Legacy calls route through LegacyCompatibility.
- Warning diagnostics are emitted for non-approved legacy call sites when diagnostics are enabled.
- Compatibility routing must not mutate unrelated overlay channels.

## Required Contract Tests

- API-facing test for SetOverlayLayer retaining all required layer fields.
- API-facing test for ClearOverlayLayer clearing only the requested channel.
- Channel-isolation test with HoverMoveRange and PlanningMoveRange both producing Movement visuals.
- Reducer integration test proving active layers render through the existing marker/decal path.
- Legacy compatibility test proving old marker calls still render and diagnostics emit for non-approved call sites when enabled.
- Decal pooling/idempotence regression test or equivalent target assertion.
