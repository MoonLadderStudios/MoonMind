# Tasks: Claude Context Snapshots

**Input**: Design documents from `specs/186-claude-context-snapshots/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped by phase around the single MM-345 / STORY-004 user story so the work stays focused, traceable, and independently testable.

**Source Traceability**: Tasks reference FR-001 through FR-014, acceptance scenarios 1-7, SC-001 through SC-006, and DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-020, DESIGN-REQ-021, and DESIGN-REQ-028 from `spec.md`.

**Test Commands**:

- Unit tests: `pytest tests/unit/schemas/test_claude_context_snapshots.py -q`
- Integration tests: `pytest tests/integration/schemas/test_claude_context_snapshots_boundary.py -q`
- Full unit verification: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- Hermetic integration verification: `./tools/test_integration.sh`
- Final verification: `/speckit.verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm active artifacts and existing schema/test structure.

- [X] T001 Confirm active feature artifacts for MM-345 in specs/186-claude-context-snapshots/spec.md, specs/186-claude-context-snapshots/plan.md, specs/186-claude-context-snapshots/research.md, specs/186-claude-context-snapshots/data-model.md, and specs/186-claude-context-snapshots/contracts/claude-context-snapshots.md
- [X] T002 Confirm existing Claude schema exports and test locations in moonmind/schemas/managed_session_models.py, moonmind/schemas/__init__.py, tests/unit/schemas/, and tests/integration/schemas/

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Identify existing validation helpers and fixture patterns before story work begins.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [X] T003 Review compact metadata validation helpers used by existing Claude policy and decision records in moonmind/schemas/managed_session_models.py for FR-009 and FR-010
- [X] T004 Review existing Claude schema unit and integration-style fixture patterns in tests/unit/schemas/test_claude_managed_session_models.py, tests/unit/schemas/test_claude_policy_envelope.py, and tests/integration/schemas/test_claude_decision_pipeline_boundary.py

**Checkpoint**: Foundation ready - story test and implementation work can now begin.

---

## Phase 3: Story - Claude Context Snapshots

**Summary**: As an operator investigating session quality, I want typed Claude context snapshot metadata, reload policy, and compaction epochs so that I can inspect what context entered a session and what survives compaction.

**Independent Test**: Bootstrap a Claude session with managed/project/local CLAUDE files, MCP manifests, skills, hooks, file reads, nested rules, and invoked skill bodies; compact it; then assert the original snapshot remains immutable and the new epoch reloads only allowed context with documented reinjection policies.

**Traceability**: FR-001 through FR-014; acceptance scenarios 1-7; SC-001 through SC-006; DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-020, DESIGN-REQ-021, DESIGN-REQ-028.

**Test Plan**:

- Unit: source-kind validation, load timing validation, reinjection policy defaults, guidance-versus-enforcement guardrails, compact metadata limits, and compaction immutability.
- Integration: representative startup, on-demand, and compaction boundary flow with snapshot, work item, and normalized events.

### Unit Tests (write first)

- [X] T005 [P] Add failing unit tests for all documented startup and on-demand context source kinds plus exported kind tuples in tests/unit/schemas/test_claude_context_snapshots.py covering FR-003, FR-004, FR-005, SC-001, DESIGN-REQ-013, and DESIGN-REQ-014
- [X] T006 [P] Add failing unit tests for required explicit reinjection policies and default policy mapping in tests/unit/schemas/test_claude_context_snapshots.py covering FR-002, FR-006, SC-002, and DESIGN-REQ-014
- [X] T007 [P] Add failing unit tests for guidance classification and rejection of memory or CLAUDE guidance as enforcement in tests/unit/schemas/test_claude_context_snapshots.py covering FR-011, FR-012, SC-005, and DESIGN-REQ-021
- [X] T008 [P] Add failing unit tests for compact metadata rejection of large payloads in tests/unit/schemas/test_claude_context_snapshots.py covering FR-009, FR-010, and DESIGN-REQ-020
- [X] T009 [P] Add failing unit tests for compaction creating a new immutable epoch and retaining only allowed segments in tests/unit/schemas/test_claude_context_snapshots.py covering FR-007, FR-008, SC-003, DESIGN-REQ-015, and DESIGN-REQ-028
- [X] T010 Run `pytest tests/unit/schemas/test_claude_context_snapshots.py -q` and confirm T005-T009 fail for missing context snapshot contracts or expected validation behavior before implementation

### Integration Tests (write first)

- [X] T011 [P] Add failing integration-style boundary test for representative startup, on-demand, compaction work item, and normalized events in tests/integration/schemas/test_claude_context_snapshots_boundary.py covering acceptance scenarios 1-7, FR-001 through FR-014, SC-004, and DESIGN-REQ-013 through DESIGN-REQ-028
- [X] T012 Run `pytest tests/integration/schemas/test_claude_context_snapshots_boundary.py -q` and confirm T011 fails for missing context snapshot contracts or expected boundary behavior before implementation

