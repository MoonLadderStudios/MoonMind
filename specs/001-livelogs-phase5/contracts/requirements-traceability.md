# Requirements Traceability: Phase 5

| DOC-REQ ID | Function Req | Implementation Surface | Validation Strategy |
|---|---|---|---|
| DOC-REQ-001 | FR3 | `frontend/src/entrypoints/task-detail.tsx`, `moonmind/workflows/temporal/service.py`, `moonmind/workflows/temporal/workflows/run.py` | Verify the task detail view exposes no terminal-style input path for managed-run control and that controls use explicit workflow APIs |
| DOC-REQ-002 | FR1 | `frontend/src/entrypoints/task-detail.tsx` | Vitest coverage asserts a dedicated Intervention section renders separately from Observation/Live Logs |
| DOC-REQ-003 | FR2 | `frontend/src/entrypoints/task-detail.tsx`, `api_service/api/routers/executions.py`, `moonmind/workflows/temporal/service.py`, `moonmind/workflows/temporal/workflows/run.py` | Router/integration/workflow tests verify Pause, Resume, Approve, Reject, Cancel, and Send Message dispatch through explicit REST/Temporal contracts |
| DOC-REQ-004 | FR4 | `moonmind/workflows/temporal/service.py`, `api_service/api/routers/executions.py`, `moonmind/schemas/temporal_models.py` | Service/router tests verify intervention audit entries are stored and serialized separately from stdout/stderr |
| DOC-REQ-005 | FR5 | `frontend/src/entrypoints/task-detail.tsx` | Confirm the log viewer remains passive while intervention history and controls are rendered outside the log surface |
| DOC-REQ-006 | FR1 | `frontend/src/entrypoints/task-detail.tsx` | Vitest coverage asserts Observation vs Intervention wording on the task detail page |
| DOC-REQ-007 | FR3 | `frontend/src/entrypoints/task-detail.tsx`, `moonmind/workflows/temporal/service.py` | Verify interventions succeed without any live-log session attachment or terminal transport assumptions |
| DOC-REQ-008 | FR6 | `tests/integration/test_interventions.py`, `frontend/src/entrypoints/task-detail.test.tsx`, `tests/unit/workflows/temporal/test_temporal_service.py`, `tests/unit/workflows/temporal/workflows/test_run_signals_updates.py` | Automated coverage verifies intervention delivery and audit behavior without a live stream dependency |
