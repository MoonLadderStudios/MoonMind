# Data Model: Themed Shimmer Band and Halo Layers

## Executing Pill Base

Represents the existing executing-state status-pill presentation that remains visible underneath the MM-489 shimmer treatment.

Fields and observable contract:
- `stateLabel`: Visible status text shown to the user.
- `semanticClass`: Shared status class, typically `status-running` for executing pills.
- `baseTint`: Existing executing-state fill and border treatment inherited from the shared status-pill styling.
- `hostBounds`: Rounded pill footprint that the shimmer treatment must not escape.

Validation rules:
- The base pill appearance remains visible after the shimmer treatment is applied.
- The host bounds do not change when the shimmer treatment is active.
- Visible status text remains the semantic source of truth.

## Shimmer Core Band

Represents the brightest moving layer of the MM-489 treatment.

Fields:
- `angle`: Diagonal sweep direction.
- `opacity`: Brightness of the core band.
- `width`: Relative band width or equivalent reusable variable.
- `position`: Animated or static placement across the pill.
- `themeRole`: Existing theme token role used for the brightest active-progress cue.

Validation rules:
- The band reads as active progress rather than warning or error state.
- The band is visually distinct from the halo layer.
- The band remains inside the host bounds.

## Trailing Halo

Represents the wider, dimmer atmospheric layer that follows or surrounds the core band.

Fields:
- `opacity`: Lower-intensity brightness than the core band.
- `width`: Wider spread than the core band.
- `position`: Placement aligned to the same directional sweep.
- `themeRole`: Existing theme token role or blend used for the atmospheric active-progress cue.

Validation rules:
- The halo is wider and dimmer than the core band.
- The halo supports the premium active-progress read without washing out the text.
- The halo remains bounded to the host pill.

## Effect Token Surface

Represents the reusable variables or named tokens that parameterize the MM-489 treatment.

Fields:
- `durationToken`
- `delayToken`
- `angleToken`
- `coreOpacityToken`
- `haloOpacityToken`
- `bandWidthTokenOrEquivalent`
- `positionTokensOrEquivalent`

Validation rules:
- Shared tokens or equivalent variables exist for the tunable values the story owns.
- Token usage stays bound to the shared Mission Control theme vocabulary.
- Hard-coded values are acceptable only where a reused equivalent variable already provides the same control point.

## State Transitions

- `executing base only -> executing with shimmer`: MM-489 layered treatment activates without replacing the base appearance.
- `executing -> non-executing`: The shimmer layers are removed and no residual executing treatment remains.
- `shared token update -> shimmer restyle`: The layered treatment adjusts through shared reusable variables without page-local forks.
