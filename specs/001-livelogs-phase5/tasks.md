# Implementation Tasks: Phase 5 Live Logs Separations

## UI Interventions Panel Tasks
- [ ] T001 Create a dedicated `InterventionPanel` component in the UI. (Implements DOC-REQ-002, DOC-REQ-006)
- [ ] T002 Remove terminal input handling code from the `LiveLogs` component. Ensure it displays purely text output. (Implements DOC-REQ-001, DOC-REQ-007)
- [ ] T003 Implement `useInterventions` hook wrapping the REST API paths to dispatch Temporal signals (Pause, Resume, Cancel). (Implements DOC-REQ-003)

## Backend Adjustments
- [ ] T004 In `moonmind/agents/managed/environment.py` and `moonmind/workflows/`, remove any coupling to interactive terminal embeds for control workflows. (Implements DOC-REQ-001, DOC-REQ-007)
- [ ] T005 Verify `moonmind/proxy.py` and Temporal workflow routing audits all control signals appropriately instead of pushing them into `stdout`. (Implements DOC-REQ-004)
- [ ] T006 Verify the backend emits "System text" appropriately so the Live Log stream can still display system status inline without being interactive. (Implements DOC-REQ-005)

## Testing and Verification
- [ ] T007 Add Pytest unit tests in `tests/integration/test_interventions.py` verifying `POST /api/task-runs/{id}/signal` functions independent of the streaming connection. (Implements DOC-REQ-008, Validates DOC-REQ-003)
- [ ] T008 Update frontend Jest/Vitest tests in `tests/` to evaluate `InterventionPanel` rendering and actions independently of stream data parsing. (Validates DOC-REQ-001, DOC-REQ-002, DOC-REQ-006, DOC-REQ-007)
- [ ] T009 Run Temporal integration in `tests/` ensuring the agent control loop respects pause/resume signals while disconnected from logs, and logs audits correctly. (Validates DOC-REQ-004, DOC-REQ-005)
