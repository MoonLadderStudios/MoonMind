# Feature Specification: Grid UI Marker Baseline

**Feature Branch**: `267-grid-ui-marker-baseline`
**Created**: 2026-04-26
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-525 as the canonical Moon Spec orchestration input.

Preserve the Jira issue key MM-525 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

Jira issue: MM-525
Issue type: Story
Status: In Progress
Summary: Inventory current Grid UI marker mutations and lock baseline regression coverage

Source Reference:
Source Document: Docs/TacticsFrontend/GridUiOverlaySystem.md
Source Title: Grid UI Overlay System
Source Sections:
- 1. Purpose
- 4. Core Principle
- 18. Diagnostics
- 20. Testing Strategy
- 21. Migration Rules

Coverage IDs:
- plan.phase0
- doc.diagnostics
- doc.testing.controller
- doc.testing.integration

Canonical Brief:
Establish an observable baseline for the existing Grid UI marker lifecycle before changing ownership semantics. Inventory all direct marker/decal mutation call sites, classify each producer role, and add focused regression coverage for the known bug class where one producer clears another producer's Movement overlay.

Acceptance Criteria:
- A checked-in inventory identifies direct uses of SpawnTileMarkers, SpawnTileMarkersFromIndexes, QueueSpawnTileMarkers, QueueSpawnTileMarkersFromIndexes, ClearTileMarkers, ClearAllTileMarkers, SpawnDecalsAtLocations, and ClearSpecifiedDecals.
- Each call site is categorized by producer role: selected movement, hover movement, attack targeting, ability preview, focus/selection, path/ghost path, phase clear, teardown clear, or debug/demo utility.
- At least one automated test captures the current Movement overlay interference bug class where clearing one Movement producer can erase another Movement producer's overlay.
- Existing Grid UI marker lifecycle/idempotence tests still pass or are updated with equivalent assertions.
- Diagnostics can distinguish producer churn from renderer churn with source, marker type, reason, owner controller, tile count, and operation type.

Jira labels:
diagnostics, grid-ui-overlay, jira, moonmind-workflow-mm-2e552d30-1728-450d-808a-968369cc4f9a, moonspec-breakdown, phase-0, tacticsfrontend, tests"

## User Story - Baseline Grid UI Marker Lifecycle

**Summary**: As a tactics frontend maintainer, I want the current Grid UI marker and decal mutation lifecycle inventoried and regression-covered so that later ownership changes can be made against a known, observable baseline.

**Goal**: Establish traceable baseline evidence for every direct marker/decal mutation producer and preserve automated coverage for the known Movement overlay interference bug class before changing ownership semantics.

**Independent Test**: Can be fully tested by reviewing the checked-in mutation inventory and running the marker lifecycle unit/integration tests that prove direct producer classifications, Movement overlay interference coverage, existing lifecycle/idempotence behavior, and diagnostic event detail are all present.

**Acceptance Scenarios**:

1. **Given** the Grid UI marker lifecycle contains direct mutation APIs, **When** maintainers review the baseline inventory, **Then** every direct use of SpawnTileMarkers, SpawnTileMarkersFromIndexes, QueueSpawnTileMarkers, QueueSpawnTileMarkersFromIndexes, ClearTileMarkers, ClearAllTileMarkers, SpawnDecalsAtLocations, and ClearSpecifiedDecals is listed with its source location.
2. **Given** an inventoried marker/decal mutation call site, **When** maintainers inspect its classification, **Then** the call site is assigned exactly one producer role from selected movement, hover movement, attack targeting, ability preview, focus/selection, path/ghost path, phase clear, teardown clear, or debug/demo utility.
3. **Given** two Movement overlay producers are active, **When** one producer clears its Movement overlay, **Then** automated regression coverage demonstrates whether another producer's Movement overlay can be erased and captures the current Movement overlay interference bug class as baseline evidence.
4. **Given** existing Grid UI marker lifecycle and idempotence expectations, **When** the baseline test suite runs, **Then** those expectations still pass or equivalent assertions document the same behavior.
5. **Given** marker producer or renderer activity changes, **When** diagnostics are emitted, **Then** diagnostic evidence includes source, marker type, reason, owner controller, tile count, and operation type so producer churn can be distinguished from renderer churn.

### Edge Cases

- A direct mutation call site is used only by a debug or demo utility and still requires inventory coverage without being treated as production gameplay behavior.
- A clear operation has broad scope, such as phase or teardown cleanup, and must be classified separately from producer-owned overlay cleanup.
- A call site's producer role is ambiguous from the call alone and requires neighboring gameplay context to avoid misclassification.
- Diagnostic events may have zero affected tiles or unknown owner controller context and still need enough structured detail to distinguish producer churn from renderer churn.

## Assumptions

