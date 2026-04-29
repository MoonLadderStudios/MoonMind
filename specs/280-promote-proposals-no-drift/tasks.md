# Tasks: Promote Proposals Without Live Preset Drift

**Input**: `specs/280-promote-proposals-no-drift/spec.md`
**Plan**: `specs/280-promote-proposals-no-drift/plan.md`
**Unit command**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/task_proposals/test_service.py tests/unit/api/routers/test_task_proposals.py`
**Integration/API boundary command**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_task_proposals.py`
**Final command**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`

## Source Traceability

MM-560; FR-001 through FR-006; SC-001 through SC-004; DESIGN-REQ-014, DESIGN-REQ-018, DESIGN-REQ-019.

## Story Summary

Promotion must execute the reviewed flat proposal payload, preserve preset provenance, reject unresolved Preset steps, and reject full promotion-time task payload replacement.

## Tasks

- [X] T001 Add a unit regression test in `tests/unit/workflows/task_proposals/test_service.py` proving promotion rejects a stored unresolved `type: "preset"` step. (FR-001, SC-001, DESIGN-REQ-014)
- [X] T002 Add a unit regression test in `tests/unit/workflows/task_proposals/test_service.py` proving `runtime_mode_override` changes runtime while preserving stored steps and preset provenance. (FR-002, FR-003, FR-004, DESIGN-REQ-018)
- [X] T003 Update integration-style API router boundary tests in `tests/unit/api/routers/test_task_proposals.py` to reject `taskCreateRequestOverride` and to assert `runtimeMode` is passed as a bounded runtime override. (FR-004, FR-005, SC-002, SC-003, DESIGN-REQ-019)
- [X] T004 Run targeted tests and confirm the new tests fail before implementation. (TDD)
- [X] T005 Remove `taskCreateRequestOverride` from `moonmind/schemas/task_proposal_models.py` and update runtime mode description. (FR-005)
- [X] T006 Update `moonmind/workflows/task_proposals/service.py` so promotion accepts only a bounded `runtime_mode_override` and applies it to the stored validated payload. (FR-003, FR-004)
- [X] T007 Update `api_service/api/routers/task_proposals.py` so the router no longer builds a full override envelope for runtime selection. (FR-004, FR-005)
- [X] T008 Update generated OpenAPI TypeScript in `frontend/src/generated/openapi.ts` to remove `taskCreateRequestOverride` from the promote request model. (FR-005)
- [X] T009 Run targeted tests and fix any failures. (SC-001, SC-002, SC-003)
- [X] T010 Run story validation and traceability check for `MM-560`, `DESIGN-REQ-014`, `DESIGN-REQ-018`, and `DESIGN-REQ-019`. (SC-004)
- [X] T011 Run final `/moonspec-verify` work by producing `verification.md`, then run final unit verification or record the exact blocker. (Final verification)
