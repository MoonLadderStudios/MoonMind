# Data Model: Calm Shimmer Motion and Reduced-Motion Fallback

## Executing Shimmer Motion Cycle

Represents the bounded motion profile for the executing status-pill shimmer.

Fields and observable contract:
- `startX`: Off-pill starting position for the visible sweep.
- `endX`: Off-pill ending position for the visible sweep.
- `midpointX`: The center-aligned emphasis point for the brightest moment.
- `durationMs`: Animated sweep duration.
- `repeatGapMs`: Idle gap between sweep cycles.
- `entryProfile`: Soft initial acceleration into the visible pill area.
- `exitProfile`: Smooth fade behavior as the sweep leaves the visible pill area.

Validation rules:
- The visible sweep travels left-to-right across the pill.
- The total cadence remains roughly 1.6 to 1.8 seconds including idle gap.
- Cycles do not overlap.
- The visible effect remains clipped to the pill bounds.

## Reduced-Motion Active Highlight

Represents the static replacement treatment when reduced motion is preferred.

Fields:
- `animationEnabled`: False under reduced-motion conditions.
- `highlightPosition`: Static highlight placement that still reads as active.
- `activeCueStrength`: Visual emphasis level that keeps executing distinguishable.
- `comprehensionWithoutMotion`: Whether the active state remains understandable without animation.

Validation rules:
- Reduced-motion mode disables animated shimmer movement.
- A static active highlight remains visible.
- The active state remains understandable without requiring motion.

## Executing-State Trigger

Represents the semantic workflow-state condition that activates the MM-490 motion behavior.

Fields:
- `state`: Canonical workflow-state value.
- `effect`: Decorative effect identifier.
- `motionMode`: Motion-allowed or reduced-motion path.

Validation rules:
- Only executing pills activate the MM-490 motion profile.
- Non-executing states do not inherit the motion behavior.
- Reduced-motion handling applies only to the executing effect contract.

## Traceability Surface

Represents the runtime-adjacent verification metadata required for MM-490.

Fields:
- `jiraIssue`: Primary Jira issue key.
- `relatedJiraIssues`: Adjacent shimmer stories.
- `designRequirements`: Preserved source requirement IDs.

Validation rules:
- `MM-490` appears in implementation or test-adjacent traceability evidence.
- Source requirement IDs used by the story remain exportable for verification.
- Traceability updates do not remove adjacent shimmer issue links that still matter.

## State Transitions

- `executing + motion allowed -> executing + sweep active`: Shared shimmer motion cycle runs with bounded left-to-right travel.
- `executing + motion allowed -> executing + reduced motion`: Animated sweep stops and static active highlight remains.
- `executing -> non-executing`: MM-490 motion behavior is removed immediately.
- `executing + old traceability -> executing + MM-490 traceability`: Runtime-adjacent evidence is updated to preserve the new Jira issue key without dropping existing related-story references.
