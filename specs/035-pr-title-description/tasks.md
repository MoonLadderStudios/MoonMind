# Tasks: Queue Publish PR Title and Description System

**Input**: Design documents from `/specs/032-pr-title-description/`
**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Tests**: Tests are required by spec success criteria and included for each user story.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm feature scaffolding and test targeting before runtime changes.

- [X] T001 Validate feature artifact set in specs/032-pr-title-description/ (plan.md, research.md, data-model.md, contracts/, quickstart.md)
- [X] T002 Identify existing publish-stage helper/test insertion points in moonmind/agents/codex_worker/worker.py and tests/unit/agents/codex_worker/test_worker.py

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Add shared publish-text resolver primitives that all stories depend on.

**⚠️ CRITICAL**: User-story implementation starts only after this phase.

- [X] T003 Add shared publish-text normalization helpers for non-empty override detection and sentence/line extraction in moonmind/agents/codex_worker/worker.py
- [X] T004 Add deterministic metadata-footer formatter helper for MoonMind job/runtime/base/head correlation in moonmind/agents/codex_worker/worker.py

**Checkpoint**: Foundational helpers in place; user-story logic can be implemented.

---

## Phase 3: User Story 1 - Producer PR Overrides Are Applied Verbatim (Priority: P1) 🎯 MVP

**Goal**: Preserve explicit producer-provided commit/title/body text without mutation.

**Independent Test**: Publish text resolver returns override values unchanged when non-empty inputs are provided.

### Tests for User Story 1

- [X] T005 [P] [US1] Add unit tests for override precedence and verbatim pass-through in tests/unit/agents/codex_worker/test_worker.py (`FR-001`, `DOC-REQ-001`, `DOC-REQ-003`)

### Implementation for User Story 1

- [X] T006 [US1] Wire commit/PR override precedence through canonical publish stage in moonmind/agents/codex_worker/worker.py (`FR-001`, `DOC-REQ-001`, `DOC-REQ-003`)

**Checkpoint**: Override precedence behavior is functional and independently testable.

---

## Phase 4: User Story 2 - Deterministic Defaults Are Generated from Task Intent (Priority: P1)

**Goal**: Derive readable deterministic title/body defaults when producer overrides are omitted.

**Independent Test**: With empty overrides, title follows documented fallback order and body includes generated summary + metadata footer.

### Tests for User Story 2

- [X] T007 [P] [US2] Add unit tests for title fallback order and UUID-title exclusion in tests/unit/agents/codex_worker/test_worker.py (`FR-002`, `DOC-REQ-004`, `DOC-REQ-005`)
- [X] T008 [P] [US2] Add unit tests for default body generation markers/keys in tests/unit/agents/codex_worker/test_worker.py (`FR-003`, `FR-004`, `DOC-REQ-006`, `DOC-REQ-007`)

### Implementation for User Story 2

- [X] T009 [US2] Implement default title derivation chain (step title -> instruction sentence/line -> fallback) in moonmind/agents/codex_worker/worker.py (`FR-002`, `DOC-REQ-004`, `DOC-REQ-005`)
- [X] T010 [US2] Implement default PR body summary + machine-parseable metadata footer generation in moonmind/agents/codex_worker/worker.py (`FR-003`, `FR-004`, `DOC-REQ-006`, `DOC-REQ-007`)

**Checkpoint**: Deterministic defaults are functional and independently testable.

---

## Phase 5: User Story 3 - Publish Correlation Metadata Is Machine Parseable (Priority: P2)

**Goal**: Enforce base/head/runtime/job correlation semantics in generated publish text.

**Independent Test**: Derived metadata and publish-stage PR args use base override semantics and effective working branch head.

### Tests for User Story 3

- [X] T011 [P] [US3] Add unit tests for base/head metadata and PR base override precedence in tests/unit/agents/codex_worker/test_worker.py (`FR-005`, `DOC-REQ-002`, `DOC-REQ-008`)

### Implementation for User Story 3

- [X] T012 [US3] Apply resolved base/head/runtime/job context to generated PR body and PR command args in moonmind/agents/codex_worker/worker.py (`FR-005`, `DOC-REQ-002`, `DOC-REQ-008`)

**Checkpoint**: Correlation metadata and PR branch semantics are independently testable.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final validation and completion checks across all stories.

- [X] T013 Run focused worker publish tests via ./tools/test_unit.sh tests/unit/agents/codex_worker/test_worker.py -k "publish or title or body"
- [X] T014 Run full required unit suite via ./tools/test_unit.sh (`FR-006`)
- [X] T015 Run runtime scope gate for tasks and diff via .specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime and .specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main (`FR-006`)
- [X] T016 Mark completed tasks as [X] in specs/032-pr-title-description/tasks.md and capture final verification notes

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: Starts immediately.
- **Phase 2 (Foundational)**: Depends on Phase 1.
- **User Stories (Phases 3-5)**: Depend on Phase 2 completion.
- **Phase 6 (Polish)**: Depends on all selected user stories.

### User Story Dependencies

- **US1 (P1)**: Independent once foundational helpers are in place.
- **US2 (P1)**: Depends on foundational helpers; independent of US1 business behavior.
- **US3 (P2)**: Depends on foundational helpers and generated metadata primitives from US2.

### Within Each User Story

- Tests should be authored before or alongside implementation and must fail/pass around the corresponding code change.
- Runtime helper changes in `worker.py` must precede final validation commands.

### Parallel Opportunities

- T005, T007, T008, and T011 are parallelizable test-authoring tasks.
- T009 and T010 can run in parallel once foundational helpers exist.

---

## Parallel Example: User Story 2

```bash
# Parallel test authoring
Task: "T007 Add unit tests for title fallback order"
Task: "T008 Add unit tests for default body generation"

# Parallel implementation slices after helper groundwork
Task: "T009 Implement title derivation chain"
Task: "T010 Implement default PR body generation"
```

---

## Implementation Strategy

### MVP First (US1)

1. Complete Phases 1-2.
2. Deliver US1 override precedence (T005-T006).
3. Validate with focused tests before moving to defaults.

### Incremental Delivery

1. Add deterministic defaults (US2).
2. Add branch/runtime correlation semantics (US3).
3. Run full validation and scope gates (Phase 6).

### Risk Controls

- Keep fallback logic deterministic and side-effect free.
- Avoid full UUID in derived title while preserving full UUID in metadata body.
- Ensure all `DOC-REQ-*` IDs are covered by both implementation and validation tasks.

## Notes

- Runtime code changes are explicitly scoped to `moonmind/agents/codex_worker/worker.py`.
- Validation coverage is explicitly scoped to `tests/unit/agents/codex_worker/test_worker.py` and `./tools/test_unit.sh` execution.