- The referenced source document `Docs/TacticsFrontend/GridUiOverlaySystem.md` is not present in this checkout; the trusted MM-525 Jira brief is the canonical source for this story.
- Runtime mode applies: the story requires product/test artifacts for the Grid UI marker lifecycle, not documentation-only changes.
- If the target Tactics frontend source tree is unavailable in this repository, implementation must stop at the first stage that proves the required source or tests cannot be located.

## Source Design Requirements

| ID | Source | Requirement | Scope | Mapped Requirement |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-001 | MM-525 Source Sections 1, 4; Coverage ID `plan.phase0` | Establish a baseline inventory before changing Grid UI marker ownership semantics. | In scope | FR-001, FR-002 |
| DESIGN-REQ-002 | MM-525 Acceptance Criteria; Coverage ID `plan.phase0` | Inventory direct uses of SpawnTileMarkers, SpawnTileMarkersFromIndexes, QueueSpawnTileMarkers, QueueSpawnTileMarkersFromIndexes, ClearTileMarkers, ClearAllTileMarkers, SpawnDecalsAtLocations, and ClearSpecifiedDecals. | In scope | FR-001 |
| DESIGN-REQ-003 | MM-525 Acceptance Criteria; Source Section 4 | Classify each direct mutation call site by one producer role from the canonical role list. | In scope | FR-002 |
| DESIGN-REQ-004 | MM-525 Acceptance Criteria; Coverage ID `doc.testing.controller` | Add automated coverage for the current bug class where clearing one Movement producer can erase another Movement producer's overlay. | In scope | FR-003 |
| DESIGN-REQ-005 | MM-525 Acceptance Criteria; Coverage ID `doc.testing.integration` | Preserve existing Grid UI marker lifecycle/idempotence behavior through existing or equivalent tests. | In scope | FR-004 |
| DESIGN-REQ-006 | MM-525 Source Section 18; Coverage ID `doc.diagnostics` | Diagnostics must expose source, marker type, reason, owner controller, tile count, and operation type so producer churn and renderer churn are distinguishable. | In scope | FR-005 |
| DESIGN-REQ-007 | MM-525 Source Section 21 | Do not change ownership semantics as part of this baseline story. | In scope | FR-006 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST include a checked-in inventory of every direct use of SpawnTileMarkers, SpawnTileMarkersFromIndexes, QueueSpawnTileMarkers, QueueSpawnTileMarkersFromIndexes, ClearTileMarkers, ClearAllTileMarkers, SpawnDecalsAtLocations, and ClearSpecifiedDecals.
- **FR-002**: The inventory MUST categorize every listed call site with exactly one producer role from selected movement, hover movement, attack targeting, ability preview, focus/selection, path/ghost path, phase clear, teardown clear, or debug/demo utility.
- **FR-003**: The test suite MUST include automated regression coverage for the current Movement overlay interference bug class where clearing one Movement producer can erase another Movement producer's overlay.
- **FR-004**: Existing Grid UI marker lifecycle and idempotence expectations MUST continue to pass, or equivalent assertions MUST preserve the same observable guarantees.
- **FR-005**: Diagnostic evidence for marker/decal producer and renderer activity MUST include source, marker type, reason, owner controller, tile count, and operation type.
- **FR-006**: The baseline story MUST NOT change Grid UI marker ownership semantics beyond the minimum test or diagnostic instrumentation required to observe current behavior.
- **FR-007**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `MM-525` and the canonical Jira preset brief.

### Key Entities

- **Marker/Decal Mutation Call Site**: A source location that directly invokes one of the marker or decal mutation APIs named by the Jira brief.
- **Producer Role**: The gameplay, lifecycle, or utility responsibility assigned to a mutation call site for baseline analysis.
- **Movement Overlay Producer**: A producer that emits Movement marker overlays and may interfere with another Movement overlay producer during clear operations.
- **Diagnostic Event**: Structured evidence describing producer or renderer activity with source, marker type, reason, owner controller, tile count, and operation type.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of direct uses of the named marker/decal mutation APIs present in the available target source tree are represented in the checked-in inventory.
- **SC-002**: 100% of inventoried call sites have exactly one producer-role classification from the canonical role list.
- **SC-003**: At least one automated test fails on the known Movement overlay interference bug class before implementation or baseline assertion adjustment, then passes with the accepted baseline evidence.
- **SC-004**: All existing or equivalent Grid UI marker lifecycle/idempotence tests pass in the final validation run.
- **SC-005**: Diagnostic validation confirms every required diagnostic field is present for producer and renderer churn evidence.
- **SC-006**: Final verification can trace `MM-525`, the canonical Jira preset brief, and DESIGN-REQ-001 through DESIGN-REQ-007 through the active MoonSpec artifacts and test evidence.
