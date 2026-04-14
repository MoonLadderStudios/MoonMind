# Implementation Plan: codex-managed-session-phase0-1

**Branch**: `141-codex-managed-session-phase0-1` | **Date**: 2026-04-08 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/141-codex-managed-session-phase0-1/spec.md`

## Summary

Implement the Phase 0 and Phase 1 slice of the Codex managed-session rollout by: 1) updating the canonical managed-session plane doc to describe the current near-term production truth around artifacts, workflow metadata, and `ManagedSessionStore`, and 2) refactoring `MoonMind.AgentSession` and its callers to use handler-safe initialization plus typed workflow updates with explicit validators instead of the generic mutating `control_action` signal.

## Technical Context

**Language/Version**: Python 3.13  
**Primary Dependencies**: Temporal Python SDK workflow decorators and updates, `moonmind.schemas.managed_session_models`, `MoonMind.AgentSession`, `MoonMind.Run`, `CodexSessionAdapter`, task-run session-control router  
**Storage**: workflow state plus file-backed `ManagedSessionStore` records and artifact refs  
**Testing**: pytest unit tests plus final verification via `./tools/test_unit.sh`  
**Target Platform**: Docker/Compose-hosted MoonMind workers using Temporal-managed workflows  
**Project Type**: backend workflow contract + docs alignment  
**Performance Goals**: preserve current managed-session behavior while removing ambiguous mutation entrypoints and failing invalid requests before activity execution  
**Constraints**: Phase 0 and Phase 1 only; no Phase 2 steer-runtime implementation and no Continue-As-New in this slice
**Scale/Scope**: one canonical doc update plus workflow/caller refactor across session workflow, run workflow, adapter wiring, and task-run router control calls

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. The change tightens MoonMind’s workflow/control boundary around the existing Codex managed-session runtime instead of recreating provider behavior.
- **II. One-Click Agent Deployment**: PASS. No new deployment dependency is introduced.
- **III. Avoid Vendor Lock-In**: PASS. The slice stays within the existing Codex-specific managed-session boundary and does not entangle other runtimes.
- **IV. Own Your Data**: PASS. Phase 0 clarifies that artifacts plus bounded workflow metadata remain the operator/audit truth surface.
- **V. Skills Are First-Class and Easy to Add**: PASS. No agent-skill runtime behavior changes are introduced.
- **VI. Thin Scaffolding, Thick Contracts**: PASS. The main change is contract hardening at the workflow boundary and documentation of current truth surfaces.
- **VII. Powerful Runtime Configurability**: PASS. No new hardcoded runtime-selection behavior is introduced.
- **VIII. Modular and Extensible Architecture**: PASS. Changes stay within docs, schemas, workflow surface, and direct callers.
- **IX. Resilient by Default**: PASS. Validator-based rejection and handler-safe initialization reduce ambiguous state mutation and in-flight workflow risk.
- **XI. Spec-Driven Development**: PASS. This slice adds a dedicated spec/plan/tasks set before implementation.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. The canonical doc remains declarative about the current intended production path, while rollout sequencing stays in the spec artifacts.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. The old generic mutating signal path will be removed instead of kept as a compatibility alias.

## Research

- `docs/ManagedAgents/CodexCliManagedSessions.md` currently says Temporal is the control plane and system of record, but it does not explicitly describe the current operational role of `ManagedSessionStore`.
- `moonmind/workflows/temporal/workflows/agent_session.py` still initializes binding state from `run()`, exposes a generic mutating `control_action` signal, and only provides `SendFollowUp` plus `ClearSession` updates.
- `moonmind/workflows/adapters/codex_session_adapter.py`, `moonmind/workflows/temporal/workflows/agent_run.py`, `moonmind/workflows/temporal/workflows/run.py`, and `api_service/api/routers/task_runs.py` still assume the older signal/update names.
- The controller and activity surfaces already support `agent_runtime.interrupt_turn`, so Phase 1 can wire a real workflow-level `InterruptTurn` without waiting on the later steer-runtime implementation.
- `codex_session_runtime.py` still returns an unsupported result for `steer_turn`, so this slice should expose the typed workflow update and validator while failing through the existing runtime behavior when invoked.

## Project Structure

- Update `docs/ManagedAgents/CodexCliManagedSessions.md` for Phase 0 truth-surface alignment.
- Extend `moonmind/schemas/managed_session_models.py` with typed update request contracts for the workflow boundary.
- Refactor `moonmind/workflows/temporal/workflows/agent_session.py` to use `@workflow.init`, typed updates, and validators.
- Update `moonmind/workflows/adapters/codex_session_adapter.py`, `moonmind/workflows/temporal/workflows/agent_run.py`, `moonmind/workflows/temporal/workflows/run.py`, and `api_service/api/routers/task_runs.py` to target the typed update names.
- Add or update tests in `tests/unit/workflows/temporal/workflows/test_agent_session.py`, `tests/unit/workflows/temporal/workflows/test_run_codex_sessions.py`, `tests/unit/workflows/adapters/test_codex_session_adapter.py`, and `tests/unit/api/routers/test_task_runs.py`.

## Data Model

- See [data-model.md](./data-model.md) for the typed update requests and validator context carried by the workflow.

## Contracts

- [contracts/agent-session-workflow-controls.md](./contracts/agent-session-workflow-controls.md)

## Implementation Plan

1. Add failing tests for the new workflow initialization path, typed update names, validator rejections, and caller routing updates.
2. Update the canonical managed-session doc to describe the production publication and recovery truth surfaces.
3. Add typed request models for send, interrupt, steer, cancel, and terminate operations at the workflow boundary.
4. Refactor `MoonMind.AgentSession` to initialize from `@workflow.init`, remove the generic mutating signal, add typed updates plus validators, and wire `InterruptTurn` through the existing activity surface.
5. Update the session adapter, parent run workflow, and task-run router to call the typed update names.
6. Run focused tests, record analyze/remediation results, then run final verification.

## Verification Plan

### Automated Tests

1. `./.venv/bin/pytest -q tests/unit/workflows/temporal/workflows/test_agent_session.py tests/unit/workflows/temporal/workflows/test_run_codex_sessions.py tests/unit/workflows/adapters/test_codex_session_adapter.py tests/unit/api/routers/test_task_runs.py`
2. `./tools/test_unit.sh`

### Manual Validation

1. Read `docs/ManagedAgents/CodexCliManagedSessions.md` and confirm the production publication/recovery roles are explicit and non-contradictory.
2. Inspect `MoonMind.AgentSession` and confirm only `attach_runtime_handles` remains as a signal while mutations use typed updates with validators.
3. Inspect the parent workflow, session adapter, and task-run router to confirm they call the new typed update names.
