# Implementation Plan: Tailwind Style System Phase 3 Dark Mode

**Branch**: `026-tailwind-dark-mode` | **Date**: 2026-02-19 | **Spec**: [`spec.md`](./spec.md)
**Input**: Feature specification from `/specs/026-tailwind-dark-mode/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/plan-template.md` for the execution workflow.

## Summary

Phase 3 delivers runtime dark mode for the task dashboard by adding a token-driven dark palette, a user-visible theme toggle, preference persistence, and no-flash first paint behavior while keeping existing FastAPI routing and semantic UI classes intact. Implementation is constrained to dashboard template/JS/CSS assets and generated CSS output. Validation focuses on persistence precedence (user over system), first-render correctness, and readability across tables/forms/live output while preserving purple-primary accent hierarchy with restrained warm highlights.

## Technical Context

**Language/Version**: Python 3.11 backend (FastAPI), vanilla ES modules for dashboard runtime, Tailwind CLI 3.4+, PostCSS 8  
**Primary Dependencies**: FastAPI templating, Tailwind CSS + autoprefixer, browser `localStorage` and `matchMedia` APIs, npm dashboard build scripts  
**Storage**: Browser-local preference state (`moonmind.theme` localStorage key); no server-side persistence  
**Testing**: `./tools/test_unit.sh`, `npm run dashboard:css:min`, manual visual QA across dashboard routes (light/dark/system preference)  
**Target Platform**: FastAPI-served web dashboard on Linux with modern Chromium/Firefox/Safari-class browsers  
**Project Type**: Web service with static frontend assets under `api_service/static/task_dashboard`  
**Performance Goals**: Eliminate visible theme flash on first paint, keep dashboard CSS generation deterministic, preserve current interaction responsiveness during theme toggling  
**Constraints**: Keep `/static/task_dashboard/dashboard.css` as served artifact, avoid framework rewrites, preserve existing semantic class/route behavior, avoid unrelated Phase 4/5 scope  
**Scale/Scope**: Single dashboard shell and renderer (`/tasks` routes), token and interaction updates scoped to dark-mode Phase 3 requirements

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- Initial Gate: PASS. `.specify/memory/constitution.md` is still an unfilled template and contains no enforceable constraints.
- Operational Gate Applied: Runtime deliverables are mandatory because intent is implementation (not docs-only).
- Document-Backed Gate Applied: `DOC-REQ-001` through `DOC-REQ-010` are all mapped and will be tracked in `contracts/requirements-traceability.md` with implementation surfaces and validation strategy.
- Post-Design Re-check: PASS. Phase 0/1 artifacts include complete traceability with no unmapped `DOC-REQ-*` requirements.

## Project Structure

### Documentation (this feature)

```text
specs/026-tailwind-dark-mode/
├── plan.md                           # This file
├── research.md                       # Phase 0 decisions
├── data-model.md                     # Phase 1 domain model
├── quickstart.md                     # Phase 1 validation flow
├── contracts/
│   ├── theme-runtime-contract.md
│   └── requirements-traceability.md
└── tasks.md                          # Generated later by /speckit.tasks
```

### Source Code (repository root)

```text
api_service/
├── templates/
│   └── task_dashboard.html           # Add viewport-fit + theme toggle + no-flash bootstrap script
├── static/task_dashboard/
│   ├── dashboard.js                  # Theme state initialization, persistence, system-preference listener
│   ├── dashboard.tailwind.css        # Dark token overrides and dark-mode surface refinements
│   └── dashboard.css                 # Generated output via Tailwind CLI

docs/
└── TailwindStyleSystem.md            # Source contract (already updated for Phase 3)

tests/
├── unit/api/routers/test_task_dashboard.py
└── task_dashboard/                   # Optional frontend runtime smoke tests if introduced
```

**Structure Decision**: Keep all runtime implementation inside existing dashboard template/JS/CSS files and regenerate `dashboard.css`. No API contract expansion or backend service-layer changes are planned for Phase 3.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

No constitution violations were identified; no complexity waivers are required.