### Implementation

- [X] T013 Add Claude context source, load timing, reinjection, guidance role, and event literal contracts plus exported documented tuples in moonmind/schemas/managed_session_models.py covering FR-003, FR-004, FR-005, FR-006, and DESIGN-REQ-013 through DESIGN-REQ-014
- [X] T014 Add ClaudeContextSegment validation in moonmind/schemas/managed_session_models.py covering FR-002, FR-009, FR-010, FR-011, FR-012, DESIGN-REQ-020, and DESIGN-REQ-021
- [X] T015 Add ClaudeContextSnapshot and ClaudeContextEvent contracts in moonmind/schemas/managed_session_models.py covering FR-001, FR-007, FR-009, FR-014, DESIGN-REQ-013, DESIGN-REQ-015, DESIGN-REQ-020, and DESIGN-REQ-028
- [X] T016 Add claude_default_reinjection_policy and compact_claude_context_snapshot helper behavior in moonmind/schemas/managed_session_models.py covering FR-006, FR-007, FR-008, FR-013, FR-014, DESIGN-REQ-014, DESIGN-REQ-015, and DESIGN-REQ-028
- [X] T017 Export the new Claude context contracts from moonmind/schemas/managed_session_models.py and moonmind/schemas/__init__.py covering the public schema contract in specs/186-claude-context-snapshots/contracts/claude-context-snapshots.md
- [X] T018 Run `pytest tests/unit/schemas/test_claude_context_snapshots.py tests/integration/schemas/test_claude_context_snapshots_boundary.py -q`, fix failures, and verify the MM-345 story passes focused unit and integration-style checks

**Checkpoint**: The story is fully functional, covered by unit and integration tests, and testable independently.

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: Strengthen the completed story without changing its core scope.

- [X] T019 [P] Review specs/186-claude-context-snapshots/quickstart.md against implemented commands and update only if command evidence or blockers changed
- [X] T020 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` for final required unit verification and record the result in this task list
- [X] T021 Run `./tools/test_integration.sh` when Docker is available, or record the exact Docker/socket blocker in this task list
- [X] T022 Run `/speckit.verify` equivalent read-only verification against specs/186-claude-context-snapshots/spec.md after implementation and tests pass

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup completion and blocks story work.
- **Story (Phase 3)**: Depends on Foundational completion.
- **Polish (Phase 4)**: Depends on focused story tests passing.

### Within The Story

- Unit tests T005-T009 must be written before implementation tasks T013-T017.
- Integration test T011 must be written before implementation tasks T013-T017.
- Red-first confirmation tasks T010 and T012 must complete before production code tasks.
- Literal contracts and tuples T013 precede model/helper implementation T014-T016.
- Exports T017 follow model/helper implementation.
- Focused validation T018 follows implementation and exports.

### Parallel Opportunities

- T005-T009 can be authored in parallel within the same test file only if edits are coordinated carefully; otherwise keep them sequential to avoid conflicts.
- T011 can be authored in parallel with unit test tasks because it touches a different file.
- T019 can run in parallel with final verification preparation after focused tests pass.

---

## Parallel Example: Story Phase

```bash
# Different files, safe to parallelize if multiple agents are coordinating:
Task: "Add failing unit tests in tests/unit/schemas/test_claude_context_snapshots.py"
Task: "Add failing integration boundary test in tests/integration/schemas/test_claude_context_snapshots_boundary.py"
```

---

## Implementation Strategy

### Test-Driven Story Delivery

1. Complete Phase 1 and Phase 2 context checks.
2. Write unit tests and integration-style boundary tests first.
3. Run focused tests and confirm they fail for missing contracts or expected behavior.
4. Implement compact Pydantic contracts and deterministic helpers in the existing schema boundary.
5. Export the new contract surface.
6. Run focused tests until they pass.
7. Run full unit verification and hermetic integration verification when available.
8. Run final `/speckit.verify` equivalent against the MM-345 spec.

---

## Notes

- This task list covers exactly one story: MM-345 / STORY-004.
- No live Claude provider calls, persistent storage, checkpoint restore APIs, or memory authoring UX are in scope.
- Context payloads must remain pointer-based and compact by default.
- Verification evidence: focused tests passed with `pytest tests/unit/schemas/test_claude_context_snapshots.py tests/integration/schemas/test_claude_context_snapshots_boundary.py -q` (26 passed); full unit verification passed with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` (3312 Python tests passed, 1 xpassed, 16 subtests passed; 222 frontend tests passed); `./tools/test_integration.sh` was blocked by missing Docker socket at `/var/run/docker.sock`.
