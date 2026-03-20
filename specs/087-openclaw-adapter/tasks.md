# Tasks: OpenClaw streaming external agent

**Feature**: `087-openclaw-adapter`

## Phase 1 — Foundation

- [X] T001 Add `execution_style` to `ProviderCapabilityDescriptor` in `moonmind/schemas/agent_runtime_models.py`
- [X] T002 Add OpenClaw runtime gate and env helpers in `moonmind/openclaw/settings.py` and `moonmind/openclaw/__init__.py`
- [X] T003 Implement `OpenClawHttpClient` and SSE helpers in `moonmind/workflows/adapters/openclaw_client.py`

## Phase 2 — User Story 1 (execute via OpenClaw)

- [X] T004 Add translation helpers and `OpenClawExternalAdapter` in `moonmind/workflows/adapters/openclaw_agent_adapter.py`
- [X] T005 Add `run_openclaw_execution` in `moonmind/openclaw/execute.py`
- [X] T006 Register `openclaw` in `moonmind/workflows/adapters/external_adapter_registry.py`
- [X] T007 Add `external_adapter_execution_style` activity and streaming branch in `moonmind/workflows/temporal/workflows/agent_run.py`
- [X] T008 Register `integration.openclaw.execute` in `moonmind/workflows/temporal/activity_catalog.py` and `integration_openclaw_execute` in `moonmind/workflows/temporal/activity_runtime.py`
- [X] T009 Wire workflow worker activities in `moonmind/workflows/temporal/worker_runtime.py` and fleet caps in `moonmind/workflows/temporal/workers.py`

## Phase 3 — Validation

- [X] T010 [P] Unit tests `tests/unit/workflows/adapters/test_openclaw_client.py`
- [X] T011 [P] Unit tests `tests/unit/workflows/adapters/test_openclaw_agent_adapter.py`
- [X] T012 Update `tests/unit/workflows/temporal/test_temporal_worker_runtime.py` for new workflow activities
- [X] T013 Update `tests/integration/services/temporal/workflows/test_agent_run.py` for execution-style activity and `mm.workflow` worker
- [X] T014 Run `./tools/test_unit.sh` / `pytest` for touched tests and `validate-implementation-scope.sh --check tasks --mode runtime`

## Dependencies

T001 → T002–T009; T004–T005 before T007–T008; tests after implementation.
