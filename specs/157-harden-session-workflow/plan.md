# Implementation Plan: Harden Session Workflow

**Branch**: `157-harden-session-workflow` | **Date**: 2026-04-12 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/157-harden-session-workflow/spec.md`

## Summary

Harden the task-scoped Codex managed-session workflow for long-lived, message-heavy execution. The implementation will serialize asynchronous mutators that touch shared session state, wait for runtime handles before runtime-bound updates proceed, drain accepted handlers before completion or handoff, and add a main-loop Continue-As-New path that carries bounded session identity, locator, control metadata, continuity refs, and compact request-tracking state.

## Technical Context

**Language/Version**: Python 3.12
**Primary Dependencies**: Temporal Python SDK, Pydantic managed-session schemas, MoonMind activity catalog/routing, pytest
**Storage**: Workflow history and bounded workflow input/query state; durable operator truth remains artifact refs plus bounded workflow metadata; `ManagedSessionStore` remains an operational recovery index outside this workflow-local hardening
**Testing**: focused pytest coverage for `MoonMind.AgentSession` workflow handlers and schema payloads; final verification via `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
**Target Platform**: Docker/Compose-hosted MoonMind Temporal workers running the Codex managed-session workflow
**Project Type**: backend runtime/workflow hardening
**Performance Goals**: Mutator serialization must not introduce busy-waiting; Continue-As-New must bound workflow history for long-running sessions; readiness waits must wake when runtime handles attach
**Constraints**: Runtime mode only; deliver production runtime code and validation tests; keep large prompts/transcripts/runtime-local state out of workflow payloads; do not add docs-only outcomes, compatibility aliases, or hidden fallback semantics
**Scale/Scope**: One task-scoped Codex managed session per task; no cross-task reuse, multi-runtime expansion, frontend UI work, or standalone image path in this phase

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The plan hardens MoonMind orchestration around Codex sessions without reimplementing Codex behavior.
- **II. One-Click Agent Deployment**: PASS. No new service, external dependency, or deployment prerequisite is introduced.
- **III. Avoid Vendor Lock-In**: PASS. Codex-specific state remains in managed-session workflow/schema surfaces and does not create a generic provider lock-in beyond the existing Codex session plane.
- **IV. Own Your Data**: PASS. Bounded workflow metadata and artifact refs remain local/operator-controlled; no external storage is added.
- **V. Skills Are First-Class and Easy to Add**: PASS. Agent skill resolution and materialization paths are not changed.
- **VI. Thin Scaffolding, Thick Contracts**: PASS. The plan strengthens typed workflow state and update contracts instead of adding alternate control paths.
- **VII. Powerful Runtime Configurability**: PASS. The test hook is explicit input for shortened-history validation; production behavior still follows existing Temporal/runtime configuration.
- **VIII. Modular and Extensible Architecture**: PASS. Changes are scoped to managed-session schema, workflow handler, and boundary tests.
- **IX. Resilient by Default**: PASS. Handler serialization, readiness waits, handler drain, and Continue-As-New directly improve long-running workflow resilience.
- **X. Facilitate Continuous Improvement**: PASS. Query state and validation tests preserve diagnostic evidence for future hardening.
- **XI. Spec-Driven Development**: PASS. This plan is derived from the active spec and preserves traceability to functional requirements.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Implementation planning remains under `specs/`; no canonical docs are converted into rollout checklists.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. No compatibility aliases or deprecated internal contracts are introduced.

## Project Structure

### Documentation (this feature)

```text
specs/157-harden-session-workflow/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── agent-session-workflow-hardening.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/
├── schemas/
│   └── managed_session_models.py
└── workflows/
    └── temporal/
        └── workflows/
            └── agent_session.py

tests/
└── unit/
    └── workflows/
        └── temporal/
            └── workflows/
                └── test_agent_session.py
```

**Structure Decision**: Use the existing backend workflow/schema/test layout. This feature does not add a new package, frontend surface, external service, or canonical documentation migration.

## Phase 0: Research

Research decisions are captured in [research.md](./research.md).

Key decisions:

1. Use a workflow-local async lock to serialize shared-state mutators.
2. Wait for runtime handles inside accepted runtime-bound updates instead of rejecting startup-race requests at validation time.
3. Drain accepted handlers before workflow completion or Continue-As-New.
4. Trigger Continue-As-New from the main workflow loop and carry only bounded session state.
5. Preserve request tracking as compact identified-control metadata only when stable request identity exists.

## Phase 1: Design

Design outputs:

- [data-model.md](./data-model.md)
- [contracts/agent-session-workflow-hardening.md](./contracts/agent-session-workflow-hardening.md)
- [quickstart.md](./quickstart.md)

Implementation surfaces:

1. Extend `CodexManagedSessionWorkflowInput` and `CodexManagedSessionSnapshot` with bounded runtime locator, control metadata, continuity refs, and test hook fields.
2. Add workflow-local handler locking and readiness waits to runtime-bound update handlers in `MoonMind.AgentSession`.
3. Persist latest continuity refs in workflow query state after send, steer, interrupt, and clear projections refresh.
4. Add main-loop Continue-As-New trigger and payload construction; do not initiate handoff from update handlers.
5. Add tests covering validator behavior, lock serialization, readiness waits, handler drain, Continue-As-New trigger, and handoff payload carry-forward.

## Post-Design Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. The design controls session lifecycle through existing orchestration boundaries only.
- **II. One-Click Agent Deployment**: PASS. No deployment topology or secret requirement changes.
- **III. Avoid Vendor Lock-In**: PASS. Codex session details remain isolated to Codex managed-session contracts.
- **IV. Own Your Data**: PASS. No external state authority is introduced; large runtime content stays out of workflow state.
- **V. Skills Are First-Class and Easy to Add**: PASS. Skill system paths are untouched.
- **VI. Thin Scaffolding, Thick Contracts**: PASS. The design updates typed schemas and handler contracts directly.
- **VII. Powerful Runtime Configurability**: PASS. The shortened-history hook is explicit and bounded to validation.
- **VIII. Modular and Extensible Architecture**: PASS. Changes stay within existing workflow/schema/test boundaries.
- **IX. Resilient by Default**: PASS. Serialization, handler drain, and Continue-As-New improve unattended long-running behavior.
- **X. Facilitate Continuous Improvement**: PASS. Query state and tests make failures easier to diagnose.
- **XI. Spec-Driven Development**: PASS. Design artifacts map to the active spec.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. No canonical docs are used as migration trackers.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. No legacy alias or compatibility wrapper is added.

## Complexity Tracking

No constitution violations. No additional complexity exceptions required.
