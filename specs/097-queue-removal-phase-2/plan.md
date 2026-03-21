# Implementation Plan: Collapse Dashboard to Single Source

**Branch**: `097-queue-removal-phase-2` | **Date**: 2026-03-21 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/097-queue-removal-phase-2/spec.md`

## Summary

Remove legacy `queue` and `orchestrator` substrate references from the Mission Control dashboard JavaScript and the Python view model, establishing Temporal as the exclusive source of truth for task execution lists and details.

## Technical Context

**Language/Version**: Python 3.12, JavaScript (ES6)
**Primary Dependencies**: FastAPI, Jinja2, Vanilla JS
**Target Platform**: MoonMind API & Mission Control Web UI
**Project Type**: single/web
**Performance Goals**: Decrease dashboard bundle size by eliminating duplicate execution polling functions
**Constraints**: Requires backwards-compatibility with existing Temporal task payloads only
**Scale/Scope**: Impacts dashboard UI exclusively, no database migrations or new dependencies

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. Consolidating around the standardized Temporal interface rather than custom queue polling.
- **III. Avoid Vendor Lock-In**: PASS. Uses standard JSON APIs over HTTP to display task lists.
- **VII. Powerful Runtime Configurability**: PASS. Removing dead code paths does not harm current configuration capability.
- **VIII. Modular Architecture**: PASS. Ensures the dashboard presentation layer is decoupled from underlying legacy queues.
- **IX. Resilient by Default**: PASS. Removes complex Javascript branching and normalizes status checking, improving UI reliability.
- **XI. Spec-Driven**: PASS. Traceable back to SingleSubstrateMigration.md.

## Project Structure

### Documentation (this feature)

```text
specs/097-queue-removal-phase-2/
├── plan.md              # This file
├── contracts/
│   └── requirements-traceability.md
└── tasks.md             # To be generated
```

### Source Code

```text
api_service/
└── api/
    └── routers/
        ├── task_compatibility.py
        └── task_dashboard_view_model.py

web/
└── static/
    └── js/
        └── dashboard.js
```

**Structure Decision**: Code modifications are entirely within existing frontend (`dashboard.js`) and API/view routes (`task_dashboard_view_model.py`, `task_compatibility.py`). No new structural options are needed.
