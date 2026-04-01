# Implementation Tasks: Phase 6 Legacy Observability Cutoff

## Backend and DTO Removal
- [x] T001 Mark `TaskRunLiveSession` schemas as deprecated in Python API models and eventually delete API routes generating WebSockets text streams for non-OAuth uses. (Implements DOC-REQ-002, DOC-REQ-005)
- [x] T002 In `api_service/api/routers/`, remove `web_ro`-driven terminal viewer hooks previously allocated to managed runs. (Implements DOC-REQ-003)

## Agent Runtime Overhauls
- [x] T003 Clean `moonmind/agents/managed/` environments to eliminate `tmate` subprocess spawns that previously provided live observational log sockets. (Implements DOC-REQ-004)

## UI and Docs Adjustments
- [x] T004 Confirm `frontend/src/` generic detail view smoothly skips terminal-embedded viewing when it hits historical runs lacking logs by rendering fallback read-only text without crashing. (Implements DOC-REQ-001)
- [x] T005 Update `docs/` describing observability architectures ensuring no PTY embeds represent standard views. (Implements DOC-REQ-006, DOC-REQ-007)

## Testing and Guardrails
- [x] T006 Provide regression scenarios within `tests/integration/` validating the launcher passes cleanly and historical test stubs mock gracefully skipping `web_ro`. (Implements DOC-REQ-008, Validates DOC-REQ-001, DOC-REQ-004)
- [x] T007 Validate the codebase still parses correctly via `./tools/test_unit.sh` and `npm run build` after removal of DTO references. (Validates DOC-REQ-002, DOC-REQ-003, DOC-REQ-005)
