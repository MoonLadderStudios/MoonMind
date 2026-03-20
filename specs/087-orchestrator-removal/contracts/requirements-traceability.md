# Requirements Traceability: Remove mm-orchestrator

**Feature**: 087-orchestrator-removal  
**Date**: 2025-03-19

## DOC-REQ to FR Mapping

| DOC-REQ | FR IDs | Planned implementation surface | Validation Strategy |
| --- | --- | --- | --- |
| DOC-REQ-001 | FR-001 | Remove `orchestrator` service from `docker-compose.yaml` | Manual `docker compose config`; optional `compose up` smoke |
| DOC-REQ-002 | FR-001 | Remove `orchestrator-tests` from `docker-compose.test.yaml` | Grep + CI config review |
| DOC-REQ-003 | FR-001 | Strip `MOONMIND_ORCHESTRATOR_*` / `ORCHESTRATOR_*` from compose/env samples | Grep repo for vars; compose validation |
| DOC-REQ-004 | FR-002 | Delete `api_service/api/routers/orchestrator.py`; unregister in `api_service/api/main.py` | Unit tests; no `/orchestrator` routes |
| DOC-REQ-005 | FR-002 | Delete `moonmind/workflows/orchestrator/` | Import/tests pass |
| DOC-REQ-006 | FR-003 | Remove Orchestrator ORM + enums from `api_service/db/models.py`; Pydantic orchestrator models in `moonmind/schemas/workflow_models.py`; `compatibility.py` orchestrator branches | Unit tests + migration |
| DOC-REQ-007 | FR-002 | Remove `services/orchestrator` and delete `docker-compose.job.yaml` | Path removed; no job compose overlay |
| DOC-REQ-008 | FR-004 | Remove `tests/integration/orchestrator`, `tests/unit/workflows/orchestrator`, `tests/contract/test_orchestrator_api.py`, `.github/workflows/orchestrator-integration-tests.yml` | `./tools/test_unit.sh` |
| DOC-REQ-009 | FR-004 | Update `tools/test-integration.ps1`, `tests/task_dashboard/test_submit_runtime.js`, `tests/unit/api/routers/test_task_compatibility.py` | Targeted tests |
| DOC-REQ-010 | FR-005 | Archive/remove listed Temporal docs; update `docs/MoonMindArchitecture.md`, `docs/Temporal/TemporalArchitecture.md`; delete `specs/005-*`, `specs/050-*` | Doc review |
| DOC-REQ-011 | FR-003 | New Alembic revision under `api_service/migrations/versions/` | `alembic upgrade head` on test DB |
| DOC-REQ-012 | FR-006 | Full unit run + compose API startup check | `./tools/test_unit.sh` + compose |

## Coverage status

- All DOC-REQ IDs mapped: yes  
- All FRs have validation strategy: yes  
