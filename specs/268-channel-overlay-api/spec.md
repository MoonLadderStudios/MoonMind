# Feature Specification: Channel-Owned Overlay Intent API

**Feature Branch**: `268-channel-overlay-api`
**Created**: 2026-04-26
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-526 as the canonical Moon Spec orchestration input.

Jira issue: MM-526 from MM project

Summary:
Add channel-owned overlay intent API inside AGridUI with legacy compatibility routing

Source Reference:
Source Document: Docs/TacticsFrontend/GridUiOverlaySystem.md
Source Title: Grid UI Overlay System
Source Sections:
- 7.1 AGridUI
- 8. Data Model
- 9. Public API Contract
- 10. Channel Semantics
- 24. Compatibility With Existing Concepts

Coverage IDs:
- plan.phase1
- doc.api.preferred
- doc.compatibility
- doc.channel-isolation

Brief:
Introduce the desired-state channel model inside the existing AGridUI surface while preserving the current decal renderer. Add overlay channel and layer state types, SetOverlayLayer and ClearOverlayLayer APIs, transient channel state, a simple marker-type union reducer, and compatibility routing for old marker APIs.

Acceptance Criteria:
- EGridOverlayChannel includes PlanningMoveRange, HoverMoveRange, AttackTargeting, AbilityPreview, DangerPreview, ActiveSelection, TargetPreview, GhostPath, Debug, and LegacyCompatibility.
- FGridOverlayLayerState stores channel, marker type, tile indexes, reason, style id, priority override, stacking flag, visibility flag, and revision.
- AGridUI exposes BlueprintCallable SetOverlayLayer and ClearOverlayLayer APIs using tile indexes as canonical overlay input.
- AGridUI stores per-channel overlay state and reduces active channel layers into existing marker rendering without splitting controller/renderer responsibilities yet.
- ClearOverlayLayer(HoverMoveRange) does not clear PlanningMoveRange when both resolve to Movement visuals.
- Legacy marker APIs still work through LegacyCompatibility and emit warning diagnostics when enabled for non-approved call sites.
- Existing decal pooling/idempotence tests still pass.

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-526 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata."

## User Story - Channel-Owned AGridUI Overlay Layers

**Summary**: As a tactics frontend maintainer, I want AGridUI to accept overlay intent by explicit channel so multiple gameplay producers can own, update, and clear their overlays without erasing unrelated producer state.

**Goal**: Add a channel-owned desired-state overlay API inside AGridUI that preserves current decal rendering behavior, isolates channel cleanup, and routes legacy marker calls through a compatibility channel until later migration stories move producers to the new API.

**Independent Test**: Can be fully tested by exercising AGridUI with multiple overlay channels, verifying SetOverlayLayer and ClearOverlayLayer update only the requested channel, confirming the reducer still renders expected marker visuals through the existing decal path, validating legacy API routing and diagnostics, and rerunning the existing decal pooling/idempotence tests.

**Acceptance Scenarios**:

1. **Given** AGridUI receives overlay layer intent for each supported gameplay channel, **When** the layer is stored, **Then** the channel identity, marker type, tile indexes, reason, style id, priority override, stacking flag, visibility flag, and revision are retained as layer state.
2. **Given** multiple channels produce Movement visuals, **When** ClearOverlayLayer is called for HoverMoveRange, **Then** PlanningMoveRange remains active and continues to contribute Movement visuals.
3. **Given** active overlay layers exist across channels, **When** AGridUI reduces layers for rendering, **Then** the existing marker/decal renderer receives the correct union of active layers without splitting controller and renderer responsibilities in this story.
4. **Given** a caller uses the old marker API, **When** compatibility routing is enabled, **Then** the call still renders through LegacyCompatibility and emits warning diagnostics for non-approved call sites when diagnostics are enabled.
5. **Given** existing decal pooling and idempotence expectations, **When** the target test suite runs after the new channel APIs are added, **Then** those expectations still pass or equivalent assertions preserve the same observable guarantees.

### Edge Cases

- A layer has an empty tile index set and still needs a deterministic revision and clear behavior.
- A channel sets an invisible layer that should remain in desired state without contributing visible marker decals.
- Multiple active layers share marker visuals but differ by priority override or stacking flag.
- A legacy marker call arrives from an approved call site and should preserve behavior without warning noise.
- A legacy marker call arrives from a non-approved call site while diagnostics are disabled and must not disrupt rendering.

## Assumptions

- The referenced source document `Docs/TacticsFrontend/GridUiOverlaySystem.md` is not present in this checkout; the trusted MM-526 Jira brief is the canonical source for this story.
- Runtime mode applies: the implementation target is the Tactics frontend Grid UI runtime, not MoonMind documentation.
- If the target Tactics frontend source tree is unavailable in this repository, implementation must stop at the first stage that proves the required source or tests cannot be located.
- This story introduces channel-owned overlay state inside AGridUI while preserving the existing decal renderer boundary; producer migrations beyond legacy compatibility routing are out of scope.

## Source Design Requirements

| ID | Source | Requirement | Scope | Mapped Requirement |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-001 | MM-526 Source Section 7.1; Coverage ID `plan.phase1` | Introduce desired-state overlay channel support inside the existing AGridUI surface. | In scope | FR-001, FR-003 |
| DESIGN-REQ-002 | MM-526 Acceptance Criteria; Source Section 8 | Define EGridOverlayChannel with PlanningMoveRange, HoverMoveRange, AttackTargeting, AbilityPreview, DangerPreview, ActiveSelection, TargetPreview, GhostPath, Debug, and LegacyCompatibility. | In scope | FR-001 |
| DESIGN-REQ-003 | MM-526 Acceptance Criteria; Source Section 8 | Define FGridOverlayLayerState with channel, marker type, tile indexes, reason, style id, priority override, stacking flag, visibility flag, and revision. | In scope | FR-002 |
| DESIGN-REQ-004 | MM-526 Acceptance Criteria; Source Section 9 | Expose BlueprintCallable SetOverlayLayer and ClearOverlayLayer APIs using tile indexes as canonical overlay input. | In scope | FR-003 |
| DESIGN-REQ-005 | MM-526 Acceptance Criteria; Source Section 10 | Store overlay state per channel and reduce active layers into the existing marker rendering path without splitting controller and renderer responsibilities. | In scope | FR-004 |
| DESIGN-REQ-006 | MM-526 Acceptance Criteria; Coverage ID `doc.channel-isolation` | Clearing HoverMoveRange must not clear PlanningMoveRange when both resolve to Movement visuals. | In scope | FR-005 |
| DESIGN-REQ-007 | MM-526 Acceptance Criteria; Source Section 24; Coverage ID `doc.compatibility` | Preserve legacy marker APIs through LegacyCompatibility and emit warning diagnostics for non-approved call sites when enabled. | In scope | FR-006 |
| DESIGN-REQ-008 | MM-526 Acceptance Criteria | Existing decal pooling and idempotence tests must still pass or retain equivalent assertions. | In scope | FR-007 |
| DESIGN-REQ-009 | MM-526 Scope boundary | Do not migrate individual gameplay producers or split controller/renderer responsibilities in this story. | In scope | FR-008 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: AGridUI MUST define an overlay channel model containing PlanningMoveRange, HoverMoveRange, AttackTargeting, AbilityPreview, DangerPreview, ActiveSelection, TargetPreview, GhostPath, Debug, and LegacyCompatibility.
- **FR-002**: AGridUI MUST store overlay layer state with channel, marker type, tile indexes, reason, style id, priority override, stacking flag, visibility flag, and revision.
- **FR-003**: AGridUI MUST expose BlueprintCallable SetOverlayLayer and ClearOverlayLayer operations that use tile indexes as the canonical overlay input.
- **FR-004**: AGridUI MUST store desired overlay state per channel and reduce active channel layers into the existing marker/decal rendering path.
- **FR-005**: ClearOverlayLayer for one channel MUST NOT clear or mutate unrelated channel state, including the case where HoverMoveRange and PlanningMoveRange both resolve to Movement visuals.
- **FR-006**: Legacy marker APIs MUST continue to render through LegacyCompatibility and MUST emit warning diagnostics for non-approved call sites when diagnostics are enabled.
- **FR-007**: Existing decal pooling and idempotence behavior MUST continue to pass through existing or equivalent automated tests.
- **FR-008**: This story MUST NOT split AGridUI controller and renderer responsibilities or migrate individual gameplay producers beyond compatibility routing required for old marker APIs.
- **FR-009**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `MM-526` and the canonical Jira preset brief.

### Key Entities

- **Overlay Channel**: A named ownership lane for one gameplay, selection, debug, or compatibility overlay producer.
- **Overlay Layer State**: The desired overlay state stored for one channel, including visual intent, tile indexes, visibility, priority, stacking, and revision.
- **Overlay Reducer**: The AGridUI behavior that converts active channel layer state into the existing marker/decal rendering representation.
- **Legacy Compatibility Route**: The compatibility path that keeps old marker APIs working while associating their output with LegacyCompatibility.
- **Warning Diagnostic**: Structured evidence emitted for non-approved legacy marker calls when diagnostics are enabled.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All ten required overlay channel names are represented in the target AGridUI channel model.
- **SC-002**: Automated validation proves every required FGridOverlayLayerState field is retained when a layer is set.
- **SC-003**: Blueprint or equivalent runtime-facing tests can call SetOverlayLayer and ClearOverlayLayer using tile indexes.
- **SC-004**: A channel-isolation test proves clearing HoverMoveRange leaves PlanningMoveRange active when both use Movement visuals.
- **SC-005**: Existing marker/decal renderer tests or equivalent integration tests prove reduced channel layers continue to render through the current decal path.
- **SC-006**: Legacy API tests prove old marker calls still render through LegacyCompatibility and warning diagnostics are emitted for non-approved call sites when enabled.
- **SC-007**: Final validation preserves passing decal pooling/idempotence coverage.
- **SC-008**: Final verification can trace `MM-526`, the canonical Jira preset brief, and DESIGN-REQ-001 through DESIGN-REQ-009 through active MoonSpec artifacts and test evidence.
