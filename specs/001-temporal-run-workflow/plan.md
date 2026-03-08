# Implementation Plan: Temporal Run Workflow

**Branch**: `001-temporal-run-workflow` | **Date**: 2026-03-08 | **Spec**: `/specs/001-temporal-run-workflow/spec.md`
**Input**: Feature specification from `/specs/001-temporal-run-workflow/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/commands/plan.md` for the execution workflow.

## Summary

Implement the `MoonMind.Run` workflow. Starting a new 'Run' via API creates a real Temporal execution. The history shows phases (initializing, planning, executing, etc.). Terminal success/fail closes with the correct status. Search attributes are visible. Large payloads are offloaded to the artifact store.

## Technical Context

**Language/Version**: Python 3.11
**Primary Dependencies**: Temporal Python SDK, FastAPI, Pydantic
**Storage**: Temporal server (state), Artifact store (MinIO/S3/local for large payloads)
**Testing**: pytest (Python unit tests)
**Target Platform**: Linux server, Docker Compose
**Project Type**: Backend service and Temporal worker
**Performance Goals**: N/A
**Constraints**: Use Temporal RetryPolicies for activities. Offload large payloads.
**Scale/Scope**: Replace local DB state machine with authoritative Temporal workflow.

## Constitution Check

*GATE: Passed Phase 0 research. Re-checked after Phase 1 design.*

- **I. One-Click Agent Deployment**: PASS. Relies on existing Docker Compose Temporal stack.
- **II. Avoid Vendor Lock-In**: PASS. Temporal is open source and self-hostable.
- **V. Replaceability and evolution**: PASS.
- **VI. Powerful Runtime Configurability**: PASS.
- **VII. Modular and Extensible Architecture**: PASS.
- **VIII. Self-Healing by Default**: PASS. Uses Temporal RetryPolicies for activities.
- **X. Spec-Driven Development Is the Source of Truth**: PASS. Aligned with spec.

## Project Structure

### Documentation (this feature)

```text
specs/001-temporal-run-workflow/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   └── requirements-traceability.md
└── tasks.md             # Phase 2 output
```

### Source Code (repository root)

```text
api_service/
├── api/
│   └── routers/
│       └── executions.py
moonmind/
├── workflows/
│   └── temporal/
│       ├── client.py
│       └── workflows/
│           └── run.py
tests/
└── unit/
    └── workflows/
        └── temporal/
            └── test_run.py
```

**Structure Decision**: Using the existing `api_service` and `moonmind` backend structure. Changes are localized to the Temporal workflows, client, and API execution routers.

## Complexity Tracking

No constitution violations detected.
