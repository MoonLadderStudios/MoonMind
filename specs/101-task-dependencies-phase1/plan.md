# Implementation Plan: Task Dependencies Phase 1 — Backend Foundation

**Branch**: `101-task-dependencies-phase1` | **Date**: 2026-03-22 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/101-task-dependencies-phase1/spec.md`

## Summary

Add the `waiting_on_dependencies` state value across all persistence and projection layers in the MoonMind backend. This is the foundational primitive required before Phase 2 (workflow dependency logic), Phase 3 (API validation), and Phase 4 (frontend) can proceed. The change is purely additive — existing workflows and database rows are unaffected.

## Technical Context

**Language/Version**: Python 3.11
**Primary Dependencies**: SQLAlchemy 2.x, Alembic, Temporal Python SDK
**Storage**: PostgreSQL (native enum `moonmindworkflowstate`)
**Testing**: pytest via `./tools/test_unit.sh`
**Target Platform**: Linux server (Docker Compose deployment)
**Project Type**: Multi-service monorepo (API service + workflow workers)
**Performance Goals**: N/A (enum addition, no runtime cost)
**Constraints**: Must be backward-compatible with in-flight workflows; Alembic migration must be reversible
**Scale/Scope**: 6 file edits + 1 new migration file

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Orchestrate, Don't Recreate | PASS | State addition supports the orchestration layer; no agent-level logic. |
| II. One-Click Agent Deployment | PASS | No new infrastructure dependencies. Alembic migration runs automatically. |
| III. Avoid Vendor Lock-In | PASS | Uses standard SQLAlchemy/Temporal primitives only. |
| IV. Own Your Data | PASS | State stored in operator-controlled PostgreSQL + Temporal. |
| V. Skills Are First-Class | N/A | Not a skill change. |
| VI. Bittersweet Lesson | PASS | Enum addition is additive and trivially reversible. |
| VII. Runtime Configurability | PASS | State is a runtime value, not a code constant. |
| VIII. Modular Architecture | PASS | Additive to existing enum; no changes to interfaces or contracts. |
| IX. Resilient by Default | PASS | Backward-compatible; existing workflows ignore new value. |
| X. Continuous Improvement | N/A | Not directly applicable. |
| XI. Spec-Driven | PASS | This plan implements spec `101-task-dependencies-phase1/spec.md`. |

## Project Structure

### Documentation (this feature)

```text
specs/101-task-dependencies-phase1/
├── spec.md
├── plan.md              # This file
├── research.md          # Phase 0 (minimal — no unknowns)
├── data-model.md        # Phase 1
├── contracts/
│   └── requirements-traceability.md
├── quickstart.md        # Phase 1
├── checklists/
│   └── requirements.md
└── tasks.md             # Phase 2 (speckit-tasks)
```

### Source Code (repository root)

```text
api_service/
├── db/
│   └── models.py                    # MoonMindWorkflowState enum (MODIFY)
├── migrations/versions/
│   └── <new>_add_waiting_on_dependencies.py  # Alembic migration (NEW)
├── core/
│   └── sync.py                      # Projection sync mapping (MODIFY)
└── api/routers/
    └── executions.py                # Dashboard status map (MODIFY)

moonmind/workflows/
├── temporal/workflows/
│   └── run.py                       # STATE_WAITING_ON_DEPENDENCIES constant (MODIFY)
└── tasks/
    └── compatibility.py             # Compatibility status map (MODIFY)

tests/
└── unit/                            # Validation tests (NEW or MODIFY)
```

**Structure Decision**: Existing monorepo structure. All changes are modifications to existing files except the Alembic migration (new) and any new test files.

## Implementation Details

### 1. Enum Addition (`api_service/db/models.py`)

Add `WAITING_ON_DEPENDENCIES = "waiting_on_dependencies"` to `MoonMindWorkflowState` after `INITIALIZING`, preserving alphabetical-ish ordering by lifecycle stage:

```python
class MoonMindWorkflowState(str, enum.Enum):
    SCHEDULED = "scheduled"
    INITIALIZING = "initializing"
    WAITING_ON_DEPENDENCIES = "waiting_on_dependencies"  # NEW
    PLANNING = "planning"
    EXECUTING = "executing"
    AWAITING_EXTERNAL = "awaiting_external"
    FINALIZING = "finalizing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"
```

### 2. Alembic Migration

Generate via `alembic revision --autogenerate -m "add_waiting_on_dependencies_state"`, then manually adjust to use raw SQL for PostgreSQL enum type alteration:

```python
def upgrade():
    op.execute("ALTER TYPE moonmindworkflowstate ADD VALUE IF NOT EXISTS 'waiting_on_dependencies'")

def downgrade():
    # PostgreSQL does not support removing enum values directly.
    # The value is inert if unused, so downgrade is a no-op with a comment.
    pass
```

> **Note**: PostgreSQL `ALTER TYPE ... ADD VALUE` cannot be rolled back in the same transaction. The `IF NOT EXISTS` guard makes the migration idempotent.

### 3. Workflow Constant (`run.py`)

```python
STATE_WAITING_ON_DEPENDENCIES = "waiting_on_dependencies"
```

Added after `STATE_INITIALIZING` and before `STATE_PLANNING`.

### 4. Projection Sync (`sync.py`)

The sync function maps Temporal `mm_state` search attribute values to `MoonMindWorkflowState` members. The existing code catches `ValueError` for unknown values and logs a warning. Adding the enum value in step 1 automatically makes it recognizable — no code change needed in the `try/except` block.

However, the `_PROJECTION_DEFAULTS` mapping (which maps `(close_status, mm_state)` → `(state, waiting_reason)`) should include a default for the new state:

```python
(MoonMindWorkflowState.WAITING_ON_DEPENDENCIES, None),
```

### 5. Dashboard Status Map (`executions.py`)

```python
_DASHBOARD_STATUS_BY_STATE: dict[MoonMindWorkflowState, str] = {
    ...
    MoonMindWorkflowState.WAITING_ON_DEPENDENCIES: "waiting",
    ...
}
```

### 6. Compatibility Status Map (`compatibility.py`)

```python
_TEMPORAL_STATUS_MAP: dict[db_models.MoonMindWorkflowState, str] = {
    ...
    db_models.MoonMindWorkflowState.WAITING_ON_DEPENDENCIES: "waiting",
    ...
}
```

And in the reverse mapping:
```python
"waiting": (db_models.MoonMindWorkflowState.WAITING_ON_DEPENDENCIES,),
```

## Complexity Tracking

> No Constitution Check violations — table not needed.

## Verification Plan

### Automated Tests

1. `./tools/test_unit.sh` — all existing tests must pass with zero regressions.
2. New test: verify `MoonMindWorkflowState.WAITING_ON_DEPENDENCIES` exists and equals `"waiting_on_dependencies"`.
3. New test: verify dashboard status mapping returns `"waiting"` for the new state.
4. New test: verify compatibility status mapping returns `"waiting"` for the new state.

### Migration Verification

1. `alembic upgrade head` — no errors.
2. Inserting a row with `state = 'waiting_on_dependencies'` — succeeds.
