# Feature Specification: Unit Death And Ragdoll

**Feature Branch**: `[358-unit-death-ragdoll]`
**Created**: 2026-05-15
**Status**: Draft
**Input**: User description: "THOR-407: Docs/TacticsUnits_Core/UnitDeathAndRagdoll.md"

## User Story - Unit Death And Ragdoll

**Summary**: As a tactics gameplay player, I want defeated units to leave active combat cleanly and enter a clear death or ragdoll presentation, so combat outcomes are readable and dead units cannot continue acting.

**Goal**: When a unit is defeated, the game records the unit as dead, removes it from active tactical participation, triggers an appropriate death or ragdoll presentation, and prevents duplicate death handling from repeated damage or late events.

**Independent Test**: Drive a unit from alive to defeated through normal gameplay damage, lethal overkill damage, repeated post-death damage, and cleanup scenarios; verify that the unit reaches one stable dead state, no longer acts or blocks active combat flow, emits the expected death presentation, and can be cleaned up without re-entering gameplay systems.

**Acceptance Scenarios**:

1. **SCN-001**: **Given** a living unit with remaining health, **When** it receives lethal damage, **Then** the unit transitions to a dead state exactly once and is no longer available for turns, actions, targeting as a living combatant, or other active unit behaviors.
2. **SCN-002**: **Given** a unit has entered the dead state, **When** death presentation begins, **Then** the unit displays a visible death or ragdoll outcome that clearly communicates defeat to the player.
3. **SCN-003**: **Given** a dead unit receives additional damage, collision events, delayed effects, or repeated death notifications, **When** those events are processed, **Then** the system keeps the original death outcome stable and does not replay duplicate gameplay-side death effects.
4. **SCN-004**: **Given** combat systems query available units after a death, **When** turn order, targeting, movement, collision, victory checks, or encounter cleanup are evaluated, **Then** the dead unit is excluded or treated according to its dead-state rules.
5. **SCN-005**: **Given** a dead unit is cleaned up, despawned, or moved into a persistent corpse state, **When** the cleanup completes, **Then** no dangling active-unit references remain and combat flow can continue.

### Edge Cases

- Lethal damage greatly exceeds remaining health.
- Multiple damage sources resolve during the same combat tick or action.
- A unit dies while animating, moving, reacting, or resolving an ability.
- A unit is defeated by non-damage causes such as scripted removal, environmental hazards, or status effects.
- Death presentation cannot ragdoll because required presentation assets or physics setup are unavailable.
- Encounter cleanup runs immediately after the final unit death.

## Assumptions

- The Jira issue is the canonical story source for this specify step: `THOR-407`.
- The original Jira preset brief is `THOR-407: Docs/TacticsUnits_Core/UnitDeathAndRagdoll.md`.
- The referenced source design file `Docs/TacticsUnits_Core/UnitDeathAndRagdoll.md` was not present in the current repository, `origin/main`, job artifacts, or available managed retrieval during prior inspection, so this specification is derived from the trusted Jira preset brief and the filename's explicit runtime subject.
- This is runtime implementation work, not documentation-only work.
- Ragdoll is one valid death presentation outcome; if a unit or platform cannot ragdoll, an equivalent visible death presentation must still satisfy the gameplay requirements.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST detect when a living unit becomes defeated by lethal damage or another valid defeat cause.
- **FR-002**: The system MUST transition a defeated unit into exactly one stable dead state.
- **FR-003**: A dead unit MUST be removed from active tactical participation, including turns, selectable living-unit actions, and living-combatant targeting.
- **FR-004**: The system MUST trigger a visible death or ragdoll presentation when a unit first enters the dead state.
- **FR-005**: Death handling MUST be idempotent so repeated damage, delayed effects, collisions, or duplicate notifications do not replay gameplay-side death effects or create multiple dead-state transitions.
- **FR-006**: Combat flow checks, including turn order, target availability, victory or encounter completion, and cleanup, MUST account for dead units consistently.
- **FR-007**: Unit cleanup, despawn, or corpse persistence MUST leave no dangling active-unit references in gameplay systems.
- **FR-008**: If ragdoll presentation is unavailable for a unit, the system MUST fall back to an equivalent visible death presentation without keeping the unit active.
- **FR-009**: Verification artifacts for this work MUST preserve Jira issue key `THOR-407` and the original preset brief `THOR-407: Docs/TacticsUnits_Core/UnitDeathAndRagdoll.md`.

### Key Entities

- **Unit**: A tactical actor that can participate in combat, receive damage, act, be targeted, and transition between alive and dead states.
- **Dead State**: The stable gameplay state for a defeated unit after its first valid death transition.
- **Death Presentation**: The player-visible defeat result, including ragdoll or an equivalent fallback presentation.
- **Combat Flow**: Turn order, targeting, movement, encounter completion, and cleanup behavior that must continue correctly after a unit dies.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In a lethal-damage validation scenario, the defeated unit transitions to dead exactly once.
- **SC-002**: In validation, the dead unit is absent from all living-unit action and turn participation checks after death.
- **SC-003**: At least one validation scenario confirms a visible death or ragdoll presentation starts when death first occurs.
- **SC-004**: At least one repeated-event validation scenario confirms post-death damage or duplicate notifications do not create a second gameplay death transition.
- **SC-005**: At least one combat-flow validation scenario confirms targeting, victory or encounter completion, and cleanup continue correctly after a unit dies.
- **SC-006**: Verification evidence preserves `THOR-407` and the original Jira preset brief in spec and downstream validation outputs.
