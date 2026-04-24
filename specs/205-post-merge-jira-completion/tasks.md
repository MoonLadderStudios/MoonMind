# Tasks: Post-Merge Jira Completion

**Input**: Design documents from `/specs/205-post-merge-jira-completion/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/post-merge-jira-completion.md`, `quickstart.md`

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: This task list covers exactly one user story: `Complete Jira After Merge`.

**Source Traceability**: Original Jira issue `MM-403` and the original Jira preset brief are preserved in `spec.md` and `spec.md` (Input). The plan marks 16 requirements missing, 16 partial, 1 implemented-unverified, and 0 implemented-verified, so all requirement rows need test or implementation coverage here.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_merge_gate_models.py tests/unit/workflows/temporal/test_post_merge_jira_completion.py tests/unit/integrations/test_jira_tool_service.py`
- Integration tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/workflows/test_merge_automation_temporal.py tests/unit/workflows/temporal/workflows/test_run_parent_owned_merge_automation_boundary.py`
- Full unit verification: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- Optional hermetic integration verification when Docker is available: `./tools/test_integration.sh`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel when the task touches different files and does not depend on incomplete tasks.
- Include exact file paths in task descriptions.
- Include requirement, scenario, success criterion, or source IDs when the task implements or validates behavior.
- Do not add story labels; this file covers one story only.

## Path Conventions

- Backend workflow code lives under `moonmind/`.
- Unit tests live under `tests/unit/`.
- Workflow-boundary integration-style tests for this story live under `tests/unit/workflows/temporal/workflows/`.
- Canonical docs live under `docs/`; volatile orchestration input remains under `local-only handoffs`.

---

## Phase 1: Setup

**Purpose**: Confirm the active feature context and existing code/test surfaces before red-first work starts.

- [X] T001 Confirm `.specify/feature.json` points at `specs/205-post-merge-jira-completion` and record any mismatch in `specs/205-post-merge-jira-completion/tasks.md` before continuing (FR-017, DESIGN-REQ-010)
- [X] T002 [P] Inspect existing merge automation model fields in `moonmind/schemas/temporal_models.py` before adding post-merge Jira config tests (FR-004, FR-009)
- [X] T003 [P] Inspect existing merge automation terminal disposition handling in `moonmind/workflows/temporal/workflows/merge_automation.py` before adding workflow tests (FR-001, FR-002, FR-003)
- [X] T004 [P] Inspect existing trusted Jira service issue and transition methods in `moonmind/integrations/jira/tool.py` before adding service-boundary tests (FR-006, FR-009, FR-014)
- [X] T005 [P] Inspect existing parent merge automation summary propagation in `moonmind/workflows/temporal/workflows/run.py` before adding parent-boundary tests (FR-013)

---

## Phase 2: Foundational

**Purpose**: Add only blocking test scaffolding and contracts needed before story implementation begins.

**Checkpoint**: No production implementation work starts until Phase 2 and the red-first test tasks in Phase 3 are complete.

- [X] T006 Create reusable stub data for Jira issues, transitions, and completion evidence in `tests/unit/workflows/temporal/test_post_merge_jira_completion.py` covering FR-006, FR-008, FR-009, and FR-010
- [X] T007 [P] Add workflow test stub helpers for post-merge Jira activity responses in `tests/unit/workflows/temporal/workflows/test_merge_automation_temporal.py` covering SCN-001 through SCN-006
- [X] T008 [P] Add parent-boundary test fixtures for merge automation results containing `postMergeJira` evidence in `tests/unit/workflows/temporal/workflows/test_run_parent_owned_merge_automation_boundary.py` covering FR-013
- [X] T009 [P] Add trusted Jira service fake responses for expanded transition fields in `tests/unit/integrations/test_jira_tool_service.py` covering FR-009 and FR-010

---

## Phase 3: Story - Complete Jira After Merge

**Summary**: As a Jira-backed workflow operator, I want MoonMind to complete the correct Jira issue only after merge automation verifies merge success so that issue status reflects delivered work without premature or ambiguous transitions.

**Independent Test**: Run merge automation with trusted Jira stubs for `merged`, `already_merged`, already-done, missing issue key, ambiguous issue keys, unavailable done transition, required transition fields, and retry/replay, then verify workflow outcome, Jira transition decision, and operator-visible artifacts.

**Traceability**: FR-001 through FR-017, SCN-001 through SCN-006, SC-001 through SC-009, DESIGN-REQ-001 through DESIGN-REQ-010.

