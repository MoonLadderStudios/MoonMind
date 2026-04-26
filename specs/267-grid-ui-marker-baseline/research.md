# Research: Grid UI Marker Baseline

## Classification

Decision: `MM-525` is a single-story runtime feature request.
Evidence: The Jira preset brief names one outcome: establish an observable baseline for existing Grid UI marker lifecycle before ownership semantics change.
Rationale: The story has one actor, one target subsystem, and one independently testable output set: inventory, baseline tests, and diagnostic evidence.
Alternatives considered: Treating the source document as a broad design was rejected because the Jira brief already selected a phase-0 baseline slice.
Test implications: Unit and integration tests are required in the target Tactics frontend project.

## Source Availability

Decision: Implementation is blocked in this checkout because the target source tree is absent.
Evidence: Repository searches found no `Docs/TacticsFrontend/GridUiOverlaySystem.md`, no `AGridUI`, no `SpawnTileMarkers`, no `QueueSpawnTileMarkers`, no `ClearTileMarkers`, no `SpawnDecalsAtLocations`, and no `ClearSpecifiedDecals`.
Rationale: Inventing target files or APIs in the MoonMind repository would violate the preserved Jira request and produce non-executable evidence.
Alternatives considered: Creating documentation-only inventory placeholders was rejected because runtime mode is selected and the Jira acceptance criteria require checked-in inventory and automated tests against actual marker lifecycle behavior.
Test implications: No target unit or integration test can be written in this checkout.

## FR-001 Direct Mutation Inventory

Decision: Status is `missing`.
Evidence: No direct mutation APIs named by the brief exist in the current repository.
Rationale: Inventory must be derived from actual target call sites, not prompt text.
Alternatives considered: Using the Jira brief as the inventory was rejected because it names APIs but not source locations.
Test implications: Requires an inventory validation check in the target project.

## FR-002 Producer Role Classification

Decision: Status is `missing`.
Evidence: No target call sites exist to classify.
Rationale: Producer roles require surrounding gameplay and lifecycle context.
Alternatives considered: Pre-populating every role from the brief was rejected because roles must map to concrete call sites.
Test implications: Requires inventory validation or review-backed automated consistency check.

## FR-003 Movement Overlay Interference Regression

Decision: Status is `missing`.
Evidence: No Movement overlay producer code or test harness exists in this checkout.
Rationale: The regression must exercise current behavior where one Movement producer can erase another Movement producer's overlay.
Alternatives considered: Adding a synthetic MoonMind unit test was rejected because it would not validate the target Grid UI runtime.
Test implications: Requires red-first target unit/controller coverage.

## FR-004 Lifecycle And Idempotence Preservation

Decision: Status is `missing`.
Evidence: No existing Grid UI marker lifecycle/idempotence tests are present.
Rationale: Equivalent assertions must be anchored in the target test suite.
Alternatives considered: Marking this verified from absence of changes was rejected because the requirement asks for passing lifecycle/idempotence evidence.
Test implications: Requires target unit and/or integration suite execution.

## FR-005 Diagnostic Evidence

Decision: Status is `missing`.
Evidence: No marker/decal diagnostic event surface exists in this checkout.
Rationale: The diagnostic fields must be emitted or observable by the target runtime.
Alternatives considered: Documenting expected fields only was rejected because runtime mode requires validation.
Test implications: Requires unit and integration validation of diagnostic event shape.

## FR-006 Ownership Semantics Guard

Decision: Status is `implemented_unverified`.
Evidence: No target runtime code has been changed in this checkout.
Rationale: The story constrains future target implementation, but final proof requires inspecting target changes when they exist.
Alternatives considered: Marking verified was rejected because the target source tree is absent.
Test implications: Final verification must confirm no ownership semantics migration was included.

## FR-007 Jira Traceability

Decision: Status is `implemented_verified` for MoonSpec artifacts created so far.
Evidence: `specs/267-grid-ui-marker-baseline/spec.md` preserves `MM-525` and the canonical Jira preset brief.
Rationale: Traceability can be satisfied in this repository's MoonSpec artifacts even though runtime implementation is blocked.
Alternatives considered: Deferring traceability until implementation was rejected because MoonSpec artifacts are already available.
Test implications: Final verification should confirm `MM-525` is preserved in all delivery metadata.
