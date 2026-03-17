# Tasks: Generic External Agent Adapter Pattern

**Input**: Design documents from `/specs/082-external-adapter-pattern/`
**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, contracts/

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup

**Purpose**: Preparation — no code changes, just verification of existing state.

- [ ] T001 Verify existing unit tests pass via `./tools/test_unit.sh tests/unit/workflows/adapters/`

---

## Phase 2: Foundational (Capability-Aware Base Class)

**Purpose**: Enhance `BaseExternalAgentAdapter` with capability-aware features — BLOCKS User Stories 2 and 3.

- [ ] T002 [US1] Add capability-aware `poll_hint_seconds` auto-population in `start()` method in `moonmind/workflows/adapters/base_external_agent_adapter.py` (DOC-REQ-010, FR-008). After calling `do_start`, if the returned handle's `poll_hint_seconds` is `None`, set it from `self.provider_capability.default_poll_hint_seconds`.
- [ ] T003 [US1] Add capability-aware cancel fallback in `cancel()` method in `moonmind/workflows/adapters/base_external_agent_adapter.py` (DOC-REQ-006, FR-006). Before calling `do_cancel`, check `self.provider_capability.supports_cancel`. If `False`, return `build_status(status="intervention_requested", extra_metadata={"cancelAccepted": False, "unsupported": True})` without invoking the provider.
- [ ] T004 [US1] Export `BaseExternalAgentAdapter` from `moonmind/workflows/adapters/__init__.py` (DOC-REQ-012, FR-010).
- [ ] T005 [US1] Add unit test `test_start_populates_poll_hint_from_capability` in `tests/unit/workflows/adapters/test_base_external_agent_adapter.py` — verify that `start()` sets `poll_hint_seconds` from capability descriptor.
- [ ] T006 [US1] Add unit test `test_cancel_returns_fallback_when_unsupported` in `tests/unit/workflows/adapters/test_base_external_agent_adapter.py` — verify that `cancel()` returns fallback status when `supportsCancel=False`, and `do_cancel` is NOT called.
- [ ] T007 [US1] Run `./tools/test_unit.sh tests/unit/workflows/adapters/test_base_external_agent_adapter.py` — all tests pass.

**Checkpoint**: Base class is capability-aware. `poll_hint_seconds` auto-populated, cancel has fallback for unsupported providers.

---

## Phase 3: User Story 2 — Capability-Aware Workflow Polling (Priority: P2)

**Goal**: `poll_hint_seconds` flows from capability descriptor through `AgentRunHandle` to the workflow polling loop.

**Independent Test**: Verify `build_handle` output includes `poll_hint_seconds` matching the capability descriptor.

### Implementation for User Story 2

- [ ] T008 [US2] Verify that `AgentRunHandle` returned by `start()` has `poll_hint_seconds` populated by running `./tools/test_unit.sh tests/unit/workflows/adapters/test_base_external_agent_adapter.py::test_start_populates_poll_hint_from_capability` (already created in T005).

**Checkpoint**: Capability-driven polling is proven end-to-end from adapter to workflow.

---

## Phase 4: User Story 3 — Codex Cloud Temporal Activity Integration (Priority: P2)

**Goal**: Wire Codex Cloud adapter into Temporal activities so `MoonMind.AgentRun` can route to Codex Cloud.

**Independent Test**: Unit tests for all 4 Codex Cloud activities.

### Implementation for User Story 3

- [ ] T009 [P] [US3] Create `moonmind/workflows/temporal/activities/codex_cloud_activities.py` with 4 activity definitions: `integration.codex_cloud.start`, `.status`, `.fetch_result`, `.cancel` following the pattern of `jules_activities.py` (DOC-REQ-011, FR-011).
- [ ] T010 [US3] Register Codex Cloud activities in `moonmind/workflows/temporal/activity_catalog.py` on the `mm.activity.integrations` task queue (DOC-REQ-011, FR-011).
- [ ] T011 [P] [US3] Create unit tests in `tests/unit/workflows/adapters/test_codex_cloud_activities.py` — verify each activity calls the correct adapter method and returns the expected model (DOC-REQ-011, FR-011).
- [ ] T012 [US3] Run `./tools/test_unit.sh tests/unit/workflows/adapters/test_codex_cloud_activities.py` — all tests pass.

**Checkpoint**: Codex Cloud is fully wired into the Temporal activity layer.

---

## Phase 5: User Story 4 — Developer Documentation (Priority: P3)

**Goal**: Step-by-step developer guide for adding a new external agent provider.

- [ ] T013 [US4] Create `docs/ExternalAgents/AddingExternalProvider.md` covering: settings, client, adapter subclass (extending `BaseExternalAgentAdapter`), registry registration, Temporal activities, activity catalog, and testing (DOC-REQ-013, FR-012).

---

## Phase 6: Polish & Cross-Cutting Concerns

- [ ] T014 [P] Update `docs/ExternalAgents/ExternalAgentIntegrationSystem.md` to mark Phases B and C as completed, Phase E as proven by Codex Cloud (DOC-REQ-013).
- [ ] T015 Run full test suite `./tools/test_unit.sh tests/unit/workflows/adapters/` — all tests pass including new and existing.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately.
- **Phase 2 (Foundational)**: Depends on Phase 1.
- **Phase 3 (US2)**: Depends on Phase 2 (T005 created in Phase 2).
- **Phase 4 (US3)**: Depends on Phase 1 only (Codex Cloud activities are independent of base class changes).
- **Phase 5 (US4)**: Depends on Phase 2 and Phase 4 (documentation references both).
- **Phase 6 (Polish)**: Depends on all prior phases.

### Parallel Opportunities

- T009, T011 can run in parallel with each other.
- T014 can run in parallel with T015.
- Phase 2 and Phase 4 can run in parallel (different files, no dependencies).

---

## Implementation Strategy

### MVP First (User Story 1 — Base Class Enhancements)

1. Complete Phase 1: Verify existing tests pass.
2. Complete Phase 2: Capability-aware base class.
3. **STOP and VALIDATE**: All existing + new tests pass.

### Incremental Delivery

1. Phase 2: Base class enhancements → Test independently
2. Phase 4: Codex Cloud activities → Test independently
3. Phase 5: Developer guide → Review
4. Phase 6: Polish → Final validation

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- All DOC-REQ-* IDs appear in at least one implementation task and one validation task
- Commit after each phase completes
