# Research: Visible Step Attachments

## FR-002, DESIGN-REQ-002, DESIGN-REQ-008 - Step + Affordance

Decision: Partial implementation; add a compact per-step + button backed by a hidden file input.
Evidence: `frontend/src/entrypoints/task-create.tsx` renders a visible `input type="file"` under each step with `aria-label="Step N attachments"`.
Rationale: The existing control is correctly step-scoped but fails the MM-410 requirement that the control not look like a generic browser file input and have explicit + affordance copy.
Alternatives considered: Keep the native file input and change only the label; rejected because the Jira brief explicitly requires a visible + button and non-generic browser input styling.
Test implications: Unit/UI tests for button role/name, hidden input accept filtering, image-only copy, and mixed-content copy.

## FR-004 - Append And Dedupe Semantics

Decision: Missing implementation; update step selection to append files and dedupe exact duplicate local file identities.
Evidence: `updateStepAttachments(localId, files)` assigns `next[localId] = files`, replacing the prior target list.
Rationale: Replacing selections conflicts with repeated + selection behavior. Stable local file identity can use name, size, type, and `lastModified`, matching existing preview key helpers.
Alternatives considered: Allow duplicate rows; rejected because the Jira brief explicitly requires exact duplicate dedupe.
Test implications: Unit test must select one file, select another, verify both remain, then select a duplicate and verify it is not duplicated.

## FR-005, DESIGN-REQ-001 - Target Ownership

Decision: Implemented but verify through the new control path.
Evidence: Selected step files are stored by `step.localId`; persisted refs live on the owning step; existing tests cover step reorder preserving payload refs.
Rationale: The new + control must reuse this path instead of introducing index-based ownership.
Alternatives considered: Use step index in DOM state; rejected because reorder would change target meaning.
Test implications: Integration-style UI test for same filename on different steps or reorder after append.

## FR-006, FR-007, FR-008, DESIGN-REQ-003, DESIGN-REQ-007

Decision: Implemented-unverified for MM-410; preserve existing summary, preview, error, retry, and remove behavior while changing the open control.
Evidence: `task-create.tsx` renders filename/type/size, `AttachmentPreview`, preview failure messages, target errors, Retry, and Remove for selected step files.
Rationale: The story should not redesign attachment rows. It should verify existing behavior is still reached through the new + button flow.
Alternatives considered: New attachment row component; rejected as unnecessary for this narrow story.
Test implications: Existing preview/upload tests remain, with one updated to use the new button/input path if needed.

## FR-009, FR-010, DESIGN-REQ-006

Decision: Implemented-unverified; preserve artifact-first submission and structured step refs.
Evidence: Submit logic uploads selected step files before `/api/executions` and constructs `task.steps[n].inputAttachments`.
Rationale: MM-410 is an authoring affordance and append behavior story, not an API contract rewrite.
Alternatives considered: Backend changes; rejected because the existing payload contract already supports the requirement.
Test implications: Existing upload-before-submit and structured payload tests remain final regression evidence.

## FR-011 - Edit/Rerun Compatibility

Decision: Implemented-unverified; preserve persisted refs and explicit empty-list removal behavior.
Evidence: `removePersistedStepAttachment`, persisted attachment rendering, and submit serialization already handle persisted step refs.
Rationale: The new local-file append path must coexist with persisted refs without changing the reconstruction contract.
Alternatives considered: Rewrite persisted attachment state; rejected as unnecessary and higher risk.
Test implications: Existing edit/rerun attachment tests remain final regression evidence.

## Test Strategy

Decision: Use focused Vitest coverage for both unit-level interaction and integration-style Create-page payload behavior, then run the full unit runner.
Evidence: Existing Create-page attachment tests use Testing Library, mocked artifact endpoints, and request payload inspection.
Rationale: The affected behavior is browser authoring state and request construction. Docker-backed integration is not required unless backend contracts change.
Alternatives considered: Add backend tests; rejected because no backend behavior changes are planned.
Test implications: Red-first focused tests in `frontend/src/entrypoints/task-create.test.tsx`, then `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx`, then `./tools/test_unit.sh`.
