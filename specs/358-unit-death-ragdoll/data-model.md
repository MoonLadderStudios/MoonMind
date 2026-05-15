# Data Model: Unit Death And Ragdoll

## Unit

Represents a tactical actor that can participate in combat.

Fields:
- `unit_id`: stable identity used by combat systems and cleanup.
- `health_state`: current health or defeat source state.
- `life_state`: alive, dying, dead, cleaned_up, or equivalent target-project states.
- `combat_participation`: whether the unit can act, be selected as living, appear in turn order, or be targeted as a living combatant.
- `presentation_state`: pending, started, completed, fallback_started, or unavailable death presentation status.
- `cleanup_state`: active, corpse_persistent, despawned, or released from active systems.

Validation rules:
- A unit may transition from alive to dead only through one valid defeat event.
- A dead unit may not re-enter living combat participation without an explicit revival feature, which is out of scope for this story.
- Presentation state may start only after the first valid death transition.
- Cleanup may not leave the unit registered as an active combat participant.

## Defeat Event

Represents the gameplay cause that defeats a unit.

Fields:
- `source`: damage, overkill damage, scripted removal, environmental hazard, status effect, or equivalent target-project cause.
- `amount_or_reason`: numeric damage amount or named non-damage reason.
- `resolution_step`: combat tick, action, or effect resolution context.
- `is_duplicate`: whether the event arrived after the unit was already dead.

Validation rules:
- Lethal and overkill damage both produce one death transition.
- Duplicate events after death must not trigger additional gameplay-side death effects.
- Multiple same-step events must converge on one death outcome.

## Death Presentation

Represents the player-visible defeat outcome.

Fields:
- `presentation_type`: ragdoll, animation, dissolve, despawn, fallback, or equivalent target-project presentation.
- `can_ragdoll`: whether the unit has the required assets/capability.
- `started_at`: gameplay time or event marker for first presentation start.
- `completion_policy`: whether presentation persists, despawns, or becomes a corpse.

Validation rules:
- The first death transition must request a visible presentation.
- If ragdoll is unavailable, fallback presentation must start without blocking dead-state behavior.
- Duplicate death events must not restart gameplay-side presentation effects unless target visual rules explicitly allow cosmetic replay with no gameplay side effects.

## Combat Flow

Represents systems that consume unit life state.

Fields:
- `turn_order_membership`
- `targeting_membership`
- `movement_or_collision_membership`
- `victory_or_encounter_status`
- `cleanup_references`

Validation rules:
- Dead units are excluded from living-unit action, turn, and target queries.
- Victory and encounter completion checks must observe the dead state.
- Cleanup references must be removed, invalidated, or converted to non-active corpse references consistently.

## State Transitions

```text
Alive
  └── valid defeat event
      └── Dead
          ├── Death presentation started
          ├── Combat participation removed
          └── Cleanup or corpse persistence completed
```

Invalid transitions:
- Dead -> Dead through a second gameplay death transition.
- Dead -> Alive without an explicit revival feature.
- Cleaned up -> active combat participant.
