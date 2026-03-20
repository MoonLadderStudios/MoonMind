# Tasks: Cursor CLI Phase 3 — Auth Profile Support

**Input**: Design documents from `/specs/090-cursor-cli-phase3/`
**Prerequisites**: plan.md (required), spec.md (required), research.md

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

- [ ] T001 Create feature branch `090-cursor-cli-phase3` (already done)

---

## Phase 2: Implementation

- [ ] T002 [US1] Create Alembic migration `seed_cursor_cli_auth_profile` in `api_service/migrations/versions/` — INSERT default `cursor_cli` auth profile row with `profile_id=cursor-cli-default`, `auth_mode=api_key`, `max_parallel_runs=1`, `rate_limit_policy=backoff` (DOC-REQ-P3-001)
- [ ] T003 [US2] Document that no code changes needed for `auth_profile.ensure_manager` — runtime_id agnostic (DOC-REQ-P3-002)
- [ ] T004 Document that `cursor_auth_volume` was already added in Phase 1 (DOC-REQ-P3-003)

---

## Phase 3: Validation

- [ ] T005 Run `./tools/test_unit.sh` to verify no regression
- [ ] T006 Verify migration file structure and SQL validity

---

## Dependencies

T002 is the only implementation task. T003–T004 are documentation-only. T005–T006 depend on T002.
