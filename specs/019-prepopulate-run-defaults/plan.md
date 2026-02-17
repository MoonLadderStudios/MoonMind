# Implementation Plan: Dashboard Queue Task Default Pre-Population

**Branch**: `019-prepopulate-run-defaults` | **Date**: 2026-02-17 | **Spec**: `specs/019-prepopulate-run-defaults/spec.md`  
**Input**: Feature specification from `/specs/019-prepopulate-run-defaults/spec.md`

## Summary

Add settings-backed queue task defaults for runtime, model, effort, and repository, then expose those defaults to the dashboard queue submit page so fields are pre-populated and editable. Ensure backend task creation resolves omitted fields using settings defaults so submissions remain valid even when inputs are blank.

## Technical Context

**Language/Version**: Python 3.11 (backend), vanilla JavaScript (dashboard UI)  
**Primary Dependencies**: FastAPI routers, MoonMind `settings`, Agent Queue service/task contract, dashboard static JS  
**Storage**: Existing settings/env configuration and existing queue job persistence (no new DB tables)  
**Testing**: Unit tests via `./tools/test_unit.sh`  
**Target Platform**: MoonMind Docker Compose deployment and browser-based dashboard  
**Project Type**: Web application served by existing backend (`api_service`) plus workflow runtime modules (`moonmind`)  
**Performance Goals**: Dashboard form fields pre-populate on initial render with no extra network round trip; default resolution adds negligible overhead to queue submission path  
**Constraints**: Preserve existing queue payload contract compatibility, keep fields user-editable, avoid secret leakage, and maintain runtime support for codex/gemini/claude  
**Scale/Scope**: Queue submit UX and task creation normalization path only; no orchestrator behavior changes

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- `.specify/memory/constitution.md` currently contains placeholders and no enforceable MUST/SHOULD clauses.
- Repository governance constraints from `AGENTS.md` apply:
  - next global spec id used (`019`),
  - unit tests run through `./tools/test_unit.sh`.

**Gate Status**: PASS WITH NOTE.

## Project Structure

### Documentation (this feature)

```text
specs/019-prepopulate-run-defaults/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── queue-defaults-contract.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/
├── config/settings.py                                # settings-backed defaults
└── workflows/agent_queue/
    ├── service.py                                    # backend default resolution for task jobs
    └── task_contract.py                              # canonical normalization behavior (validation compatibility)

api_service/
├── api/routers/task_dashboard_view_model.py          # dashboard runtime config defaults
└── static/task_dashboard/dashboard.js                # queue submit form pre-population

tests/unit/config/test_settings.py                    # settings defaults/override tests
tests/unit/api/routers/test_task_dashboard_view_model.py
                                                    # dashboard config default tests
tests/unit/workflows/agent_queue/test_service_hardening.py
                                                    # queue task default-resolution tests
```

**Structure Decision**: Extend existing API + workflow modules to keep default resolution centralized in settings and queue service, while using current dashboard view-model injection for pre-populated inputs.

## Phase 0: Research Plan

1. Confirm current dashboard form population behavior and identify where runtime/model/effort/repository defaults are injected.
2. Validate queue service normalization path for `type=task` submissions to determine default injection point.
3. Evaluate existing settings fields and env aliases for codex model/effort/repository defaults.
4. Define backward-compatible defaulting rules that do not break explicit user overrides.

## Phase 1: Design Outputs

- `research.md`: selected defaulting strategy and alternatives.
- `data-model.md`: settings/default resolution model for dashboard + queue payload.
- `contracts/queue-defaults-contract.md`: runtime config and queue submission default contract.
- `quickstart.md`: manual validation and test workflow.

## Post-Design Constitution Re-check

- Design includes runtime production changes and explicit validation tests.
- No additional constitution violations found beyond placeholder constitution baseline.

**Gate Status**: PASS.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |
