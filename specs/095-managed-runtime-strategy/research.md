# Research: Managed Runtime Strategy Pattern — Phase 1

**Branch**: `095-managed-runtime-strategy`
**Date**: 2026-03-21

## Research Tasks

### 1. Strategy Pattern for CLI Command Construction

**Decision**: Use Python's `abc.ABC` with `@abstractmethod` for the strategy interface.

**Rationale**: The existing codebase already uses ABC patterns (e.g. `AgentAdapter` protocol). ABC provides compile-time contract enforcement and clear intent.

**Alternatives considered**:
- `typing.Protocol` — lighter weight but doesn't enforce method implementation at class definition time. Since we want a clear contract that fails early, ABC is preferred.
- Registration decorators — more ceremony, less discoverable. A plain dict registry is simpler and follows existing patterns.

### 2. Fallthrough vs Hard Switch

**Decision**: Phase 1 uses fallthrough — the launcher checks the registry first, then falls through to existing `if/elif`. This allows incremental migration.

**Rationale**: Reduces blast radius. Only Gemini CLI goes through the strategy path in Phase 1. Other runtimes are unaffected.

**Alternatives considered**:
- Hard switch (remove all `if/elif` immediately) — rejected because it changes all runtimes at once and makes rollback harder.

### 3. Environment Shaping Boundary

**Decision**: `shape_environment()` on the strategy handles runtime-specific env passthrough. The adapter's `_shape_environment_for_oauth`/`_shape_environment_for_api_key` helpers remain as shared utilities for auth mode shaping. The strategy calls them internally.

**Rationale**: Auth mode shaping is cross-cutting (same for all runtimes). Runtime env keys (`GEMINI_HOME`, `CODEX_HOME`) are runtime-specific. Separating these concerns keeps the strategy focused.

### 4. Registry Location

**Decision**: `moonmind/workflows/temporal/runtime/strategies/__init__.py` with a module-level `RUNTIME_STRATEGIES` dict.

**Rationale**: Colocated with the launcher that consumes it. No need for a separate registry service — this is a simple in-process lookup.

## All NEEDS CLARIFICATION Resolved

No unresolved clarifications remain.