**Unit Test Plan**: Cover Pydantic config validation, issue-key candidate extraction and precedence, trusted candidate validation, ambiguity detection, transition selection, already-done no-op behavior, required-field failure, idempotent decision shape, evidence compactness, and trusted Jira boundary behavior.

**Integration Test Plan**: Cover merge automation workflow success and failure paths, required post-merge completion before terminal success, parent result propagation, no duplicate transitions on retry/replay, and operator-visible summary/artifact refs.

### Unit Tests

**Write these tests first. Run them and confirm they fail for the expected missing `postMergeJira`, resolver, selector, activity, or evidence behavior before implementing production code.**

- [X] T010 [P] Add failing model tests for `postMergeJira` config defaults, unsupported strategy failure, compact JSON fields, and missing-config compatibility in `tests/unit/workflows/temporal/test_merge_gate_models.py` covering FR-004, FR-009, FR-010, and DESIGN-REQ-004
- [X] T011 [P] Add failing issue-resolution tests for candidate source precedence, same-key deduplication, missing candidates, invalid candidates, and conflicting validated candidates in `tests/unit/workflows/temporal/test_post_merge_jira_completion.py` covering FR-005, FR-006, FR-007, FR-015, DESIGN-REQ-003, and DESIGN-REQ-008
- [X] T012 [P] Add failing transition-selector tests for already-done no-op, explicit transition ID validation, explicit transition name validation, exactly-one done-category selection, zero done transitions, multiple done transitions, and missing required fields in `tests/unit/workflows/temporal/test_post_merge_jira_completion.py` covering FR-008, FR-009, FR-010, SC-003, SC-005, and DESIGN-REQ-004
- [X] T013 [P] Add failing completion-decision tests for `succeeded`, `noop_already_done`, `skipped`, `blocked`, and `failed` evidence without raw credentials in `tests/unit/workflows/temporal/test_post_merge_jira_completion.py` covering FR-011, FR-012, FR-014, SC-007, and SC-008
- [X] T014 [P] Add failing trusted Jira service tests for transition field expansion, stale explicit transition rejection, and required-field failure evidence in `tests/unit/integrations/test_jira_tool_service.py` covering FR-006, FR-009, FR-010, and DESIGN-REQ-007

### Integration Tests

- [X] T015 [P] Add failing workflow-boundary test proving resolver disposition `merged` runs required post-merge Jira completion before terminal success in `tests/unit/workflows/temporal/workflows/test_merge_automation_temporal.py` covering FR-001, FR-002, SCN-001, SC-001, DESIGN-REQ-001, and DESIGN-REQ-002
- [X] T016 [P] Add failing workflow-boundary test proving resolver disposition `already_merged` runs the same post-merge Jira completion path before terminal success in `tests/unit/workflows/temporal/workflows/test_merge_automation_temporal.py` covering FR-002, SCN-002, and SC-002
- [X] T017 [P] Add failing workflow-boundary test proving an already-done Jira issue returns `noop_already_done` and terminal success without another transition in `tests/unit/workflows/temporal/workflows/test_merge_automation_temporal.py` covering FR-008, FR-011, SCN-003, SC-003, and SC-006
- [X] T018 [P] Add failing workflow-boundary tests proving missing, invalid, or ambiguous issue keys block or fail without Jira mutation in `tests/unit/workflows/temporal/workflows/test_merge_automation_temporal.py` covering FR-005, FR-006, FR-007, SCN-004, SC-004, and DESIGN-REQ-003
- [X] T019 [P] Add failing workflow-boundary tests proving zero, multiple, or field-incomplete done transitions block or fail without Jira mutation in `tests/unit/workflows/temporal/workflows/test_merge_automation_temporal.py` covering FR-009, FR-010, SCN-005, SC-005, and DESIGN-REQ-004
- [X] T020 [P] Add failing workflow-boundary test proving replay, retry, or duplicate completion evaluation does not emit duplicate Jira transitions in `tests/unit/workflows/temporal/workflows/test_merge_automation_temporal.py` covering FR-011, SCN-006, SC-006, and DESIGN-REQ-005
- [X] T021 [P] Add failing parent-boundary tests proving post-merge Jira success, no-op, blocked, failed, and skipped summaries propagate through `MoonMind.Run` results in `tests/unit/workflows/temporal/workflows/test_run_parent_owned_merge_automation_boundary.py` covering FR-013 and DESIGN-REQ-006

### Red-First Confirmation

- [X] T022 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_merge_gate_models.py tests/unit/workflows/temporal/test_post_merge_jira_completion.py tests/unit/integrations/test_jira_tool_service.py` and confirm T010 through T014 fail for the expected missing production behavior
- [X] T023 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/workflows/test_merge_automation_temporal.py tests/unit/workflows/temporal/workflows/test_run_parent_owned_merge_automation_boundary.py` and confirm T015 through T021 fail for the expected missing workflow behavior

