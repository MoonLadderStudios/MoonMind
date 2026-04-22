# Tasks: Claude Settings Credential Actions

**Input**: Design documents from `/specs/239-claude-settings-credential-actions/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration-style UI tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped by phase around the single Claude Settings credential method story so the work stays focused, traceable, and independently testable.

**Source Traceability**: MM-477; DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-005; FR-001 through FR-014; SC-001 through SC-008.

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

**Purpose**: Establish the current row-action and enrollment boundaries before story implementation.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [X] T002 Inspect current Codex OAuth action routing in `frontend/src/components/settings/ProviderProfilesManager.tsx` for FR-013 and SC-006
- [X] T003 Inspect current Claude API-key/manual-auth drawer routing in `frontend/src/components/settings/ProviderProfilesManager.tsx` for FR-004, FR-006, FR-011, FR-012, and DESIGN-REQ-005
- [X] T004 Define the `claude_anthropic` OAuth/API-key fixture shape in `frontend/src/components/settings/ProviderProfilesManager.test.tsx` for FR-003, FR-004, FR-007, FR-008, and DESIGN-REQ-002

**Checkpoint**: Foundation ready - story test and implementation work can now begin.

---

## Phase 3: Story - Claude Credential Method Actions

**Summary**: As an operator managing Claude Anthropic provider profiles, I want OAuth enrollment and API-key enrollment to appear as distinct first-class row actions so that I can choose the correct credential method without mistaking one path for the other.

**Independent Test**: Render the Settings Provider Profiles table with a `claude_anthropic` row that supports OAuth volume enrollment, API-key enrollment, OAuth validation, and OAuth disconnect; activate each credential method action and verify the OAuth actions use the OAuth session lifecycle while the API-key action opens the API-key enrollment flow without creating an OAuth terminal session. Render `codex_default` alongside it and verify existing Codex OAuth labels and requests remain unchanged.

**Traceability**: MM-477, FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, FR-012, FR-013, FR-014, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, SC-007, SC-008, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-005

**Test Plan**:

- Unit: provider-profile auth action classification, Claude/Anthropic label selection, unsupported metadata fail-closed behavior, narrow layout action visibility
- Integration-style UI: rendered row actions, Claude OAuth session request payload, API-key action no-OAuth behavior, and Codex OAuth regression behavior in the existing component harness

### Unit Tests (write first)

> **NOTE**: Write these tests FIRST. Run them, confirm they FAIL for the expected reason, then implement only enough code to make them pass.

- [X] T005 [P] Add failing UI test for a supported `claude_anthropic` row showing `Connect with Claude OAuth` and `Use Anthropic API key` in `frontend/src/components/settings/ProviderProfilesManager.test.tsx` for FR-003, FR-004, FR-010, SC-001, DESIGN-REQ-002
- [X] T006 [P] Add failing UI test for metadata-supported `Validate OAuth` and `Disconnect OAuth` labels in `frontend/src/components/settings/ProviderProfilesManager.test.tsx` for FR-007, FR-008, SC-004, DESIGN-REQ-002
- [X] T007 [P] Add failing UI test for unsupported or metadata-free Claude rows hiding credential method actions in `frontend/src/components/settings/ProviderProfilesManager.test.tsx` for FR-009 and SC-005
- [X] T008 Run `npm run ui:test -- frontend/src/components/settings/ProviderProfilesManager.test.tsx` and confirm T005-T007 fail for the expected missing MM-477 action behavior

### Integration Tests (write first)

- [X] T009 [P] Add failing UI test that `Connect with Claude OAuth` calls `/api/v1/oauth-sessions` with `runtime_id=claude_code` and does not open the API-key drawer in `frontend/src/components/settings/ProviderProfilesManager.test.tsx` for FR-005, SC-002, DESIGN-REQ-002
- [X] T010 [P] Add failing UI test that `Use Anthropic API key` opens the API-key enrollment flow and makes no `/api/v1/oauth-sessions` request in `frontend/src/components/settings/ProviderProfilesManager.test.tsx` for FR-006, FR-011, FR-012, SC-003, DESIGN-REQ-005
- [X] T011 Preserve Codex OAuth regression assertions in `frontend/src/components/settings/ProviderProfilesManager.test.tsx` for FR-013 and SC-006
- [X] T012 Run `npm run ui:test -- frontend/src/components/settings/ProviderProfilesManager.test.tsx` and confirm only the new MM-477 Claude tests fail while existing Codex OAuth assertions remain meaningful

### Implementation

- [X] T013 Replace the Claude manual-only auth action labels with a credential-method action model in `frontend/src/components/settings/ProviderProfilesManager.tsx` for FR-003, FR-004, FR-007, FR-008, FR-010, and DESIGN-REQ-002
- [X] T014 Route `Connect with Claude OAuth` through the existing OAuth session mutation in `frontend/src/components/settings/ProviderProfilesManager.tsx` for FR-005 and SC-002
- [X] T015 Route `Use Anthropic API key` to the existing API-key/manual-auth enrollment drawer without OAuth session creation in `frontend/src/components/settings/ProviderProfilesManager.tsx` for FR-006, FR-011, FR-012, SC-003, and DESIGN-REQ-005
- [X] T016 Hide Claude credential method actions for unsupported or metadata-free rows in `frontend/src/components/settings/ProviderProfilesManager.tsx` for FR-009 and SC-005
- [X] T017 Preserve Codex OAuth session start/finalize/retry/cancel rendering and request behavior in `frontend/src/components/settings/ProviderProfilesManager.tsx` for FR-013 and SC-006
- [X] T018 Run `npm run ui:test -- frontend/src/components/settings/ProviderProfilesManager.test.tsx` and fix failures until the focused Settings suite passes

**Checkpoint**: The story is fully functional, covered by focused UI tests, and testable independently.

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: Strengthen the completed story without adding hidden scope.

- [X] T019 [P] Review `frontend/src/components/settings/ProviderProfilesManager.tsx` for readable Claude labels, accessible aria labels, and narrow row/card layout containment for SC-007
- [X] T020 [P] Confirm no standalone Claude auth route or page was created outside the existing Settings Provider Profiles flow for FR-001, FR-002, and DESIGN-REQ-001
- [X] T021 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` for final unit verification
- [X] T022 Run `/moonspec-verify` to validate the final implementation against MM-477 and DESIGN-REQ-001, DESIGN-REQ-002, and DESIGN-REQ-005

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - blocks story work
- **Story (Phase 3)**: Depends on Foundational phase completion
- **Polish (Phase 4)**: Depends on the story being functionally complete and tests passing

