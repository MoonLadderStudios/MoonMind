# Research: Channel-Owned Overlay Intent API

## Input Classification

Decision: `MM-526` is a single-story runtime feature request.
Evidence: The trusted Jira preset brief has one bounded AGridUI outcome: introduce channel-owned overlay state and compatibility routing while preserving the current decal renderer.
Rationale: The brief lists one target surface, one source document, one coherent acceptance set, and explicit scope boundaries.
Alternatives considered: Treating the referenced Grid UI Overlay System document as a broad design was rejected because the Jira issue already selects one phase-1 AGridUI story.
Test implications: Unit and integration tests are both required once the target Tactics frontend source tree is available.

## Target Source Availability

Decision: Implementation is blocked in this checkout because the target Tactics frontend source tree is absent.
Evidence: Repository search found no `Docs/TacticsFrontend/GridUiOverlaySystem.md`, no `AGridUI`, no `EGridOverlayChannel`, no Unreal project files, and no Grid UI marker/decal runtime tests.
Rationale: Creating placeholder AGridUI files in the MoonMind repository would not implement the requested runtime behavior and would violate the source-bound scope.
Alternatives considered: Implementing a mock overlay model inside MoonMind was rejected because the Jira brief targets the Tactics frontend runtime.
Test implications: Target unit/controller and integration commands cannot run in this checkout.

## FR-001 Channel Model

Decision: missing.
Evidence: No target AGridUI channel model exists in this checkout.
Rationale: The required enum/model must live in the target runtime and include exactly the channel names from the Jira brief.
Alternatives considered: Recording the model only in documentation was rejected because selected mode is runtime.
Test implications: Add unit/model coverage in the target project.

## FR-002 Layer State

Decision: missing.
Evidence: No target FGridOverlayLayerState or equivalent exists in this checkout.
Rationale: The required state fields are observable runtime behavior because the reducer and clear semantics depend on them.
Alternatives considered: Inferring state only from rendered decals was rejected because channel-owned desired state must be explicit.
Test implications: Add unit state-retention tests.

## FR-003 Public API

Decision: missing.
Evidence: No BlueprintCallable SetOverlayLayer or ClearOverlayLayer target API exists in this checkout.
Rationale: The Jira brief requires tile indexes as canonical overlay input through AGridUI-facing APIs.
Alternatives considered: Adding only private helper methods was rejected because the public API contract is explicit.
Test implications: Add API-facing or Blueprint-equivalent tests.

## FR-004 Reducer To Existing Renderer

Decision: missing.
Evidence: No target marker/decal renderer path is present in this checkout.
Rationale: The story must preserve the existing renderer and reduce desired channel state into that path.
Alternatives considered: Splitting controller and renderer responsibilities was rejected by the Jira brief.
Test implications: Add reducer unit coverage and renderer integration coverage.

## FR-005 Channel Isolation

Decision: missing.
Evidence: No target Movement overlay channel implementation exists in this checkout.
Rationale: The core bug-prevention behavior is that clearing one channel cannot clear unrelated channel state even when visuals share a marker type.
Alternatives considered: Clearing by marker type was rejected because it recreates the known producer-interference class.
Test implications: Add a two-channel Movement visual regression test.

## FR-006 Legacy Compatibility Diagnostics

Decision: missing.
Evidence: No legacy marker API or diagnostic surface exists in this checkout.
Rationale: Old marker APIs must keep working through LegacyCompatibility during migration, with warning diagnostics for non-approved call sites when enabled.
Alternatives considered: Removing old marker APIs immediately was rejected because compatibility routing is an explicit requirement.
Test implications: Add unit and integration diagnostic tests.

## FR-007 Existing Decal Pooling And Idempotence

Decision: missing.
Evidence: No target decal pooling/idempotence tests exist in this checkout.
Rationale: The feature must preserve current rendering lifecycle guarantees while adding desired-state channels.
Alternatives considered: Relying only on new overlay tests was rejected because existing lifecycle behavior must remain covered.
Test implications: Run existing target tests or add equivalent assertions.

## FR-008 Scope Boundary

Decision: implemented_unverified.
Evidence: `spec.md` and `plan.md` preserve the no-split/no-producer-migration boundary, and no target code has been changed.
Rationale: This remains an implementation guard for the target project.
Alternatives considered: Migrating producers in the same story was rejected because the Jira issue only requires AGridUI API and compatibility routing.
Test implications: Final verification must confirm no out-of-scope producer migration or renderer split occurred.

## FR-009 Traceability

Decision: implemented_verified.
Evidence: `spec.md` preserves `MM-526` and the canonical Jira preset brief.
Rationale: Downstream artifacts can trace requirements and delivery metadata back to Jira.
Alternatives considered: Summarizing the issue without the key was rejected because the user explicitly required preserving `MM-526`.
Test implications: Final verification must preserve the issue key in evidence.
