# Implementation Plan: Worker Self-Heal System

**Branch**: `034-worker-self-heal` | **Date**: 2026-02-20 | **Spec**: `specs/034-worker-self-heal/spec.md`  
**Input**: Feature specification from `/specs/034-worker-self-heal/spec.md`

## Summary

Queue workers must deterministically detect stuck steps, bound retries, and recover without operator babysitting. This feature introduces a self-heal controller that wraps each step in an attempt loop with configurable budgets (wall/idle timeouts, no-progress threshold, max soft/hard resets), classifies failures into transient vs deterministic buckets, and selects soft reset, hard reset, or queue-level retry accordingly. The worker will persist checkpoint artifacts (`patches/steps/*.patch`, `state/steps/*.json`), extend live-control/queue APIs so operators can issue `retry_step`, `hard_reset_step`, or `resume_from_step`, and emit the new `task.step.attempt.*`/`task.self_heal.*` events plus StatsD metrics described in `docs/WorkerSelfHealSystem.md`. Tests and docs will prove the idle-timeout, hard-reset replay, operator commands, and metrics requirements.

## Technical Context

**Language/Version**: Python 3.11 for workers, FastAPI, Celery, and tests; TypeScript/ES2022 for the task dashboard controls.  
**Primary Dependencies**: `asyncio`, `httpx`, `docker` SDK, `sqlalchemy` 2.x, `FastAPI`, `pydantic` v2, RabbitMQ/Celery, StatsD emitter (`moonmind.workflows.speckit_celery.tasks._MetricsEmitter`), tmate for live sessions, pytest via `./tools/test_unit.sh`.  
**Storage**: PostgreSQL tables (`agent_jobs`, `agent_job_events`, `task_run_control_events`, `task_run_live_sessions`) plus filesystem artifacts under `var/artifacts/agent_jobs/<run_id>/` for logs, patches, and the new `state/steps` + `state/self_heal` JSON payloads; RabbitMQ broker for job dispatch; optional object storage untouched.  
**Testing**: `./tools/test_unit.sh` (pytest) targeting `tests/unit/agents/codex_worker`, `tests/unit/workflows/agent_queue`, and `tests/unit/api/routers/test_task_runs.py`; integration smoke via `docker compose -f docker-compose.test.yaml run orchestrator-tests` when validating live-control wiring.  
**Target Platform**: Linux containers (Codex worker image + API + RabbitMQ + PostgreSQL) running under Docker Compose/WSL.  
**Project Type**: Backend automation stack (Celery worker + FastAPI API + static dashboard) with shared Python packages and a small TypeScript UI bundle.  
**Performance Goals**: Detect wall-clock overruns at 900 s and idle gaps at 300 s per attempt, resolve ≥95 % of transient/stuck failures within `step_max_attempts=3`, rebuild resume-from-step workspaces in <5 minutes for ≤10 completed steps, and emit StatsD counters/timers for every attempt/duration/timeout.  
**Constraints**: Enforce documented budgets (max 3 attempts, 2 no-progress repeats, 1 hard reset per job), scrub secrets before persisting failure signatures/artifacts, ship minimal retry prompts (objective + failure summary + diff hash), respect cancel/pause/takeover fencing, and keep operator controls/metrics backward compatible.  
**Scale/Scope**: Applies to all queue task jobs (typically 3–10 ordered steps) processed by a handful of Codex workers concurrently; must coordinate with server-side live-control payloads and queue-level retries without regressing existing Spec Automation or dashboard flows.

## Constitution Check

`.specify/memory/constitution.md` is still the placeholder template with unnamed principles, so there are no enforceable gates to evaluate. Flagging **NEEDS CLARIFICATION** for product leadership to ratify the constitution; until then we proceed under the established MoonMind norms (test-first, CLI-first, observability) and will re-run this gate once the document is populated.

## Project Structure

### Documentation (this feature)

```text
specs/034-worker-self-heal/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── requirements-traceability.md
│   └── task-run-recovery.openapi.yaml
└── checklists/
```

### Source Code (repository root)

```text
moonmind/
├── agents/codex_worker/
│   ├── worker.py              # Step orchestration, live-control loop, metrics
│   ├── handlers.py            # Codex exec adapter + output callbacks
│   ├── secret_refs.py         # Vault + token helpers for git/publish
│   └── __init__.py
├── workflows/agent_queue/
│   ├── models.py              # agent_jobs + control event tables
│   ├── repositories.py        # liveControl + artifact persistence helpers
│   ├── service.py             # TaskRun control API logic
│   ├── storage.py             # Artifact IO wrappers
│   └── task_contract.py       # Canonical payload normalization/validation
├── workflows/speckit_celery/metrics.py (StatsD client reused by worker)
api_service/
├── api/routers/task_runs.py   # Operator control endpoints
├── api/routers/agent_queue.py # Worker-auth claim/heartbeat surfaces
├── static/task_dashboard/dashboard.js # Live-control UI + event rendering
├── schemas.py / dependencies.py
celery_worker/
└── speckit_worker.py          # Worker entrypoint wiring settings

tests/
├── unit/agents/codex_worker/test_worker.py
├── unit/api/routers/test_task_runs.py
├── unit/workflows/agent_queue/test_service_hardening.py
├── unit/workflows/agent_queue/test_repositories.py
└── unit/workflows/agent_queue/test_task_contract.py

docs/
├── WorkerSelfHealSystem.md
├── WorkerPauseSystem.md
└── TasksStepSystem.md

tools/test_unit.sh             # Canonical pytest runner
```

**Structure Decision**: Keep all self-heal logic inside `moonmind/agents/codex_worker` so a single controller owns attempt budgeting, metrics, and checkpoint replay. Server-side queue changes stay in `moonmind/workflows/agent_queue` and FastAPI routers under `api_service/api/routers` to ensure auditability and operator UX remain centralized. Tests mirror this split—worker behavior in `tests/unit/agents/codex_worker`, persistence/control logic in `tests/unit/workflows/agent_queue`, and HTTP/UI coverage in `tests/unit/api`. This layout limits blast radius and preserves existing deployment boundaries.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|---------------------------------------|
| _None_ | – | – |
