# Research: Author Governed Tool Steps

## FR-001 / Trusted Tool Discovery

Decision: Implemented and verified. Use the existing same-origin `/mcp/tools` endpoint as the Create page discovery source and treat failures as non-blocking.
Evidence: `frontend/src/entrypoints/task-create.tsx` fetches discovered tools for Tool steps; `frontend/src/entrypoints/task-create.test.tsx` includes focused governed Tool authoring coverage.
Rationale: This preserves the trusted tool boundary and avoids raw Jira credentials or direct provider calls.
Alternatives considered: Hardcoded tool list rejected because it would drift and violate governed discovery.
Test implications: Frontend integration test with mocked discovery response.

## FR-002 / Manual Fallback

Decision: Implemented and verified. Keep manual typed Tool id, version, and schema-shaped inputs editable when discovery or dynamic option calls fail.
Evidence: `frontend/src/entrypoints/task-create.tsx` keeps manual Tool fields available; `frontend/src/entrypoints/task-create.test.tsx` covers discovery failure fallback.
Rationale: Governed discovery improves authoring but cannot become a single point of failure for typed Tool submission.
Alternatives considered: Blocking Tool authoring on discovery rejected because the spec requires manual fallback.
Test implications: Frontend integration test verifies visible unavailable state and editable manual fields.

## FR-003 / Grouping and Search

Decision: Implemented and verified. Derive group labels from the namespace before the first dot in the tool name, with a search field filtering by name, description, and group.
Evidence: `frontend/src/entrypoints/task-create.tsx` maintains Tool search state and grouped rendering; `frontend/src/entrypoints/task-create.test.tsx` verifies grouped/filterable trusted Tool choices.
Rationale: Provides immediate grouping without waiting for richer catalog metadata.
Alternatives considered: Backend catalog enrichment deferred; not required for this single story.
Test implications: Frontend integration test verifies Jira and GitHub groups and search filtering.

## FR-004 / Discovered Tool Selection

Decision: Implemented and verified. Selecting a discovered Tool populates the Tool id while preserving the existing version and inputs unless the author edits them.
Evidence: `frontend/src/entrypoints/task-create.tsx` handles discovered Tool selection; the grouped/filterable Tool test verifies selection behavior.
Rationale: Selection should reduce manual id entry while preserving author-controlled version pinning and input draft state.
Alternatives considered: Replacing all authored fields on selection rejected because it could discard user input.
Test implications: Frontend integration test covers selection and preserved authored input.

## FR-005 / Dynamic Jira Target Status Options

Decision: Implemented and verified. For `jira.transition_issue`, parse `issueKey` from Tool inputs JSON and call `/mcp/tools/call` with `jira.get_transitions`; populate target status options from returned transition `to.name` values.
Evidence: `frontend/src/entrypoints/task-create.tsx` calls `jira.get_transitions`; `frontend/src/entrypoints/task-create.test.tsx` verifies dynamic Jira target status loading.
Rationale: Uses trusted Jira data and avoids guessing transition IDs or statuses.
Alternatives considered: Static status list rejected because Jira workflows vary by issue and permission.
Test implications: Frontend integration test mocks `jira.get_transitions` and verifies JSON update/submission.

## FR-006 / Dynamic Status Input Update

Decision: Implemented and verified. Selecting a trusted target status writes `targetStatus` into Tool inputs JSON while preserving unrelated authored keys.
Evidence: `frontend/src/entrypoints/task-create.tsx` updates Tool inputs from selected status; dynamic Jira status test verifies the displayed JSON and submitted payload.
Rationale: The UI must submit deterministic schema-shaped inputs without guessing transition IDs.
Alternatives considered: Writing transition IDs rejected for this story because MM-576 requires target statuses and forbids guessed IDs.
Test implications: Frontend integration test verifies submitted Tool payload.

## FR-007 / Tool Terminology and Contract Copy

Decision: Implemented and verified. Display contract metadata for typed governed Tool execution while keeping Tool terminology and avoiding Script as a Step Type concept.
Evidence: `frontend/src/entrypoints/task-create.tsx` includes Tool contract metadata copy; focused UI tests exercise the governed Tool panel.
Rationale: Authors need visible contract context without introducing arbitrary script authoring language.
Alternatives considered: Adding Script wording rejected by DESIGN-REQ-019 and DESIGN-REQ-020.
Test implications: Frontend integration plus artifact review for user-facing terminology.

