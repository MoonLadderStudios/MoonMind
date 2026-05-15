# Contract: Unit Death And Ragdoll Runtime Behavior

This contract describes the observable gameplay behavior required by THOR-407. It is implementation-agnostic and must be mapped to the target THOR gameplay APIs, events, or automation surfaces when the target workspace is available.

## Death Transition Contract

Given a living unit receives a valid defeat event:
- The unit transitions to a dead state exactly once.
- The transition records or exposes enough state for combat systems to query that the unit is no longer alive.
- Repeated defeat events after the first transition return the existing dead outcome or no-op without replaying gameplay-side death effects.

## Combat Participation Contract

After the death transition:
- The unit is absent from living-unit turn participation.
- The unit is absent from living-unit action selection.
- The unit is absent from living-combatant targeting.
- Victory, encounter completion, movement, collision, and cleanup queries treat the unit according to dead-state rules.

## Presentation Contract

On the first death transition:
- A visible death presentation starts.
- Ragdoll is used when available and appropriate for the unit.
- An equivalent visible fallback starts when ragdoll is unavailable.
- Presentation failure does not keep the unit alive or active in combat.

## Cleanup Contract

When a dead unit is cleaned up, despawned, or converted to a persistent corpse:
- Active combat references are removed or invalidated.
- Later combat flow checks do not dereference cleaned-up active-unit state.
- Persistent corpse state, if used, is not treated as a living combatant.

## Required Validation Coverage

- Lethal damage from a normal attack.
- Overkill damage.
- Multiple same-step damage sources.
- Repeated post-death damage or duplicate notifications.
- Ragdoll-capable unit death.
- Non-ragdoll fallback death.
- Turn order and targeting after death.
- Victory or encounter cleanup after death.
