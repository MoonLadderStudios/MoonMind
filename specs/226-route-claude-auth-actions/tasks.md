# Tasks: Route Claude Auth Actions

**Input**: Design documents from `/specs/226-route-claude-auth-actions/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration-style UI tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped by phase around the single Claude Settings auth action story so the work stays focused, traceable, and independently testable.

**Source Traceability**: MM-445; DESIGN-REQ-001, DESIGN-REQ-003, DESIGN-REQ-007; FR-001 through FR-010; SC-001 through SC-006.

**Test Commands**:

- Unit tests: `npm run ui:test -- frontend/src/components/settings/ProviderProfilesManager.test.tsx`
- Integration tests: `npm run ui:test -- frontend/src/components/settings/ProviderProfilesManager.test.tsx`
- Final verification: `/speckit.verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Path Conventions

- **Web app**: `frontend/src/components/settings/` for Settings UI and tests

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the existing Settings test harness is ready for this story.

- [X] T001 Confirm `frontend/src/components/settings/ProviderProfilesManager.test.tsx` can run with `npm run ui:test -- frontend/src/components/settings/ProviderProfilesManager.test.tsx`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish the row-action classification boundary before story implementation.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [X] T002 Identify the current Codex OAuth action path and row status rendering in `frontend/src/components/settings/ProviderProfilesManager.tsx` for FR-004 and DESIGN-REQ-003
- [X] T003 Define the test fixture shape for trusted Claude auth metadata in `frontend/src/components/settings/ProviderProfilesManager.test.tsx` for FR-003, FR-005, and FR-008

**Checkpoint**: Foundation ready - story test and implementation work can now begin.

---

## Phase 3: Story - Claude Settings Auth Actions

**Summary**: As an operator configuring provider profiles, I want `claude_anthropic` rows in Providers & Secrets to show Claude-specific authentication actions so I can start or manage Claude enrollment without seeing Codex-shaped OAuth controls.

**Independent Test**: Render disconnected Claude, connected Claude, unsupported Claude, and Codex OAuth provider profile rows. The story passes when Claude rows show only trusted Claude-specific actions and status, unsupported rows fail closed, and Codex OAuth behavior remains unchanged.

**Traceability**: MM-445, FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, DESIGN-REQ-001, DESIGN-REQ-003, DESIGN-REQ-007

**Test Plan**:

- Unit: auth action classification from provider profile metadata, Claude label selection, unsupported metadata fail-closed behavior
- Integration-style UI: rendered row actions/status and preserved Codex OAuth request behavior in the existing component test harness

### Unit Tests (write first)

> **NOTE**: Write these tests FIRST. Run them, confirm they FAIL for the expected reason, then implement only enough code to make them pass.

- [X] T004 [P] Add failing UI test for disconnected `claude_anthropic` showing `Connect Claude` and no generic `Auth` in `frontend/src/components/settings/ProviderProfilesManager.test.tsx` for FR-002, FR-006, SC-001, DESIGN-REQ-007
- [X] T005 [P] Add failing UI test for connected `claude_anthropic` showing supported `Replace token`, `Validate`, and `Disconnect` actions in `frontend/src/components/settings/ProviderProfilesManager.test.tsx` for FR-005, SC-002, DESIGN-REQ-007
- [X] T006 [P] Add failing UI test for unsupported or missing Claude metadata hiding Claude lifecycle actions in `frontend/src/components/settings/ProviderProfilesManager.test.tsx` for FR-003, FR-008
- [X] T007 [P] Add failing UI test for Claude auth status text from trusted metadata in `frontend/src/components/settings/ProviderProfilesManager.test.tsx` for FR-009, DESIGN-REQ-001
- [X] T008 Run `npm run ui:test -- frontend/src/components/settings/ProviderProfilesManager.test.tsx` and confirm T004-T007 fail for the expected missing Claude action behavior

### Integration Tests (write first)

- [X] T009 [P] Preserve or update Codex OAuth regression assertions in `frontend/src/components/settings/ProviderProfilesManager.test.tsx` for FR-004 and SC-003
- [X] T010 Run `npm run ui:test -- frontend/src/components/settings/ProviderProfilesManager.test.tsx` and confirm only the new Claude tests fail while existing Codex OAuth assertions remain meaningful