## FR-008 / Submission Boundary

Decision: Implemented and verified. Preserve existing Tool payload submission and backend contract tests for conflicting Skill payload and shell-like fields.
Evidence: `frontend/src/entrypoints/task-create.test.tsx` has governed Tool submission coverage; `tests/unit/workflows/tasks/test_task_contract.py` rejects conflicting payloads and shell-like fields.
Rationale: The new UI is an authoring affordance; the execution contract remains unchanged.
Alternatives considered: New backend validation not needed for this story.
Test implications: Rerun focused unit contract test and frontend submission test.

## SC-001 / Grouped Search Coverage

Decision: Implemented and verified.
Evidence: `frontend/src/entrypoints/task-create.test.tsx` includes a focused test for grouped and searchable trusted Tool choices.
Rationale: This is the direct acceptance proof for grouped discovery.
Alternatives considered: Manual-only verification rejected because the success criterion explicitly requires tests.
Test implications: Frontend integration.

## SC-002 / Dynamic Jira Status Coverage

Decision: Implemented and verified.
Evidence: `frontend/src/entrypoints/task-create.test.tsx` verifies selecting `jira.transition_issue`, choosing a trusted target status, and submitting the expected Tool payload.
Rationale: This proves the dynamic option path and submission boundary together.
Alternatives considered: Unit-only coverage rejected because the story is an authoring UI flow.
Test implications: Frontend integration.

## SC-003 / Fallback Coverage

Decision: Implemented and verified.
Evidence: `frontend/src/entrypoints/task-create.test.tsx` verifies discovery/transition failure fallback while manual Tool authoring remains available.
Rationale: The fallback behavior is a resilience requirement.
Alternatives considered: Silent failure rejected because the spec requires an unavailable-state message.
Test implications: Frontend integration.

## SC-004 / Contract Coverage

Decision: Implemented and verified.
Evidence: `tests/unit/workflows/tasks/test_task_contract.py` rejects Skill payloads on Tool steps and shell-like executable fields.
Rationale: Backend contract validation remains the authoritative execution boundary.
Alternatives considered: UI-only validation rejected because submissions can be produced outside the browser.
Test implications: Unit.

## SC-005 / Traceability Coverage

Decision: Implemented and verified.
Evidence: `spec.md`, `plan.md`, `tasks.md`, and `verification.md` preserve MM-576 plus DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-019, and DESIGN-REQ-020.
Rationale: Jira-to-MoonSpec traceability is required for final verification and PR metadata.
Alternatives considered: Local artifact-only traceability rejected because feature artifacts must preserve the issue key.
Test implications: Artifact review.

## DESIGN-REQ-007 / Governed Tool Contracts

Decision: Implemented and verified.
Evidence: `frontend/src/entrypoints/task-create.tsx` displays Tool contract metadata and preserves Tool payload submission.
Rationale: The authoring UI must make schema-backed governed execution visible.
Alternatives considered: Hiding contract metadata rejected because MM-576 asks for governed typed operations.
Test implications: Frontend integration and artifact review.

## DESIGN-REQ-008 / Picker, Dynamic Options, and Validation

Decision: Implemented and verified.
Evidence: `frontend/src/entrypoints/task-create.tsx` implements grouping/search and trusted Jira dynamic options; focused UI tests cover both flows.
Rationale: Searchable tool selection and dynamic status options are central to the source design.
Alternatives considered: Static Jira statuses rejected because workflow options are issue-specific.
Test implications: Frontend integration.

## DESIGN-REQ-019 / Tool Terminology

Decision: Implemented and verified.
Evidence: `frontend/src/entrypoints/task-create.tsx` keeps Tool as the user-facing Step Type terminology in the governed Tool panel.
Rationale: The UI should not introduce Script as the Step Type concept.
Alternatives considered: Script-oriented copy rejected by source design.
Test implications: Frontend integration and artifact review.

## DESIGN-REQ-020 / No Arbitrary Shell Step

Decision: Implemented and verified.
Evidence: `tests/unit/workflows/tasks/test_task_contract.py` rejects shell-like executable fields, and the Create page Tool UI submits typed Tool payloads.
Rationale: Arbitrary shell execution remains out of scope unless modeled as an approved typed command tool.
Alternatives considered: Free-form shell fields rejected by the source design and contract tests.
Test implications: Unit plus frontend integration.
