# Implementation Plan: Typed Workflow Messages

**Branch**: `174-typed-workflow-messages` | **Date**: 2026-04-15 | **Spec**: [spec.md](./spec.md)  
**Input**: Single-story feature specification from `/specs/174-typed-workflow-messages/spec.md`

## Summary

Harden the Codex managed-session workflow message boundary by making the active runtime-handle signal, clear/cancel/terminate updates, and Continue-As-New handoff use named Pydantic contracts. Preserve the legacy catch-all `control_action` signal only as a replay compatibility shim. Validate with focused schema tests, workflow unit tests, and the existing managed-session lifecycle integration scenario.

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Temporal Python SDK, Pydantic v2, pytest  
**Storage**: Temporal workflow history and bounded workflow state  
**Unit Testing**: `pytest` through `./tools/test_unit.sh`  
**Integration Testing**: pytest Temporal time-skipping lifecycle test; not marked `integration_ci` because Temporal test server timing is local-only  
**Target Platform**: Docker/Compose-hosted MoonMind worker fleet  
**Project Type**: Python service and Temporal workflow  
**Performance Goals**: Continuation payloads and query snapshots remain bounded and reference-sized  
**Constraints**: Preserve in-flight workflow compatibility for legacy `control_action`; do not embed prompts, transcripts, logs, or credentials in workflow metadata  
**Scale/Scope**: One Codex task-scoped managed session workflow message surface

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. Work stays in adapter/workflow contracts for existing agent runtimes.
- **II. One-Click Agent Deployment**: PASS. No new deployment dependency.
- **III. Avoid Vendor Lock-In**: PASS. Codex-specific changes remain in managed-session schema/workflow boundaries.
- **IV. Own Your Data**: PASS. No external storage or SaaS dependency.
- **V. Skills Are First-Class and Easy to Add**: PASS. No skill runtime changes.
- **VI. Replaceable Scaffolding**: PASS. Contracts and tests are strengthened without adding agent cognition.
- **VII. Runtime Configurability**: PASS. No hardcoded runtime selection changes.
- **VIII. Modular and Extensible Architecture**: PASS. Message contracts live in the managed-session schema module.
- **IX. Resilient by Default**: PASS. Epoch validation, idempotency, serialization, and continuation typing improve long-running safety.
- **X. Facilitate Continuous Improvement**: PASS. Tests provide concrete verification evidence.
- **XI. Spec-Driven Development**: PASS. This spec, plan, tasks, and traceability cover the runtime change.
- **XII. Canonical Documentation Separation**: PASS. Runtime work references the canonical desired-state doc without turning it into a migration checklist.
- **XIII. Pre-release Compatibility Policy**: PASS. Active internal contracts are made explicit; legacy replay shim is retained only where in-flight Temporal history requires it.

## Project Structure

### Documentation (this feature)

```text
specs/174-typed-workflow-messages/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── managed-session-message-contract.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/
├── schemas/
│   ├── __init__.py
│   └── managed_session_models.py
└── workflows/temporal/workflows/
    └── agent_session.py

tests/
├── unit/schemas/
│   └── test_managed_session_models.py
├── unit/workflows/temporal/workflows/
│   └── test_agent_session.py
└── integration/services/temporal/workflows/
    └── test_agent_session_lifecycle.py
```

**Structure Decision**: Update existing managed-session schema and workflow modules only; use existing unit and lifecycle integration test locations.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |
