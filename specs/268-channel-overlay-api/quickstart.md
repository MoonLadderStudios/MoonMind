# Quickstart: Channel-Owned Overlay Intent API

## Scope

This quickstart validates `MM-526` in the target Tactics frontend repository. The current MoonMind checkout does not contain the target AGridUI runtime source or tests.

## Prerequisites

1. Set `TARGET_PROJECT_ROOT` to the root of the Tactics frontend workspace containing `Docs/TacticsFrontend/GridUiOverlaySystem.md`.
2. Confirm `TARGET_PROJECT_ROOT` contains AGridUI runtime source and the existing marker/decal renderer tests.
3. Preserve `MM-526` in implementation notes, verification output, commit text, and pull request metadata.

## Validation Steps

1. Confirm the source design exists:

```bash
test -f "$TARGET_PROJECT_ROOT/Docs/TacticsFrontend/GridUiOverlaySystem.md"
```

2. Confirm target AGridUI/runtime source exists:

```bash
rg -n "AGridUI|SpawnTileMarkers|ClearTileMarkers|SpawnDecalsAtLocations|ClearSpecifiedDecals" "$TARGET_PROJECT_ROOT"
```

3. Run the target unit/controller tests for:

- overlay channel model values
- overlay layer state field retention
- SetOverlayLayer and ClearOverlayLayer API behavior
- HoverMoveRange versus PlanningMoveRange channel isolation
- legacy marker API compatibility diagnostics

4. Run the target integration tests for:

- reducer output into the existing marker/decal rendering path
- existing decal pooling/idempotence behavior
- legacy compatibility behavior across approved and non-approved call sites

5. Run MoonSpec verification against `specs/268-channel-overlay-api/spec.md` and confirm the verdict preserves `MM-526`.

## Current Checkout Result

The target Tactics frontend source tree is absent from this MoonMind checkout, so target unit and integration commands cannot be run here.
