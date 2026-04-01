# Implementation Tasks: Phase 5 Live Logs Separations

## UI Interventions Panel Tasks
- [x] T001 Create a dedicated `InterventionPanel` in `frontend/src/entrypoints/task-detail.tsx`. (Implements DOC-REQ-002, DOC-REQ-006)
- [x] T002 Keep the Live Logs viewer passive-only with no terminal input handling on task detail. (Implements DOC-REQ-001, DOC-REQ-007)
- [x] T003 Route Pause/Resume/Approve/Reject/Cancel/Send Message through explicit execution REST calls in `frontend/src/entrypoints/task-detail.tsx`. (Implements DOC-REQ-003)

## Backend Adjustments
- [x] T004 Harden `moonmind/workflows/temporal/service.py` and `moonmind/workflows/temporal/workflows/run.py` so managed-run controls use Temporal updates/cancel paths rather than interactive terminal transport. (Implements DOC-REQ-001, DOC-REQ-003, DOC-REQ-007)
- [x] T005 Persist separate intervention audit entries in execution memo/API serialization instead of mixing operator actions into stdout/stderr. (Implements DOC-REQ-004)
- [x] T006 Preserve the Live Logs viewer as an observation surface while leaving room for separate inline system annotations. (Implements DOC-REQ-005)

## Testing and Verification
- [x] T007 Add/refresh Pytest coverage in `tests/integration/test_interventions.py` verifying `POST /api/executions/{id}/signal` works independently of the streaming connection. (Implements DOC-REQ-008, Validates DOC-REQ-003)
- [x] T008 Update Vitest coverage in `frontend/src/entrypoints/task-detail.test.tsx` to validate `InterventionPanel` rendering and actions independently of log streaming. (Validates DOC-REQ-001, DOC-REQ-002, DOC-REQ-006, DOC-REQ-007)
- [x] T009 Add workflow/service/router boundary tests covering pause/resume/send-message/reject behavior and separate intervention audit history. (Validates DOC-REQ-004, DOC-REQ-005)