### Conditional Verification-Only Work

- [X] T024 Verify existing merge automation does not invoke post-merge Jira completion for waiting, remediation, manual-review, blocked, failed, or unresolved dispositions in `tests/unit/workflows/temporal/workflows/test_merge_automation_temporal.py` covering implemented-unverified FR-003
- [X] T025 If T024 fails, implement the missing success-disposition guard in `moonmind/workflows/temporal/workflows/merge_automation.py` so completion runs only for `merged` and `already_merged` covering FR-003

### Implementation

- [X] T026 Add `MergeAutomationPostMergeJiraModel` and compact post-merge decision/result models to `moonmind/schemas/temporal_models.py` covering FR-004, FR-009, FR-010, FR-011, and `contracts/post-merge-jira-completion.md`
- [X] T027 Add deterministic Jira issue candidate, resolution, transition selection, already-done, and evidence helpers in `moonmind/workflows/temporal/post_merge_jira_completion.py` covering FR-005 through FR-012 and FR-015
- [X] T028 Update trusted Jira transition retrieval and required-field handling in `moonmind/integrations/jira/tool.py` covering FR-006, FR-009, FR-010, and FR-014
- [X] T029 Update Jira integration request or response models in `moonmind/integrations/jira/models.py` for transition target status category and required field metadata covering FR-009 and FR-010
- [X] T030 Add an activity-bound post-merge Jira completion entry point in `moonmind/workflows/temporal/activity_runtime.py` that uses `JiraToolService.get_issue`, `JiraToolService.get_transitions`, and `JiraToolService.transition_issue` covering FR-006, FR-012, FR-014, DESIGN-REQ-007, and SC-008
- [X] T031 Register the post-merge Jira completion activity definition in `moonmind/workflows/temporal/activity_catalog.py` and activity binding in `moonmind/workflows/temporal/activity_runtime.py` covering DESIGN-REQ-002 and DESIGN-REQ-007
- [X] T032 Wire `MoonMind.MergeAutomation` to invoke post-merge Jira completion after resolver dispositions `merged` and `already_merged`, before terminal success, in `moonmind/workflows/temporal/workflows/merge_automation.py` covering FR-001, FR-002, FR-003, SCN-001, SCN-002, and DESIGN-REQ-001
- [X] T033 Add blocked and failed post-merge completion handling to `moonmind/workflows/temporal/workflows/merge_automation.py` so required completion prevents terminal success and preserves operator-visible evidence covering FR-007, FR-010, FR-012, FR-013, SCN-004, and SCN-005
- [X] T034 Add idempotent workflow state and artifact references for post-merge Jira resolution and transition evidence in `moonmind/workflows/temporal/workflows/merge_automation.py` covering FR-011, FR-012, SCN-006, SC-006, and SC-007
- [X] T035 Update parent `MoonMind.Run` merge automation result handling in `moonmind/workflows/temporal/workflows/run.py` so `postMergeJira` status, no-op, skipped, blocked, failed, and artifact refs are preserved in summaries covering FR-013 and DESIGN-REQ-006
- [X] T036 Update canonical merge automation docs in `docs/Tasks/PrMergeAutomation.md` to describe post-merge Jira ownership, success timing, blocked behavior, and idempotency covering FR-001, FR-002, FR-011, and DESIGN-REQ-001
- [X] T037 [P] Update canonical Jira integration docs in `docs/Tools/JiraIntegration.md` to describe trusted post-merge issue fetch, transition lookup, transition mutation, and forbidden raw-credential paths covering FR-006, FR-014, and DESIGN-REQ-007
- [X] T038 [P] Update task publishing or task-origin docs in `docs/Tasks/TaskPublishing.md` with authoritative Jira key preservation expectations for Jira-backed runs covering FR-004, FR-005, and DESIGN-REQ-003

### Story Validation