### Within The Story

- Unit tests T005-T007 MUST be written and fail before implementation tasks T013-T017.
- Integration-style UI tests T009-T011 MUST be written and fail or remain meaningful before implementation.
- T013 classifier work must precede routing tasks T014-T016.
- T017 preserves Codex behavior and must be checked before final story validation T018.
- Final verification T022 runs only after focused and full unit tests pass or are explicitly blocked.

### Parallel Opportunities

- T005-T007 can be authored together because they add independent assertions in the same test file but must be merged carefully.
- T009-T010 can be authored together with T011 because they validate different action paths in the same test harness.
- T019 and T020 can run in parallel after story tests pass.

---

## Parallel Example: Story Phase

```bash
# Launch conceptual test authoring together, then merge in one file:
Task: "Add supported Claude credential method action assertion in frontend/src/components/settings/ProviderProfilesManager.test.tsx"
Task: "Add Claude OAuth session request assertion in frontend/src/components/settings/ProviderProfilesManager.test.tsx"
Task: "Add Claude API-key no-OAuth assertion in frontend/src/components/settings/ProviderProfilesManager.test.tsx"
```

---

## Implementation Strategy

### Test-Driven Story Delivery

1. Complete setup and foundation checks.
2. Add focused failing tests for Claude OAuth/API-key method labels, OAuth lifecycle labels, unsupported metadata, OAuth request behavior, and API-key no-OAuth behavior.
3. Confirm the focused test suite fails for the intended missing MM-477 behavior.
4. Implement a credential-method action model in `ProviderProfilesManager.tsx`.
5. Route Claude OAuth through the existing OAuth session mutation.
6. Route Anthropic API-key enrollment through the existing Managed Secrets-backed enrollment drawer.
7. Pass focused UI tests.
8. Run full required unit verification.
9. Run `/moonspec-verify` and preserve MM-477 in the final report.

---

## Notes

- MM-477 and the Jira preset brief are the canonical source for this story.
- Do not add token persistence, OAuth backend endpoints, or standalone Claude auth pages unless focused verification exposes an unavoidable gap.
- The existing manual-auth commit endpoint is treated as the API-key enrollment backend for this row-action story because it stores a Managed Secret and materializes `ANTHROPIC_API_KEY`.
