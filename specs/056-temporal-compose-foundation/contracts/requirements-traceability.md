# Requirements Traceability: Temporal Compose Foundation

| DOC-REQ ID | Source Reference | Mapped FR(s) | Planned Implementation Surfaces | Implementation Task Coverage | Validation Task Coverage | Validation Strategy |
|---|---|---|---|---|---|---|
| DOC-REQ-001 | `docs/Temporal/TemporalPlatformFoundation.md` §2, §3 | FR-001, FR-015 | `docker-compose.yaml`, `.env-template`, `README.md`, `services/temporal/*` | `T001`, `T015`, `T021` | `T016` | Compose startup/integration test `tests/integration/temporal/test_compose_foundation.py`; foundation health checks in quickstart |
| DOC-REQ-002 | `docs/Temporal/TemporalPlatformFoundation.md` §4 | FR-002, FR-015 | `docker-compose.yaml`, `services/temporal/dynamicconfig/development-sql.yaml`, `moonmind/workflows/temporal/client.py` | `T001`, `T003`, `T021` | `T016` | Integration test `tests/integration/temporal/test_compose_foundation.py` plus persistence/visibility health API checks |
| DOC-REQ-003 | `docs/Temporal/TemporalPlatformFoundation.md` §4.1, §13 | FR-016, FR-015 | `services/temporal/scripts/rehearse-visibility-schema-upgrade.sh`, `api_service/api/routers/executions.py`, upgrade readiness persistence | `T004`, `T013`, `T020`, `T021` | `T018` | Upgrade rehearsal integration gate `tests/integration/temporal/test_upgrade_rehearsal.py` blocks rollout on failed/missing rehearsal |
| DOC-REQ-004 | `docs/Temporal/TemporalPlatformFoundation.md` §5 | FR-005, FR-015 | `services/temporal/scripts/bootstrap-namespace.sh`, `api_service/api/routers/executions.py`, `moonmind/workflows/temporal/service.py` | `T002`, `T005`, `T019` | `T017` | Namespace idempotency integration test `tests/integration/temporal/test_namespace_retention.py` |
| DOC-REQ-005 | `docs/Temporal/TemporalPlatformFoundation.md` §6 | FR-003, FR-011, FR-015 | `api_service/api/routers/executions.py`, `moonmind/workflows/temporal/service.py`, `moonmind/schemas/temporal_models.py` | `T009`, `T013`, `T028` | `T022`, `T023` | Contract and integration tests verify visibility-backed list/filter/pagination behavior |
| DOC-REQ-006 | `docs/Temporal/TemporalPlatformFoundation.md` §7 | FR-004, FR-015 | `moonmind/workflows/temporal/workers.py`, `moonmind/workflows/temporal/service.py`, `api_service/api/routers/executions.py` | `T010`, `T037` | `T035` | Unit tests in `tests/unit/workflows/temporal/test_settings_and_queue_semantics.py` verify routing-only queue semantics |
| DOC-REQ-007 | `docs/Temporal/TemporalPlatformFoundation.md` §8 | FR-006, FR-015 | `moonmind/workflows/temporal/workers.py`, `moonmind/config/settings.py` | `T002`, `T007`, `T010`, `T038` | `T034` | Worker versioning tests in `tests/unit/workflows/temporal/test_workers.py` verify Auto-Upgrade default and exception governance |
| DOC-REQ-008 | `docs/Temporal/TemporalPlatformFoundation.md` §9 | FR-007, FR-015 | `.env-template`, `moonmind/config/settings.py`, upgrade readiness checks in `moonmind/workflows/temporal/service.py` | `T002`, `T007`, `T013`, `T020`, `T039` | `T018`, `T035` | Integration + unit gates ensure shard-decision acknowledgement is required before rollout |
| DOC-REQ-009 | `docs/Temporal/TemporalPlatformFoundation.md` §10 | FR-008, FR-015 | `moonmind/workflows/temporal/schedules.py`, `api_service/api/routers/recurring_tasks.py`, `api_service/api/routers/executions.py` | `T012`, `T033` | `T026` | Integration tests verify schedule upsert/trigger behavior replaces external cron ownership |
| DOC-REQ-010 | `docs/Temporal/TemporalPlatformFoundation.md` §11-§12 | FR-009, FR-015 | `docker-compose.yaml`, `api_service/api/routers/executions.py`, Temporal metrics/log hooks | `T001`, `T007`, `T019`, `T040` | `T016`, `T036` | Compose network assertions and observability integration tests for poller/retry/visibility-failure signals |
| DOC-REQ-011 | `docs/Temporal/TemporalArchitecture.md` §4, §5, §17 | FR-010, FR-015 | `moonmind/workflows/temporal/*`, `moonmind/workflows/speckit_celery/tasks.py`, compatibility routing surfaces | `T006`, `T009`, `T011`, `T029` | `T023` | Integration tests verify Temporal-first execution ownership and activity side-effect boundaries |
| DOC-REQ-012 | `docs/Temporal/TemporalArchitecture.md` §8, §12, §16 | FR-011, FR-015 | `api_service/api/routers/executions.py`, `moonmind/schemas/temporal_models.py`, `moonmind/workflows/temporal/service.py` | `T008`, `T014`, `T015`, `T027` | `T022`, `T023` | Contract tests cover start/update/signal/cancel/list/describe endpoints and pagination token handling |
| DOC-REQ-013 | `docs/Temporal/TemporalArchitecture.md` §7, §14 | FR-012, FR-015 | `moonmind/workflows/temporal/service.py`, artifact handling in `moonmind/workflows/agent_queue/storage.py` | `T011`, `T030` | `T024` | Unit tests assert large payloads are converted to artifact references and excluded from workflow history |
| DOC-REQ-014 | `docs/Temporal/TemporalArchitecture.md` §9 | FR-013, FR-015 | `moonmind/workflows/temporal/service.py`, `api_service/api/routers/manifests.py` | `T011`, `T031` | `T024` | Unit tests verify `fail_fast`, `continue_and_report`, and `best_effort` branching behavior |
| DOC-REQ-015 | `docs/Temporal/TemporalArchitecture.md` §11 | FR-014, FR-015 | `moonmind/workflows/temporal/service.py`, `api_service/api/routers/executions.py` signal/callback surfaces | `T011`, `T032` | `T025` | Integration tests verify callback signal handling and timer-based polling fallback behavior |

## Runtime Mode Alignment Gate

- Selected orchestration mode for this feature is **runtime implementation mode**.
- Planning and tasks must include production runtime code changes plus validation tests.
- Docs-only outputs are non-compliant with FR-001, FR-015, and runtime intent declared in spec input.

## Coverage Gate

- Total source requirements rows: **15** (`DOC-REQ-001` through `DOC-REQ-015`).
- Every row maps to FRs, planned implementation surfaces, implementation task coverage, and validation task coverage.
- Planning must fail if any requirement row is removed, unmapped, or missing validation strategy or task coverage.
