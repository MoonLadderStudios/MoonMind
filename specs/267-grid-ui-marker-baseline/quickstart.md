# Quickstart: Grid UI Marker Baseline

## Prerequisite

This MoonSpec story requires the target Tactics frontend source tree containing `Docs/TacticsFrontend/GridUiOverlaySystem.md`, Grid UI marker/decal runtime code, and marker lifecycle tests. Those files are not present in this checkout.

## Target Validation Flow

1. Confirm the target source document exists:

   ```bash
   test -f Docs/TacticsFrontend/GridUiOverlaySystem.md
   ```

2. Locate every direct mutation API use:

   ```bash
   rg -w -n "SpawnTileMarkers|SpawnTileMarkersFromIndexes|QueueSpawnTileMarkers|QueueSpawnTileMarkersFromIndexes|ClearTileMarkers|ClearAllTileMarkers|SpawnDecalsAtLocations|ClearSpecifiedDecals" .
   ```

3. Create or update the checked-in inventory with source path, source location, invoked API, producer role, operation category, and notes.

4. Add red-first automated coverage for the Movement overlay interference bug class.

5. Add or update lifecycle/idempotence tests preserving existing Grid UI marker behavior.

6. Add or update diagnostic validation for source, marker type, reason, owner controller, tile count, and operation type.

7. Run the target project's unit test suite.

8. Run the target project's integration or controller-level marker lifecycle suite.

9. Run MoonSpec verification and confirm `MM-525` and the canonical Jira preset brief are preserved in all delivery metadata.

## Current Checkout Result

The current repository cannot execute the target validation flow because the Tactics frontend source tree is unavailable.
