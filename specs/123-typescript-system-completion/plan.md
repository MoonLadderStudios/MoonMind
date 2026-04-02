# Implementation Plan: typescript-system-completion

**Branch**: `123-typescript-system-completion` | **Date**: 2026-04-02 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/123-typescript-system-completion/spec.md`

## Summary

Finish the Mission Control TypeScript migration by replacing the last legacy routes (`/tasks/new`, `/tasks/create`, `/tasks/manifests/new`, `/tasks/skills`) with React/Vite entrypoints, moving shared Mission Control CSS into the frontend-owned Vite pipeline, deleting the legacy shell and bundle, updating automated tests to target the new frontend, and closing out the documentation tracker.

## Technical Context

**Language/Version**: Python 3.12, TypeScript + React 19
**Primary Dependencies**: FastAPI, Jinja templates, Vite, React, TanStack Query, Tailwind CSS, Vitest, Playwright-style browser tests
**Testing**: `./tools/test_unit.sh`, focused router tests under `tests/unit/api/routers/`, Vitest entrypoint tests, `npm run ui:typecheck`, `npm run ui:lint`, `npm run ui:build`, `npm run ui:verify-manifest`
**Project Type**: Backend-served Mission Control UI with React/Vite page entrypoints
**Constraints**: Server retains route ownership; do not preserve legacy JS aliases in parallel; keep dashboard alerts available on React pages; do not touch unrelated dirty auth-volume scripts

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. The change stays within Mission Control UI delivery, not agent behavior.
- **II. One-Click Agent Deployment**: PASS. The Docker/frontend build remains repo-managed and single-deployment.
- **VIII. Modular and Extensible Architecture**: PASS. Remaining routes become page-level TypeScript entrypoints rather than extending the monolith.
- **IX. Resilient by Default**: PASS. Asset resolution remains strict; React pages still fail loudly on missing bundles.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. This spec tracks the remaining migration work before implementation.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Canonical docs stay desired-state; tmp tracker is updated/closed.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. The plan removes the legacy shell, legacy bundle, and obsolete tests instead of preserving them.

## Scope

### In Scope

- React/Vite entrypoints for task create, manifest submit, and skills
- Router/template cutover for all remaining legacy Mission Control pages
- Frontend-owned shared CSS imported through Vite
- Removal of `task_dashboard.html`, `dashboard.js`, old Tailwind CLI scripts, and stale JS runtime tests
- Docs and migration-tracker updates
- Branch/commit/PR creation

### Out of Scope

- A client-side router takeover
- New Mission Control feature expansion beyond parity for the migrated pages
- Non-UI workflow changes unrelated to the migration

## Structure Decision

- Add entrypoints and small supporting modules under `frontend/src/entrypoints/` and adjacent shared helpers.
- Update `frontend/vite.config.ts` to register the new entrypoints and emit the shared stylesheet via Vite.
- Update `api_service/api/routers/task_dashboard.py` to render React pages only and to include dashboard alerts assets/root for React routes.
- Replace legacy CSS ownership by moving the shared stylesheet into `frontend/src/styles/mission-control.css` and importing it from `mountPage.tsx`.
- Update tests in `tests/unit/api/routers/`, `frontend/src/entrypoints/`, `tests/e2e/`, and `tools/test_unit.sh` to match the React/Vite system.
- Update `README.md`, `docs/UI/TypeScriptSystem.md`, `docs/UI/MissionControlArchitecture.md`, `docs/UI/MissionControlStyleGuide.md`, and `docs/tmp/063-UI-TypeScriptSystem.md`.

## Verification Plan

### Automated Tests

1. Run `./tools/test_unit.sh --python-only --no-xdist tests/unit/api/routers/test_task_dashboard.py tests/api_service/test_ui_assets.py tests/api_service/test_ui_assets_strict.py`.
2. Run `npm run ui:test`.
3. Run `npm run ui:typecheck`.
4. Run `npm run ui:lint`.
5. Run `npm run ui:clean-dist`.
6. Run `npm run ui:build`.
7. Run `npm run ui:verify-manifest`.

### Manual Validation

1. Open `/tasks/new` and submit a task successfully and with an error response.
2. Confirm runtime changes update provider-profile options on the create page.
3. Confirm large instructions create/upload/link an artifact before execution submit.
4. Open `/tasks/manifests/new` and `/tasks/skills` and verify the React pages render and function.