### Implementation

- [X] T011 Replace `isCodexOAuthCapable` with a provider-profile auth action classifier in `frontend/src/components/settings/ProviderProfilesManager.tsx` for FR-003 and DESIGN-REQ-003
- [X] T012 Render disconnected Claude `Connect Claude` row action without invoking the Codex OAuth mutation in `frontend/src/components/settings/ProviderProfilesManager.tsx` for FR-002, FR-006, and FR-007
- [X] T013 Render connected Claude lifecycle actions from supported trusted metadata in `frontend/src/components/settings/ProviderProfilesManager.tsx` for FR-005 and DESIGN-REQ-007
- [X] T014 Suppress Claude lifecycle actions when provider identity or trusted metadata is absent in `frontend/src/components/settings/ProviderProfilesManager.tsx` for FR-008
- [X] T015 Render optional Claude auth status text in the Status cell without exposing secrets in `frontend/src/components/settings/ProviderProfilesManager.tsx` for FR-009
- [X] T016 Preserve Codex OAuth session start/finalize/retry/cancel rendering and request behavior in `frontend/src/components/settings/ProviderProfilesManager.tsx` for FR-004
- [X] T017 Run `npm run ui:test -- frontend/src/components/settings/ProviderProfilesManager.test.tsx` and fix failures until the focused Settings suite passes

**Checkpoint**: The story is fully functional, covered by focused UI tests, and testable independently.

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: Strengthen the completed story without adding hidden scope.

- [X] T018 [P] Review `frontend/src/components/settings/ProviderProfilesManager.tsx` for readable labels, accessible aria labels, and narrow row layout containment for SC-005
- [X] T019 [P] Confirm no standalone Claude auth route, page, or specs directory was created outside `specs/226-route-claude-auth-actions` for FR-007 and SC-005
- [X] T020 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` for final unit verification
- [X] T021 Run `/speckit.verify` to validate the final implementation against MM-445 and DESIGN-REQ-001, DESIGN-REQ-003, and DESIGN-REQ-007

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - blocks story work
- **Story (Phase 3)**: Depends on Foundational phase completion
- **Polish (Phase 4)**: Depends on the story being functionally complete and tests passing

### Within The Story

- Unit tests T004-T007 MUST be written and fail before implementation tasks T011-T016
- Integration-style Codex regression check T009-T010 MUST remain meaningful before implementation
- T011 classifier work must precede Claude rendering tasks T012-T015
- T016 preserves Codex OAuth behavior and must be checked before final story validation T017
- Final verification T021 runs only after focused and full unit tests pass or are explicitly blocked

### Parallel Opportunities

- T004-T007 can be authored together because they add independent assertions in the same test file but should be merged carefully
- T012-T015 can be reasoned about together after T011, but implementation touches the same component and should be applied serially
- T018 and T019 can run in parallel after story tests pass

---

## Parallel Example: Story Phase

```bash
# Launch conceptual test authoring together, then merge in one file:
Task: "Add disconnected Claude action assertion in frontend/src/components/settings/ProviderProfilesManager.test.tsx"
Task: "Add connected Claude lifecycle assertion in frontend/src/components/settings/ProviderProfilesManager.test.tsx"
Task: "Add unsupported Claude fail-closed assertion in frontend/src/components/settings/ProviderProfilesManager.test.tsx"
```

---

## Implementation Strategy

### Test-Driven Story Delivery

1. Complete setup and foundation checks.
2. Add focused failing tests for Claude row actions/status and unsupported metadata.
3. Confirm the focused test suite fails for the intended missing Claude behavior.
4. Implement a small auth action classifier in `ProviderProfilesManager.tsx`.
5. Render Claude-specific actions and status while preserving Codex OAuth mutations.
6. Pass focused UI tests.
7. Run full required unit verification.
8. Run `/speckit.verify` and preserve MM-445 in the final report.

---

## Notes

- MM-445 and the Jira preset brief are the canonical source for this story.
- MM-446 is recorded as a Jira blocker dependency in the orchestration input; implementation should not broaden scope to MM-446.
- Do not add token paste persistence, new backend endpoints, or standalone Claude auth pages in this story.
