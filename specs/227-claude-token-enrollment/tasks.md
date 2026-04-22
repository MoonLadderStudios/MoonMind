# Tasks: Claude Token Enrollment Drawer

**Input**: Design documents from `/specs/227-claude-token-enrollment/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration-style UI tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped by phase around the single Claude manual token enrollment story so the work stays focused, traceable, and independently testable.

**Source Traceability**: MM-446; DESIGN-REQ-005, DESIGN-REQ-008, DESIGN-REQ-009; FR-001 through FR-013; SC-001 through SC-007.

**Test Commands**:

- Unit tests: `npm run ui:test -- frontend/src/components/settings/ProviderProfilesManager.test.tsx`
- Integration tests: `npm run ui:test -- frontend/src/components/settings/ProviderProfilesManager.test.tsx`
- Final verification: `/moonspec-verify`

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

**Purpose**: Establish the existing Claude action and status metadata boundary before drawer implementation.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [X] T002 Inspect current Claude manual auth action classification in `frontend/src/components/settings/ProviderProfilesManager.tsx` for FR-001, FR-011, and DESIGN-REQ-005
- [X] T003 Define test fixture metadata for Claude readiness details in `frontend/src/components/settings/ProviderProfilesManager.test.tsx` for FR-009, FR-010, and DESIGN-REQ-009

**Checkpoint**: Foundation ready - story test and implementation work can now begin.

---

## Phase 3: Story - Claude Manual Token Enrollment

**Summary**: As an operator connecting Claude Anthropic, I want a focused manual token enrollment drawer so I can follow the external Claude enrollment ceremony, paste the returned token securely, and understand validation, save, profile update, ready, or failed states.

**Independent Test**: Render a supported `claude_anthropic` provider row, open `Connect Claude`, move through external-step, token-paste, manual-auth submission, ready, cancel, and failed states, then confirm the token is cleared/redacted, readiness status renders in the row, and Codex OAuth behavior is not invoked.

**Traceability**: MM-446, FR-001 through FR-013, SC-001 through SC-007, DESIGN-REQ-005, DESIGN-REQ-008, DESIGN-REQ-009

**Test Plan**:

- Unit: lifecycle-state rendering, empty-token blocking, token clearing, failure redaction, readiness metadata formatting
- Integration-style UI: mocked manual-auth request shape, row-to-drawer interaction, no `/api/v1/oauth-sessions` call for Claude

### Unit Tests (write first)

> **NOTE**: Write these tests FIRST. Run them, confirm they FAIL for the expected reason, then implement only enough code to make them pass.

- [X] T004 [P] Add failing UI test that `Connect Claude` opens a drawer/modal with manual enrollment copy, `not_connected`, `awaiting_external_step`, and no terminal OAuth wording in `frontend/src/components/settings/ProviderProfilesManager.test.tsx` for FR-001, FR-002, FR-003, SC-001, DESIGN-REQ-005, DESIGN-REQ-008
- [X] T005 [P] Add failing UI test for advancing to the secure token paste state and blocking empty submission in `frontend/src/components/settings/ProviderProfilesManager.test.tsx` for FR-004, FR-012, DESIGN-REQ-008
- [X] T006 [P] Add failing UI test for submitted token progress through `validating_token`, `saving_secret`, `updating_profile`, and `ready` in `frontend/src/components/settings/ProviderProfilesManager.test.tsx` for FR-002, FR-007, SC-002, DESIGN-REQ-008
- [X] T007 [P] Add failing UI test proving success and cancellation clear the token input value in `frontend/src/components/settings/ProviderProfilesManager.test.tsx` for FR-005, FR-006, SC-003
- [X] T008 [P] Add failing UI test proving validation failure output redacts the submitted token and token-like substrings in `frontend/src/components/settings/ProviderProfilesManager.test.tsx` for FR-008, SC-004, DESIGN-REQ-009
- [X] T009 [P] Add failing UI test proving structured Claude readiness metadata renders connected/not connected, last validated, failure, backing secret, and launch-ready details in `frontend/src/components/settings/ProviderProfilesManager.test.tsx` for FR-009, FR-010, SC-005, DESIGN-REQ-009
- [X] T010 Run `npm run ui:test -- frontend/src/components/settings/ProviderProfilesManager.test.tsx` and confirm T004-T009 fail for the expected missing drawer/readiness behavior

### Integration Tests (write first)

- [X] T011 [P] Add mocked request test proving Claude manual token submission calls `/api/v1/provider-profiles/{profile_id}/manual-auth/commit` with the token and does not call `/api/v1/oauth-sessions` in `frontend/src/components/settings/ProviderProfilesManager.test.tsx` for FR-011 and SC-006
- [X] T012 Preserve existing Codex OAuth start/finalize/retry tests in `frontend/src/components/settings/ProviderProfilesManager.test.tsx` for FR-011 and SC-006
- [X] T013 Run `npm run ui:test -- frontend/src/components/settings/ProviderProfilesManager.test.tsx` and confirm only the new Claude drawer/manual-auth tests fail while existing Codex OAuth assertions remain meaningful

### Implementation

- [X] T014 Add Claude enrollment drawer state, selected profile state, token state, and redaction helpers in `frontend/src/components/settings/ProviderProfilesManager.tsx` for FR-002, FR-005, FR-006, FR-008, DESIGN-REQ-008, DESIGN-REQ-009
- [X] T015 Wire Claude auth action buttons to open the drawer/modal instead of only emitting a notice in `frontend/src/components/settings/ProviderProfilesManager.tsx` for FR-001, FR-003, FR-011
- [X] T016 Render external instruction and secure token paste states in `frontend/src/components/settings/ProviderProfilesManager.tsx` for FR-004 and DESIGN-REQ-008
- [X] T017 Implement empty-token blocking and manual-auth submit progress states in `frontend/src/components/settings/ProviderProfilesManager.tsx` for FR-007, FR-012, SC-002
- [X] T018 Implement secret-free ready and failed state rendering with failure redaction in `frontend/src/components/settings/ProviderProfilesManager.tsx` for FR-005, FR-008, DESIGN-REQ-009
- [X] T019 Render structured Claude readiness metadata in the provider row Status cell in `frontend/src/components/settings/ProviderProfilesManager.tsx` for FR-009, FR-010
- [X] T020 Preserve Codex OAuth session start/finalize/retry/cancel behavior in `frontend/src/components/settings/ProviderProfilesManager.tsx` for FR-011 and SC-006
- [X] T021 Run `npm run ui:test -- frontend/src/components/settings/ProviderProfilesManager.test.tsx` and fix failures until the focused Settings suite passes

**Checkpoint**: The story is fully functional, covered by focused UI tests, and testable independently.

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: Strengthen the completed story without adding hidden scope.

- [X] T022 [P] Review `frontend/src/components/settings/ProviderProfilesManager.tsx` for accessible drawer labels, keyboard-safe controls, and narrow layout containment for SC-001
- [X] T023 [P] Confirm no standalone Claude auth route or page was created and no token appears in notices or rendered failure text for FR-003, FR-008, and FR-011
- [X] T024 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` for final unit verification
- [X] T025 Run `/moonspec-verify` to validate the final implementation against MM-446 and DESIGN-REQ-005, DESIGN-REQ-008, and DESIGN-REQ-009

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - blocks story work
- **Story (Phase 3)**: Depends on Foundational phase completion
- **Polish (Phase 4)**: Depends on the story being functionally complete and tests passing

