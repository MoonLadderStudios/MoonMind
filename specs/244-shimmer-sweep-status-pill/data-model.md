# Data Model: Shared Executing Shimmer for Status Pills

## Status Pill Surface

Represents an existing Mission Control status presentation that communicates workflow state on list, card, and detail views.

Fields and observable contract:
- `stateLabel`: Visible status text shown to the user.
- `semanticClass`: Existing shared status class such as `status-running`, `status-failed`, or `status-completed`.
- `state`: Canonical workflow-state value used to determine whether the pill is executing.
- `effect`: Optional decorative effect contract for the pill.
- `hostContent`: Existing text and optional icon content that must remain unchanged by decorative treatment.

Validation rules:
- Host content remains the semantic source of truth.
- Decorative treatment is additive and does not replace visible status text.
- Existing pill footprint and inline layout remain unchanged.

## Executing Shimmer Modifier

Represents the shared decorative treatment for executing status pills.

Fields:
- `selectorMode`: Preferred selector contract or fallback executing marker.
- `isActive`: Whether the pill is eligible for executing shimmer.
- `motionMode`: Animated or reduced-motion replacement mode.
- `themeTokens`: Existing Mission Control accent, border, panel, and ink token roles used by the effect.

Validation rules:
- Modifier activates only for executing status pills.
- Preferred selector and fallback marker represent the same shared effect contract.
- Non-executing states never inherit the modifier.
- Reduced-motion mode uses no animated sweep while preserving an active-state cue.

## Reduced-Motion Active Treatment

Represents the non-animated executing-state fallback used when motion reduction is preferred.

Fields:
- `activeCue`: Static highlight or border emphasis that still reads as executing.
- `animationEnabled`: False.
- `textPriority`: Host text remains above decorative treatment.

Validation rules:
- Executing state remains visually distinguishable without animation.
- Treatment remains bounded to the pill and does not affect layout.
- Non-executing pills do not inherit reduced-motion executing styling.

## State Transitions

- `non-executing -> executing`: Shared executing shimmer modifier becomes eligible on supported hosts.
- `executing -> non-executing`: Executing shimmer modifier is removed immediately with no residual styling.
- `executing + motion allowed -> executing + reduced motion`: Animated sweep is replaced with a static active treatment.
- `executing on unsupported selector -> executing on supported selector`: Host gains preferred selector or approved fallback marker and becomes shimmer-eligible without changing visible content.
