# Tasks: Cursor CLI Phase 5 — Testing and Hardening

**Input**: Design documents from `/specs/092-cursor-cli-phase5/`
**Prerequisites**: plan.md (required), spec.md (required), research.md

---

## Phase 1: Setup

- [ ] T001 Create feature branch `092-cursor-cli-phase5` (already done)

---

## Phase 2: Implementation

- [ ] T002 [P] [US1] Add `detect_rate_limit()` to `ndjson_parser.py` — scan events for 429/rate-limit indicators, return detection result dict (DOC-REQ-P5-003)
- [ ] T003 [P] [US2] Create `moonmind/workflows/temporal/runtime/process_control.py` with `cancel_managed_process()` — SIGTERM → grace → SIGKILL (DOC-REQ-P5-004)
- [ ] T004 [P] [US3] Add `cursor_cli: "Cursor CLI"` to `TASK_RUNTIME_LABELS` in `dashboard.js` (DOC-REQ-P5-005)

---

## Phase 3: Tests

- [ ] T005 [P] [US1] Add rate-limit detection tests to `test_ndjson_parser.py` (DOC-REQ-P5-003)
- [ ] T006 [P] [US2] Create `tests/unit/services/temporal/runtime/test_process_control.py` (DOC-REQ-P5-004)

---

## Phase 4: Documentation

- [ ] T007 Document DOC-REQ-P5-001 as already covered by Phase 2 tests
- [ ] T008 Document DOC-REQ-P5-002 (Docker integration) as deferred

---

## Phase 5: Validation

- [ ] T009 Run `./tools/test_unit.sh` to verify all tests pass
