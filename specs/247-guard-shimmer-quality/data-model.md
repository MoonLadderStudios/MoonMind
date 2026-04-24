# Data Model: Shimmer Quality Regression Guardrails

## Executing Shimmer Quality Guardrail

Represents the observable verification contract for the shared executing shimmer treatment.

Fields and observable contract:
- `readabilityAtSamplePoints`: Whether the executing label remains readable at sampled moments during the sweep.
- `boundedToRoundedPill`: Whether the visible shimmer remains clipped to the pill bounds.
- `scrollbarInteraction`: Whether the shimmer avoids scrollbar interaction.
- `layoutStable`: Whether pill dimensions and nearby layout remain unchanged during activation and animation.
- `themeIntentPreserved`: Whether the active treatment remains intentional in light and dark themes.

Validation rules:
- The executing label remains readable throughout the guarded sweep samples.
- The shimmer stays inside the rounded pill bounds.
- The effect does not interact with scrollbars.
- Activating or animating the shimmer does not change layout.
- The active treatment remains intentional in supported themes.

## State Matrix Coverage Set

Represents the workflow states this story must verify for shimmer activation behavior.

Fields:
- `executingState`: The only state allowed to activate the shimmer.
- `nonExecutingStates`: The listed states where the shimmer must remain off.
- `futureVariantStates`: States such as `finalizing` that stay out of scope until a future story expands the contract.

Validation rules:
- Only the `executing` state activates the shimmer treatment.
- Every listed non-executing state remains free of shimmer activation.
- Future-variant states remain off unless a later story explicitly expands coverage.

## Reduced-Motion Active Fallback

Represents the non-animated executing treatment under reduced-motion conditions.

Fields:
- `animationEnabled`: False when reduced motion is preferred.
- `staticHighlightPresent`: Whether a visible active fallback remains.
- `activeStateComprehension`: Whether executing still reads as active without animation.

Validation rules:
- Reduced-motion conditions disable the shimmer animation.
- A static active fallback remains visible.
- The executing state remains understandable without motion.

## Runtime Traceability Surface

Represents runtime-adjacent metadata used by tests and final verification.

Fields:
- `primaryJiraIssue`: The primary Jira issue key for the shared shimmer contract.
- `relatedJiraIssues`: Adjacent shimmer stories preserved alongside the primary issue.
- `designRequirementIds`: Source design requirements carried into verification.

Validation rules:
- MM-491 appears in runtime-adjacent evidence for this story.
- Adding MM-491 does not silently drop still-relevant adjacent shimmer issue references.
- Design requirement IDs remain available for final verification.

## State Transitions

- `non-executing -> executing`: shared shimmer activates and must satisfy readability, bounds, layout, and theme guardrails.
- `executing -> reduced motion`: animation turns off and the static active fallback remains.
- `executing -> non-executing`: shimmer is removed immediately.
- `existing traceability -> MM-491 traceability`: runtime-adjacent evidence is extended to preserve MM-491 alongside adjacent shimmer stories.
