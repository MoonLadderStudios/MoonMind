# Requirements Traceability Matrix: Manifest Task System Phase 1 (Worker Readiness)

**Feature**: `specs/030-manifest-phase1`  
**Updated**: March 2, 2026

| DOC-REQ | FR Mapping | Implementation Tasks | Validation Tasks | Implementation Surface | Validation Strategy |
|---|---|---|---|---|---|
| DOC-REQ-001 (§11.2 profile secret resolution) | FR-001, FR-002, FR-003, FR-004 | T002, T004, T010 | T005, T011, T012, T013 | `api_service/api/routers/agent_queue.py` manifest secret resolution endpoint + worker/auth/job-state guards | Execute `tests/unit/api/routers/test_agent_queue.py` cases that assert capability, ownership, running-state enforcement, malformed payload safety, and profile value resolution. |
| DOC-REQ-002 (§11.2 vault passthrough) | FR-001, FR-004 | T001, T002, T004 | T005, T012, T013 | `moonmind/schemas/agent_queue_models.py`, `api_service/api/routers/agent_queue.py` vault reference extraction + pass-through response shaping | Execute `tests/unit/api/routers/test_agent_queue.py` cases that validate vault reference metadata is returned without server-side secret materialization. |
| DOC-REQ-003 (§8.11 checkpoint persistence) | FR-005, FR-006 | T001, T003, T006, T007 | T008, T009, T012, T013 | `api_service/api/schemas.py`, `api_service/services/manifests_service.py`, `api_service/api/routers/manifests.py` | Execute `tests/unit/services/test_manifests_service.py` and `tests/unit/api/routers/test_manifests.py` to verify `state_json`, `state_updated_at`, and optional `last_run_*` persistence/update behavior. |
| DOC-REQ-004 (runtime guard) | FR-007, FR-008 | T001, T002, T003, T004, T006, T007, T010 | T005, T008, T009, T011, T012, T013 | Runtime code changes in API router/service layers | Run `./tools/test_unit.sh` plus runtime scope gate `./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime` to validate runtime/test coverage before handoff. |
