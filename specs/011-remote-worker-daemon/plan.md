# Implementation Plan: Remote Worker Daemon (015-Aligned)

**Branch**: `011-remote-worker-daemon` | **Date**: 2026-02-14 | **Spec**: `specs/011-remote-worker-daemon/spec.md`  
**Input**: Feature specification from `/specs/011-remote-worker-daemon/spec.md`

## Summary

Align the standalone `moonmind-codex-worker` daemon with 015 umbrella principles by enforcing Agentkit-always startup readiness, validating Google embedding credential requirements, extending queue execution semantics to support `codex_skill` jobs through skills-first routing with compatibility fallback, and enabling per-task Codex model/effort overrides with worker-default fallback.

## Technical Context

**Language/Version**: Python 3.11  
**Primary Dependencies**: `httpx`, subprocess-based CLI execution, queue REST endpoints, existing codex worker handler/runtime modules  
**Storage**: Local worker filesystem for checkouts/artifacts + MoonMind queue/artifact persistence via existing API  
**Testing**: Unit tests via `./tools/test_unit.sh`  
**Target Platform**: Linux shell/container runtime for standalone worker daemon  
**Project Type**: Backend daemon runtime + contracts/docs alignment  
**Performance Goals**: Keep poll/heartbeat behavior stable while adding lightweight startup checks and skill metadata handling  
**Constraints**: Preserve queue endpoint compatibility and existing `codex_exec` behavior while adding skills-first semantics and task-level codex runtime override support  
**Scale/Scope**: `moonmind/agents/codex_worker/*`, codex worker unit tests, and `specs/011-remote-worker-daemon/*`

## Constitution Check

- `.specify/memory/constitution.md` remains placeholder-only.
- Enforced repository constraints:
  - runtime implementation required (not docs-only)
  - unit validation via `./tools/test_unit.sh`

**Gate Status**: PASS WITH NOTE.

## Project Structure

### Documentation (this feature)

```text
specs/011-remote-worker-daemon/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── codex-worker-runtime-contract.md
│   └── requirements-traceability.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/agents/codex_worker/
├── __init__.py
├── cli.py
├── worker.py
├── handlers.py
└── utils.py

tests/unit/agents/codex_worker/
├── test_cli.py
├── test_worker.py
└── test_handlers.py
```

**Structure Decision**: Keep the existing standalone worker package boundary and evolve behavior inside CLI/worker/handler modules to satisfy umbrella semantics with minimal API coupling.

## Phase 0: Research Plan

1. Reconcile Milestone-3 `codex_exec` scope with umbrella skills-first execution requirements.
2. Define startup preflight additions for Agentkit and embedding readiness.
3. Define compatibility strategy for `codex_skill` without breaking `codex_exec` behavior.
4. Define required event metadata additions for observability.
5. Define precedence and fallback behavior for task-level `codex.model`/`codex.effort`.

## Phase 1: Design Outputs

- `research.md`: decisions for startup checks, skills fallback, and compatibility.
- `data-model.md`: startup, policy, and execution metadata entities.
- `contracts/codex-worker-runtime-contract.md`: updated queue/runtime contract including skills metadata and codex override precedence.
- `contracts/requirements-traceability.md`: updated requirement mapping to implementation and tests.
- `quickstart.md`: deterministic startup and verification flow aligned with 015 fast-path requirements.

## Post-Design Constitution Re-check

- Runtime implementation + tests are included.
- No enforceable constitution conflicts found.

**Gate Status**: PASS.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |
