# Tasks: Channel-Owned Overlay Intent API

**Input**: Design documents from `/specs/268-channel-overlay-api/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks cover exactly one story: `Channel-Owned AGridUI Overlay Layers`.

**Source Traceability**: `MM-526`, FR-001 through FR-009, SCN-001 through SCN-005, SC-001 through SC-008, and DESIGN-REQ-001 through DESIGN-REQ-009.

**Test Commands**:

- Unit tests: target Tactics frontend unit/controller test command for AGridUI overlay API tests
- Integration tests: target Tactics frontend integration/controller-level command for marker/decal renderer and diagnostics tests
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel when paths differ and dependencies are complete
- Each task includes a target path. `TARGET_PROJECT_ROOT` means the root of the Tactics frontend repository/workspace containing `Docs/TacticsFrontend/GridUiOverlaySystem.md`; this root is not present in the current MoonMind checkout.

## Phase 1: Setup

**Purpose**: Establish the target runtime workspace and preserve traceability before any story implementation.

- [ ] T001 Verify `TARGET_PROJECT_ROOT/Docs/TacticsFrontend/GridUiOverlaySystem.md` exists and records source sections 7.1, 8, 9, 10, and 24 for MM-526. (DESIGN-REQ-001, DESIGN-REQ-009)
- [ ] T002 Verify `TARGET_PROJECT_ROOT` contains AGridUI runtime source, marker/decal renderer source, legacy marker APIs, and existing decal pooling/idempotence tests before editing implementation files. (FR-001, FR-007)
- [ ] T003 Verify target unit/controller test command for AGridUI overlay channel, layer state, and API tests and record it in `specs/268-channel-overlay-api/quickstart.md`. (FR-001, FR-002, FR-003)
- [ ] T004 Verify target integration test command for marker/decal renderer, channel isolation, diagnostics, and legacy compatibility tests and record it in `specs/268-channel-overlay-api/quickstart.md`. (FR-004, FR-005, FR-006, FR-007)
- [ ] T005 Preserve `MM-526` and the canonical Jira preset brief in any target implementation notes or delivery metadata. (FR-009, SC-008)

---

## Phase 2: Foundational

**Purpose**: Add or locate the target test harness and public contract locations that block story implementation.

**CRITICAL**: No story implementation work can begin until T001 through T009 are complete.

- [ ] T006 Identify target AGridUI header/interface and implementation files for the overlay channel model, layer state, and BlueprintCallable API in `TARGET_PROJECT_ROOT/Source/`. (FR-001, FR-002, FR-003)
- [ ] T007 Identify target marker/decal renderer integration points and reducer location in `TARGET_PROJECT_ROOT/Source/`. (FR-004, DESIGN-REQ-005)
- [ ] T008 Identify target legacy marker API call surface and approved legacy call-site policy location in `TARGET_PROJECT_ROOT/Source/` or `TARGET_PROJECT_ROOT/Docs/TacticsFrontend/`. (FR-006, DESIGN-REQ-007)
- [ ] T009 Identify or create target test fixture locations for overlay model, API, channel isolation, reducer integration, legacy diagnostics, and decal pooling/idempotence tests in `TARGET_PROJECT_ROOT/Tests/GridUi/`. (FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007)

**Checkpoint**: Target source and test harness locations are ready; red-first story tests can begin.

---

## Phase 3: Story - Channel-Owned AGridUI Overlay Layers

**Summary**: As a tactics frontend maintainer, I want AGridUI to accept overlay intent by explicit channel so multiple gameplay producers can own, update, and clear their overlays without erasing unrelated producer state.

**Independent Test**: Exercise AGridUI with multiple overlay channels, verify SetOverlayLayer and ClearOverlayLayer update only the requested channel, confirm reducer output still renders through the existing decal path, validate legacy API routing and diagnostics, and rerun decal pooling/idempotence tests.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, SCN-001, SCN-002, SCN-003, SCN-004, SCN-005, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, SC-007, SC-008, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-009.

**Test Plan**:

- Unit: channel model values, layer state retention, public API behavior, channel-isolated clear semantics, legacy compatibility diagnostics.
- Integration: active layer reduction into the existing marker/decal renderer, existing decal pooling/idempotence preservation, and end-to-end legacy compatibility behavior.

### Unit Tests (write first)

- [ ] T010 [P] Add failing unit/controller test for all required EGridOverlayChannel values in `TARGET_PROJECT_ROOT/Tests/GridUi/GridUiOverlayChannelTests.*`. (FR-001, SC-001, DESIGN-REQ-002)
- [ ] T011 [P] Add failing unit/controller test for FGridOverlayLayerState retaining channel, marker type, tile indexes, reason, style id, priority override, stacking flag, visibility flag, and revision in `TARGET_PROJECT_ROOT/Tests/GridUi/GridUiOverlayLayerStateTests.*`. (FR-002, SC-002, DESIGN-REQ-003)
- [ ] T012 [P] Add failing API-facing test for BlueprintCallable SetOverlayLayer and ClearOverlayLayer using tile indexes in `TARGET_PROJECT_ROOT/Tests/GridUi/GridUiOverlayApiTests.*`. (FR-003, SC-003, DESIGN-REQ-004)
- [ ] T013 [P] Add failing unit/controller test proving ClearOverlayLayer(HoverMoveRange) does not clear PlanningMoveRange when both resolve to Movement visuals in `TARGET_PROJECT_ROOT/Tests/GridUi/GridUiOverlayChannelIsolationTests.*`. (FR-005, SC-004, DESIGN-REQ-006)
- [ ] T014 [P] Add failing unit/controller test for legacy marker API routing through LegacyCompatibility and warning diagnostics for non-approved call sites when enabled in `TARGET_PROJECT_ROOT/Tests/GridUi/GridUiOverlayLegacyCompatibilityTests.*`. (FR-006, SC-006, DESIGN-REQ-007)
- [ ] T015 Run the target unit/controller test command for T010 through T014 and confirm each new test fails for the expected missing behavior before production implementation. (FR-001, FR-002, FR-003, FR-005, FR-006)

### Integration Tests (write first)

- [ ] T016 [P] Add failing integration/controller-level test proving active channel layers reduce into the existing marker/decal renderer in `TARGET_PROJECT_ROOT/Tests/GridUi/GridUiOverlayRendererIntegrationTests.*`. (FR-004, SC-005, DESIGN-REQ-005)
- [ ] T017 [P] Add failing integration/controller-level test preserving existing decal pooling/idempotence behavior after channel overlay operations in `TARGET_PROJECT_ROOT/Tests/GridUi/GridUiDecalPoolingIntegrationTests.*`. (FR-007, SC-007, DESIGN-REQ-008)
- [ ] T018 [P] Add failing integration/controller-level test proving legacy API calls still render through LegacyCompatibility without mutating unrelated channels in `TARGET_PROJECT_ROOT/Tests/GridUi/GridUiOverlayLegacyIntegrationTests.*`. (FR-006, FR-008, DESIGN-REQ-007, DESIGN-REQ-009)
- [ ] T019 Run the target integration test command for T016 through T018 and confirm each new test fails for the expected missing behavior before implementation. (FR-004, FR-006, FR-007, FR-008)

### Implementation

- [ ] T020 Add EGridOverlayChannel to the target AGridUI runtime source in `TARGET_PROJECT_ROOT/Source/` until T010 passes. (FR-001, DESIGN-REQ-002)
- [ ] T021 Add FGridOverlayLayerState to the target AGridUI runtime source in `TARGET_PROJECT_ROOT/Source/` until T011 passes. (FR-002, DESIGN-REQ-003)
- [ ] T022 Add BlueprintCallable SetOverlayLayer and ClearOverlayLayer APIs using tile indexes in `TARGET_PROJECT_ROOT/Source/` until T012 passes. (FR-003, DESIGN-REQ-004)
- [ ] T023 Add per-channel overlay state storage and revision behavior in `TARGET_PROJECT_ROOT/Source/` until T011 and T012 pass. (FR-002, FR-003)
- [ ] T024 Add channel-isolated clear behavior in `TARGET_PROJECT_ROOT/Source/` until T013 passes. (FR-005, DESIGN-REQ-006)
- [ ] T025 Add reducer behavior from active channel layers into the existing marker/decal rendering path in `TARGET_PROJECT_ROOT/Source/` until T016 passes. (FR-004, DESIGN-REQ-005)
- [ ] T026 Route legacy marker APIs through LegacyCompatibility and add warning diagnostics for non-approved call sites when enabled in `TARGET_PROJECT_ROOT/Source/` until T014 and T018 pass. (FR-006, DESIGN-REQ-007)
- [ ] T027 Preserve existing decal pooling/idempotence behavior and update equivalent assertions only when target behavior remains unchanged in `TARGET_PROJECT_ROOT/Tests/GridUi/` until T017 passes. (FR-007, DESIGN-REQ-008)
- [ ] T028 Review implementation to confirm it does not split controller/renderer responsibilities or migrate individual gameplay producers beyond compatibility routing. (FR-008, DESIGN-REQ-009)
- [ ] T029 Run the target unit/controller test command and fix failures without expanding scope beyond MM-526. (FR-001, FR-002, FR-003, FR-005, FR-006, FR-008)
- [ ] T030 Run the target integration test command and fix failures without expanding scope beyond MM-526. (FR-004, FR-006, FR-007, FR-008)

**Checkpoint**: The story is functional in the target project, red-first tests now pass, channel ownership is isolated, and the existing renderer boundary is preserved.

---

## Phase 4: Story Validation And Polish

**Purpose**: Validate the completed single story and preserve delivery traceability.

- [ ] T031 Run the quickstart validation in `specs/268-channel-overlay-api/quickstart.md` against `TARGET_PROJECT_ROOT`. (SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, SC-007)
- [ ] T032 Verify all final target test outputs preserve or reference `MM-526` where project conventions support traceability. (FR-009, SC-008)
- [ ] T033 Run `/moonspec-verify` for `specs/268-channel-overlay-api/spec.md` and record the final verdict. (FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: Must confirm the target Tactics frontend workspace exists.
- **Foundational (Phase 2)**: Depends on Setup and blocks story work.
- **Story (Phase 3)**: Depends on Foundational and must follow red-first testing.
- **Validation And Polish (Phase 4)**: Depends on story implementation and passing target tests.

### Within The Story

- T010 through T014 must be written and fail before T020 through T026.
- T016 through T018 must be written and fail before T025 through T027.
- T024 depends on T020 through T023.
- T025 depends on T020 through T024.
- T026 depends on `contracts/overlay-api-contract.md`.
- T033 is last.

### Parallel Opportunities

- T003 and T004 can run in parallel after T001 and T002.
- T010 through T014 can be authored in parallel because they target distinct test concerns.
- T016 through T018 can be authored in parallel.
- T020 and T021 can begin in parallel after red-first tests are confirmed, if target source ownership allows.

## Parallel Example: Story Phase

```text
Task: "Add failing unit/controller test for all required EGridOverlayChannel values in TARGET_PROJECT_ROOT/Tests/GridUi/GridUiOverlayChannelTests.*"
Task: "Add failing unit/controller test proving ClearOverlayLayer(HoverMoveRange) does not clear PlanningMoveRange in TARGET_PROJECT_ROOT/Tests/GridUi/GridUiOverlayChannelIsolationTests.*"
Task: "Add failing integration/controller-level test proving active channel layers reduce into the existing marker/decal renderer in TARGET_PROJECT_ROOT/Tests/GridUi/GridUiOverlayRendererIntegrationTests.*"
```

## Implementation Strategy

1. Stop immediately if `TARGET_PROJECT_ROOT` is unavailable; the current MoonMind checkout does not contain the target runtime source.
2. Complete setup and foundational source/test location tasks.
3. Write red-first unit/controller tests and confirm expected failures.
4. Write red-first integration/controller tests and confirm expected failures.
5. Add channel model, layer state, public APIs, per-channel storage, channel-isolated clear behavior, reducer behavior, legacy compatibility routing, and diagnostics until tests pass.
6. Run target unit and integration suites.
7. Run quickstart validation.
8. Run `/moonspec-verify`.

## Notes

- This task list covers one story only.
- Do not run `moonspec-breakdown`; `MM-526` is already a single-story runtime request.
- Do not split controller/renderer responsibilities in this story.
- Do not migrate individual gameplay producers beyond compatibility routing required for old marker APIs.
- Do not map target runtime implementation onto the MoonMind repository; use the Tactics frontend workspace that contains AGridUI and the marker/decal renderer.
