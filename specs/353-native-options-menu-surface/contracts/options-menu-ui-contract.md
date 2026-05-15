# UI Interaction Contract: Native Options Menu Surface

## Scope

This contract defines the externally visible menu behavior for THOR-402. It is intentionally independent of implementation files so it can be satisfied by the target game runtime while final authored presentation assets are still absent.

## Stable Identifiers

Required navigation action:
- `frontend.nav.options`

Required category identifiers:
- `frontend.options.video`
- `frontend.options.audio`
- `frontend.options.input`

## Home -> Options

Given the player is on Home and the Options navigation action is available:
- Activating `frontend.nav.options` opens an Options panel or screen.
- The opened surface is usable even when final authored Options presentation assets are unavailable.
- The surface renders at least the Video, Audio, and Input category actions.

## Category Resolution

When authored category data is available:
- The Options surface renders category actions generated from that authored data.
- Required baseline category identifiers remain available.

When authored category data is absent, empty, or incomplete:
- The Options surface fills missing required baseline categories from fallback entries.
- The resulting surface is not empty.

## Back / Cancel

Given the Options surface is open:
- Back returns the player to Home.
- Cancel returns the player to Home.
- After return, focus is restored to `frontend.nav.options`.

## Persistence Boundary

This contract does not require settings values to be saved, loaded, applied, or previewed. Category actions may be placeholders or entry points until a separate settings persistence story defines saved settings behavior.

## Acceptance Evidence

The contract is satisfied when automated coverage demonstrates:
- Home -> Options opens the baseline surface.
- Video, Audio, and Input category actions are visible.
- The flow works without authored Options presentation assets or authored category data.
- Back or Cancel returns to Home and restores focus to the Options navigation action.