- [X] T039 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_merge_gate_models.py tests/unit/workflows/temporal/test_post_merge_jira_completion.py tests/unit/workflows/temporal/workflows/test_merge_automation_temporal.py tests/unit/workflows/temporal/workflows/test_run_parent_owned_merge_automation_boundary.py tests/unit/integrations/test_jira_tool_service.py` and fix failures until the story passes independently covering FR-016 and DESIGN-REQ-009
- [X] T040 Run `rg -n "MM-403|postMergeJira|Post-Merge Jira Completion" specs/205-post-merge-jira-completion docs/Tasks/PrMergeAutomation.md docs/Tools/JiraIntegration.md docs/Tasks/TaskPublishing.md` and confirm traceability evidence exists for FR-017, SC-009, and DESIGN-REQ-010

**Checkpoint**: The story is fully functional, covered by unit and workflow-boundary integration tests, and independently testable.

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: Verify the completed story against repository standards, security guardrails, and MoonSpec artifacts.

- [X] T041 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` for full unit verification after targeted tests pass
- [X] T042 Run `./tools/test_integration.sh` when Docker is available, or record Docker unavailability in `specs/205-post-merge-jira-completion/quickstart.md` verification notes if the managed runtime has no Docker socket
- [X] T043 Run `rg -n --glob '!specs/205-post-merge-jira-completion/tasks.md' "ghp_|github_pat_|AIza|ATATT|AKIA|BEGIN (RSA|OPENSSH|PRIVATE) KEY|token=|password=" moonmind tests specs/205-post-merge-jira-completion docs/Tasks/PrMergeAutomation.md docs/Tools/JiraIntegration.md docs/Tasks/TaskPublishing.md` and remove any secret-like content before publishing
- [X] T044 Review `git diff -- AGENTS.md specs/205-post-merge-jira-completion moonmind tests docs/Tasks/PrMergeAutomation.md docs/Tools/JiraIntegration.md docs/Tasks/TaskPublishing.md` to confirm the implementation matches MM-403 and does not include unrelated changes
- [X] T045 Run `/moonspec-verify` for `specs/205-post-merge-jira-completion` and preserve PASS/PARTIAL/FAIL evidence for MM-403 final verification

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Phase 1 and blocks story implementation.
- **Story (Phase 3)**: Depends on Phase 2. Unit tests, integration tests, and red-first confirmations must complete before production implementation.
- **Polish (Phase 4)**: Depends on the story being functionally complete and targeted tests passing.

### Within The Story

- T010 through T014 must be written before T022.
- T015 through T021 must be written before T023.
- T022 and T023 must confirm red-first failures before T026 through T038.
- T024 verifies the implemented-unverified FR-003 row before T025; skip T025 only when T024 passes.
- T026 model work precedes helper, activity, and workflow wiring.
- T027 helper work precedes T030 activity wiring and T032 workflow invocation.
- T030 and T031 activity wiring precede T032 through T034 workflow behavior.
- T035 parent summary work follows T032 through T034 result shape work.
- T036 through T038 docs follow the production contract shape from T026 through T035.
- T039 and T040 validate the complete story before Phase 4.

### Parallel Opportunities

- T002 through T005 can run in parallel.
- T007 through T009 can run in parallel after T006.
- T010, T011, T014, T015, and T021 can run in parallel because they touch different test files.
- T036, T037, and T038 can run in parallel after implementation behavior is stable.

---

## Parallel Example: Story Phase

```bash
# Launch test authoring in separate files:
Task: "Add failing model tests in tests/unit/workflows/temporal/test_merge_gate_models.py"
Task: "Add failing trusted Jira service tests in tests/unit/integrations/test_jira_tool_service.py"
Task: "Add failing parent-boundary tests in tests/unit/workflows/temporal/workflows/test_run_parent_owned_merge_automation_boundary.py"

# Launch documentation updates after contract shape is stable:
Task: "Update docs/Tasks/PrMergeAutomation.md"
Task: "Update docs/Tools/JiraIntegration.md"
Task: "Update docs/Tasks/TaskPublishing.md"
```

---

## Implementation Strategy

1. Complete setup and foundational test scaffolding.
2. Write red-first unit tests for model, resolver, selector, trusted Jira service, idempotency, and evidence requirements.
3. Write red-first workflow-boundary tests for merge success, already-merged, already-done, missing or ambiguous issue keys, unsafe transitions, duplicate evaluation, and parent result propagation.
4. Confirm the tests fail for the expected missing behavior.
5. Implement compact models, deterministic helpers, trusted activity/service wiring, merge automation orchestration, parent summary propagation, and canonical docs.
6. Run targeted story validation, full unit verification, optional hermetic integration verification, secret scan, diff review, and `/moonspec-verify`.

---

## Notes

- This file covers one story only and must not be expanded into general multi-issue Jira completion.
- Jira completion must remain inside trusted backend activity/service boundaries, not resolver shell scripts.
- Fuzzy Jira summary search and default multi-issue completion are out of scope for MM-403.
- Missing `postMergeJira` input must preserve existing merge automation invocation compatibility.
- `MM-403` must remain visible in MoonSpec artifacts, verification output, commit text, and pull request metadata.