### Within The Story

- Unit tests T004-T009 MUST be written and fail before implementation tasks T014-T020
- Integration-style no-Codex-OAuth test T011 MUST be written before implementation
- T014 state model work must precede drawer rendering and submit tasks T015-T018
- T020 preserves Codex OAuth behavior and must be checked before final story validation T021
- Final verification T025 runs only after focused and full unit tests pass or are explicitly blocked

### Parallel Opportunities

- T004-T009 can be authored together because they add independent assertions in the same test file but should be merged carefully
- T014-T019 touch the same component and should be applied serially
- T022 and T023 can run in parallel after story tests pass

---

## Implementation Strategy

### Test-Driven Story Delivery

1. Complete setup and foundation checks.
2. Add focused failing tests for drawer opening, lifecycle states, token clearing, failure redaction, readiness metadata, and no Codex OAuth call.
3. Confirm focused tests fail for the intended missing drawer/readiness behavior.
4. Implement a small Claude enrollment drawer state machine in `ProviderProfilesManager.tsx`.
5. Render manual enrollment states and readiness metadata while preserving Codex OAuth mutations.
6. Pass focused UI tests.
7. Run full required unit verification.
8. Run `/moonspec-verify` and preserve MM-446 in the final report.

---

## Notes

- MM-446 and the Jira preset brief are the canonical source for this story.
- The source design path `docs/ManagedAgents/ClaudeAnthropicOAuth.md` is treated as runtime source requirements.
- Do not add a standalone Claude auth page.
- Do not store or render submitted token values.
