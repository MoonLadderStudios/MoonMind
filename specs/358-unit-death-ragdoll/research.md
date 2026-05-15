# Phase 0 Research: Unit Death And Ragdoll

## Workspace And Source Availability

Decision: The current MoonMind checkout does not contain the THOR gameplay source needed to implement or verify THOR-407.
Evidence: Repository searches found no `Docs/TacticsUnits_Core/UnitDeathAndRagdoll.md`, no `.uproject`, and no matching `UnitDeath`, `ragdoll`, unit combat, health, targeting, or turn-order implementation files outside unrelated MoonMind text.
Rationale: Planning must be honest about the implementation boundary. The Jira brief and generated spec are traceable, but runtime implementation requires the target THOR workspace.
Alternatives considered: Treating MoonMind as the implementation target was rejected because no relevant gameplay surface exists here.
Test implications: Unit and integration test commands must be executed in the target THOR workspace once available; this checkout can only validate Moon Spec artifacts.

## FR-001 Defeat Detection

Decision: Status `missing`; add code and tests.
Evidence: No target unit health, damage, or defeat-code surface found in this checkout.
Rationale: The story requires a living unit to become defeated through lethal damage or equivalent defeat causes.
Alternatives considered: Relying on presentation-only death handling was rejected because gameplay state must change first.
Test implications: Unit tests for lethal, overkill, and non-damage defeat causes; integration test for normal gameplay damage.

## FR-002 Stable Dead State

Decision: Status `missing`; add code and tests.
Evidence: No unit dead-state model or transition guard found.
Rationale: Exactly-one transition is the core safety property for later combat systems.
Alternatives considered: Inferring dead state from health alone was rejected because duplicate events and cleanup need an explicit stable state.
Test implications: Unit tests must prove one transition across repeated death triggers; integration tests must observe stable state after combat resolution.

## FR-003 Active Participation Removal

Decision: Status `missing`; add code and tests.
Evidence: No target turn/action/targeting filters found.
Rationale: A dead unit cannot remain selectable, actionable, or targetable as a living combatant.
Alternatives considered: Hiding dead units visually without gameplay filtering was rejected because combat flow would remain incorrect.
Test implications: Unit tests for participation predicates; integration tests for turn order and targeting after death.

## FR-004 Death Or Ragdoll Presentation

Decision: Status `missing`; add code and tests.
Evidence: No target death animation, ragdoll, or presentation trigger found.
Rationale: Defeat must be readable to the player and presentation must start only on the first death transition.
Alternatives considered: Gameplay-only death with no presentation was rejected because the user story requires a clear visual outcome.
Test implications: Unit tests for presentation trigger decisions; integration tests or automation evidence for presentation start.

## FR-005 Idempotent Death Handling

Decision: Status `missing`; add code and tests.
Evidence: No duplicate death notification, post-death damage, or collision guard found.
Rationale: Repeated events are common in gameplay resolution and must not replay gameplay-side death effects.
Alternatives considered: Letting downstream systems deduplicate independently was rejected because each system could drift and produce inconsistent outcomes.
Test implications: Unit tests for repeated damage, delayed effects, collisions, and duplicate notifications; integration test for same-tick multi-source damage.

## FR-006 Combat Flow Integration

Decision: Status `missing`; add code and tests.
Evidence: No target combat flow code for turn order, targeting, victory, encounter completion, or cleanup found.
Rationale: Death must update every combat query that controls continued play.
Alternatives considered: Updating only the unit object was rejected because stale combat indexes can still keep a dead unit active.
Test implications: Integration tests for turn order, target availability, victory or encounter completion, and cleanup after death.

## FR-007 Cleanup Reference Safety

Decision: Status `missing`; add code and tests.
Evidence: No cleanup, despawn, corpse persistence, or dangling-reference safety code found.
Rationale: Unit cleanup must not leave active gameplay references that can crash or alter later combat.
Alternatives considered: Immediate destruction without reference validation was rejected because combat systems may retain unit references.
Test implications: Unit tests for cleanup registration/unregistration; integration test for encounter cleanup after final death.

## FR-008 Ragdoll Fallback

Decision: Status `missing`; add code and tests.
Evidence: No ragdoll capability check or fallback path found.
Rationale: The story accepts ragdoll as one presentation path but requires an equivalent visible death presentation when ragdoll is unavailable.
Alternatives considered: Failing death presentation when ragdoll is unavailable was rejected because gameplay death must still complete.
Test implications: Unit tests for ragdoll-capable and non-ragdoll units; integration test for fallback presentation.

## FR-009 And SC-006 Traceability

Decision: Status `implemented_unverified`; preserve through later artifacts.
Evidence: `specs/358-unit-death-ragdoll/spec.md`, `plan.md`, `quickstart.md`, and this research artifact preserve `THOR-407` and `THOR-407: Docs/TacticsUnits_Core/UnitDeathAndRagdoll.md`.
Rationale: Traceability exists in planning artifacts but must be carried through tasks, verification, commit text, and PR metadata later.
Alternatives considered: Treating traceability as complete now was rejected because final verification has not run.
Test implications: Final verification must confirm traceability in all downstream outputs.

## Test Strategy

Decision: Use separate unit and integration layers in the target THOR workspace.
Evidence: The specification includes state-transition, predicate, presentation, combat-flow, and cleanup requirements that naturally split between isolated behavior and runtime flow validation.
Rationale: Unit tests are the fastest way to prove idempotency and predicates; integration tests are needed to prove combat systems observe the dead state in an encounter-like flow.
Alternatives considered: Integration-only testing was rejected because duplicate-event and fallback cases need focused red-first coverage; unit-only testing was rejected because combat flow and presentation are cross-system behavior.
Test implications: Unit tests must be written and observed failing before code changes; integration tests must prove lethal damage, presentation, participation removal, duplicate-event safety, combat flow, and cleanup.
