# Implementation Plan: Codex Session Phase 2 Runtime Behaviors

**Branch**: `155-codex-session-phase2-runtime` | **Date**: 2026-04-12 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/155-codex-session-phase2-runtime/spec.md`

## Summary

Make the Codex managed-session Phase 2 controls real runtime behaviors instead of partial workflow vocabulary. Termination must drive runtime cleanup and supervision finalization before workflow completion, cancellation must stop active work without destroying session continuity, steering must reach the Codex app-server turn protocol, and launch/clear/interrupt/terminate must be safe under Temporal activity retry and cancellation.

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Temporal Python SDK, Pydantic managed-session schemas, Docker CLI-backed managed-session controller, Codex App Server JSON-RPC protocol, pytest  
**Storage**: JSON-backed `ManagedSessionStore` records plus container-local managed-session state file; durable operator truth remains artifact refs plus bounded workflow metadata  
**Testing**: focused pytest suites for workflow, activity, controller, and runtime boundaries; final verification via `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`  
**Target Platform**: Docker/Compose-hosted MoonMind Temporal workers and task-scoped managed Codex session containers  
**Project Type**: backend runtime/workflow hardening  
**Performance Goals**: Control actions should complete within existing activity timeout budgets; blocking controls must heartbeat at least every configured heartbeat interval  
**Constraints**: Runtime mode only; deliver production code and validation tests; keep Codex-specific behavior behind managed-session runtime/controller boundaries; do not introduce docs-only outcomes, compatibility aliases, or hidden fallback semantics  
**Scale/Scope**: One task-scoped Codex session container per task; no cross-task session reuse; no Kubernetes, Claude, Gemini, or generic marketplace expansion in this phase

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The plan controls Codex through its managed runtime protocol and MoonMind orchestration boundaries rather than reimplementing agent behavior.
- **II. One-Click Agent Deployment**: PASS. No new service, external dependency, or deployment prerequisite is introduced.
- **III. Avoid Vendor Lock-In**: PASS. Codex-specific behavior remains isolated to Codex managed-session runtime/controller surfaces; shared orchestration contracts stay typed and bounded.
- **IV. Own Your Data**: PASS. Durable state remains local artifacts, workflow metadata, and the JSON supervision record; no external storage path is added.
- **V. Skills Are First-Class and Easy to Add**: PASS. Skill resolution/materialization paths are not changed.
- **VI. Thin Scaffolding, Thick Contracts**: PASS. The work strengthens existing typed control contracts and deletes stub behavior instead of adding an alternate control path.
- **VII. Powerful Runtime Configurability**: PASS. Existing activity route, timeout, and runtime configuration surfaces remain the source of operational tuning.
- **VIII. Modular and Extensible Architecture**: PASS. Changes are scoped to workflow update handlers, activity wrappers/catalog entries, the managed-session controller, the Codex container runtime, and boundary tests.
- **IX. Resilient by Default**: PASS. Idempotency, non-transient failure classification, cleanup ordering, and activity heartbeats directly address retry/cancellation reliability.
- **X. Facilitate Continuous Improvement**: PASS. Failure outcomes and session state remain visible through existing operator-facing session artifacts and metadata.
- **XI. Spec-Driven Development**: PASS. Spec and plan artifacts exist before implementation tasks.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. This plan stays under `specs/`; no canonical documentation migration checklist is added.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. The plan removes the unsupported steering stub behavior rather than preserving a compatibility fallback.

## Project Structure

### Documentation (this feature)

```text
specs/155-codex-session-phase2-runtime/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── managed-session-phase2-controls.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/
├── schemas/
│   └── managed_session_models.py
└── workflows/
    └── temporal/
        ├── activity_catalog.py
        ├── activity_runtime.py
        ├── workflows/
        │   └── agent_session.py
        └── runtime/
            ├── codex_session_runtime.py
            ├── managed_session_controller.py
            └── managed_session_supervisor.py

tests/
└── unit/
    ├── services/temporal/runtime/
    │   ├── test_codex_session_runtime.py
    │   └── test_managed_session_controller.py
    └── workflows/temporal/
        ├── test_activity_runtime.py
        ├── test_agent_runtime_activities.py
        └── workflows/
            ├── test_agent_session.py
            └── test_run_codex_sessions.py
```

**Structure Decision**: Use the existing backend runtime/workflow structure. This feature does not add a new package, frontend surface, or external service; it hardens the current Codex managed-session workflow, activity, controller, and container-runtime boundaries.

## Phase 0: Research

Research decisions are captured in [research.md](./research.md).

Key decisions:

1. Preserve `CancelSession` as non-destructive control and route active-work cancellation through the existing interruption boundary.
2. Implement steering through the Codex App Server turn steering protocol and preserve active-turn identity while the turn remains active.
3. Treat termination as the only destructive cleanup action and let cleanup failures remain visible to Temporal retry/error handling.
4. Add idempotent controller behavior only when durable state proves the prior side effect completed.
5. Add heartbeat coverage to blocking session controls so cancellation can be delivered.

## Phase 1: Design

Design outputs:

- [data-model.md](./data-model.md)
- [contracts/managed-session-phase2-controls.md](./contracts/managed-session-phase2-controls.md)
- [quickstart.md](./quickstart.md)

Implementation surfaces:

1. Update workflow handlers and validators in `MoonMind.AgentSession` so cancel and terminate have distinct state transitions and duplicate termination remains safe.
2. Update activity wrappers and catalog routes so session controls that may block heartbeat and expose heartbeat timeouts.
3. Update `DockerCodexManagedSessionController` so launch, clear, interrupt, and terminate are retry-safe when durable state proves completion.
4. Update container-side `CodexManagedSessionRuntime.steer_turn()` to use Codex App Server steering instead of returning the unsupported stub.
5. Add or update tests at workflow, activity, controller, and container-runtime boundaries.

## Post-Design Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. Runtime-specific work remains behind adapter/controller/runtime boundaries.
- **II. One-Click Agent Deployment**: PASS. No deployment topology changes.
- **III. Avoid Vendor Lock-In**: PASS. Codex-only behavior remains isolated and documented as Codex-specific.
- **IV. Own Your Data**: PASS. No external data authority or SaaS dependency added.
- **V. Skills Are First-Class and Easy to Add**: PASS. Skill runtime paths are untouched.
- **VI. Thin Scaffolding, Thick Contracts**: PASS. The design uses existing typed models and boundary tests.
- **VII. Powerful Runtime Configurability**: PASS. Existing timeout/route configuration remains authoritative.
- **VIII. Modular and Extensible Architecture**: PASS. No cross-module refactor beyond the existing control boundaries.
- **IX. Resilient by Default**: PASS. Activity retry safety, heartbeat cancellation, and explicit terminal cleanup are first-order design elements.
- **X. Facilitate Continuous Improvement**: PASS. Tests and operator-visible state preserve evidence for future hardening.
- **XI. Spec-Driven Development**: PASS. Design artifacts align to the active spec.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. No canonical docs are converted into migration trackers.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. Stub steering behavior is removed from the production path.

## Complexity Tracking

No constitution violations. No additional complexity exceptions required.
