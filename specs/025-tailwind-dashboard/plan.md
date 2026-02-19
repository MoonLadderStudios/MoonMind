# Implementation Plan: Tailwind Style System Phase 2

**Branch**: `025-tailwind-dashboard` | **Date**: 2026-02-18 | **Spec**: [`spec.md`](./spec.md)  
**Input**: Feature specification from `/specs/025-tailwind-dashboard/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/commands/plan.md` for the execution workflow.

## Summary

Phase 2 modernizes the tasks dashboard stylesheet by replacing the legacy `--bg`/`--accent` tokens with the documented `--mm-*` palette, shifting the entire UI toward MoonMind’s purple/cyan brand direction, and ensuring gradients + status chips align with `docs/TailwindStyleSystem.md`. Implementation keeps the FastAPI + vanilla JS architecture intact; all work occurs inside `dashboard.tailwind.css`/`dashboard.css` plus the style doc. Deliverables include palette tokenization, updated gradients, validation guidance, and regression-safe build/test steps so later Tailwind phases (dark mode, responsive polish) can reuse the same tokens.

## Technical Context

<!--
  ACTION REQUIRED: Replace the content in this section with the technical details
  for the project. The structure here is presented in advisory capacity to guide
  the iteration process.
-->

**Language/Version**: Python 3.11 backend (FastAPI), vanilla ES modules, Tailwind CLI 3.4+, PostCSS 8 for CSS build  
**Primary Dependencies**: FastAPI templating, Tailwind CSS + autoprefixer, npm scripts defined in `package.json`  
**Storage**: N/A (pure static asset + template work)  
**Testing**: `./tools/test_unit.sh` (ensures routers/templates unaffected) + manual visual QA using local FastAPI server  
**Target Platform**: Web UI served from FastAPI on Linux; modern evergreen browsers (Chromium, Firefox, Safari)  
**Project Type**: Web service with static dashboard assets (`api_service/static/task_dashboard`)  
**Performance Goals**: Preserve current dashboard bundle size (<200 KB gzipped) and initial paint time (<300 ms on dev hardware); maintain WCAG AA contrast for interactive elements  
**Constraints**: Keep `/static/task_dashboard/dashboard.css` path + filenames stable, avoid JS/HTML rewrites, minimize layout shifts, no additional frontend frameworks  
**Scale/Scope**: Single dashboard shell + JS renderer; affects consolidated/list/detail routes plus docs referenced by thin-dashboard stakeholders

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

`.specify/memory/constitution.md` is still the default template with no enforceable principles, so there are no explicit gating rules beyond standard testing + scope guards. Marking gate as PASS with the implicit constraint that runtime code (CSS) and docs must ship together.

## Project Structure

### Documentation (this feature)

```text
specs/[###-feature]/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)
<!--
  ACTION REQUIRED: Replace the placeholder tree below with the concrete layout
  for this feature. Delete unused options and expand the chosen structure with
  real paths (e.g., apps/admin, packages/something). The delivered plan must
  not include Option labels.
-->

```text
api_service/
├── templates/
│   └── task_dashboard.html        # HTML shell (unchanged markup references CSS/JS)
├── static/task_dashboard/
│   ├── dashboard.tailwind.css     # Tokenized source input (Phase 1 artifact, editable)
│   ├── dashboard.css              # Generated output shipped to browsers
│   └── dashboard.js               # Vanilla JS renderer consuming semantic classes
docs/
└── TailwindStyleSystem.md         # Design + migration guide

package.json                       # Tailwind/PostCSS devDependencies + scripts
tools/
└── build-dashboard-css.sh         # Optional helper for CI/dev rebuild (if added later)

tests/
└── unit/api/routers/              # Existing FastAPI dashboard route tests
```

**Structure Decision**: Work lives inside the existing FastAPI project (`api_service`). We only touch the static asset directory plus documentation, relying on current npm scripts and FastAPI templates to deliver the rebuilt CSS. No new top-level projects or packages are needed.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

No deviations from the (currently empty) constitution were required, so no complexity waivers are logged for this feature.
