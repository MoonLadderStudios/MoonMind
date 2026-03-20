# Data Model: Orchestrator Removal

**Feature**: 087-orchestrator-removal

## Entities removed

All persisted entities tied to SQLAlchemy classes in `api_service/db/models.py` for orchestrator runs:

- **OrchestratorRun** — primary run record (`orchestrator_runs` or equivalent table name from `__tablename__`)
- **OrchestratorRunArtifact** — artifact rows per run
- Related: action plan / plan steps / task states / approval gate rows as defined on the `OrchestratorRun` model relationships

Implementation must read current `models.py` to enumerate exact `__tablename__` values and FK dependencies for the migration `downgrade`/`upgrade` pair.

## Alembic drop order (T002 inventory)

Before dropping `orchestrator_runs`, delete dependent rows and remove FKs/columns on shared tables:

1. **Delete** rows in `workflow_task_states` where `orchestrator_run_id IS NOT NULL` (orchestrator-only task state rows).
2. On `workflow_task_states`: drop `uq_orchestrator_task_state_attempt`, `ix_workflow_task_states_orchestrator_run_id`, `ck_workflow_task_state_orchestrator_plan_step`, `ck_workflow_task_state_run_id_exclusive`; drop FK on `orchestrator_run_id`; drop columns `orchestrator_run_id`, `plan_step`, `plan_step_status`, `worker_state`.
3. Add replacement check: `workflow_run_id IS NOT NULL` (all remaining rows are workflow-scoped).
4. **Drop tables** (child → parent): `orchestrator_task_steps`, `orchestrator_run_artifacts`, `orchestrator_runs`, `orchestrator_action_plans`, `approval_gates` (only referenced by orchestrator runs).
5. Optionally drop unused PostgreSQL enum types (`orchestratorrunstatus`, etc.) via raw SQL where safe.

## Post-migration state

- No orchestrator tables at Alembic head.
- Application code MUST NOT import removed ORM classes.

## Non-goals

- Migrating historical orchestrator data to another store (out of scope per spec assumptions).
