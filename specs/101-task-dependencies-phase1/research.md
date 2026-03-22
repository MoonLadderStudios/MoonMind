# Research: Task Dependencies Phase 1

**Branch**: `101-task-dependencies-phase1`
**Date**: 2026-03-22

## Research Summary

No NEEDS CLARIFICATION items remain — all technical context was resolved through direct codebase inspection.

### Decision 1: PostgreSQL Enum Alteration Strategy

**Decision**: Use `ALTER TYPE ... ADD VALUE IF NOT EXISTS` in the Alembic migration.
**Rationale**: PostgreSQL does not support removing enum values, but `ADD VALUE IF NOT EXISTS` is idempotent and safe. The downgrade is a no-op.
**Alternatives considered**:
- Creating a new enum type and renaming: Too disruptive for a single value addition.
- Using a string column instead of a native enum: Breaks the existing pattern used by all other states.

### Decision 2: Dashboard Status String

**Decision**: Map `WAITING_ON_DEPENDENCIES` to `"waiting"`.
**Rationale**: Existing status strings are short single-word labels (`"queued"`, `"running"`, `"failed"`, etc.). `"waiting"` is consistent with this convention and distinct from `"awaiting_action"` (used for `AWAITING_EXTERNAL`).
**Alternatives considered**:
- `"blocked"`: Too strong/negative; the task is waiting, not broken.
- `"pending"`: Confusable with `"queued"`.
- `"waiting_on_dependencies"`: Too long for a dashboard badge.

### Decision 3: Enum Ordering

**Decision**: Place `WAITING_ON_DEPENDENCIES` after `INITIALIZING` in the enum class, matching lifecycle stage ordering.
**Rationale**: The lifecycle progresses `initializing → waiting_on_dependencies → planning → ...`. Keeping the enum members in lifecycle order improves readability.
**Alternatives considered**:
- Alphabetical ordering: Breaks the implicit lifecycle ordering used by existing members.
- Appending at end: Functional but makes the lifecycle harder to read.
