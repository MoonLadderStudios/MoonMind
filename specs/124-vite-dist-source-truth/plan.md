# Implementation Plan: vite-dist-source-truth

**Branch**: `124-vite-dist-source-truth` | **Date**: 2026-04-03 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/124-vite-dist-source-truth/spec.md`

## Summary

Remove the remaining checked-in-`dist` workflow from MoonMind by deleting the sync action and tracked bundle tree, separating tracked API-type generation from runtime frontend bundle builds, updating CI/docs to use build-from-source verification, and fixing tests that implicitly depended on committed frontend output.

## Technical Context

**Language/Version**: JSON/npm scripts, GitHub Actions YAML, Markdown docs, Python 3.12 tests  
**Primary Dependencies**: Vite, FastAPI asset manifest loader, GitHub Actions, pytest  
**Testing**: `./tools/test_unit.sh --python-only --no-xdist tests/tools/test_verify_vite_manifest.py tests/api_service/test_ui_assets.py tests/api_service/test_ui_assets_strict.py`, `npm run generate:check`, `npm run ci:test`  
**Project Type**: Backend-served web application with source-built frontend bundles  
**Constraints**: Keep manifest verification strict, keep Docker source builds intact, do not reintroduce committed `dist/` convenience flows

## Constitution Check

- **II. One-Click Agent Deployment**: PASS. Docker remains the canonical deployment path and continues building the UI from source.
- **VI. Design for Deletion / Thin Scaffolding**: PASS. The change removes transitional glue instead of extending it.
- **VII. Powerful Runtime Configurability**: PASS. Asset resolution behavior remains explicit and unchanged at runtime.
- **VIII. Modular and Extensible Architecture**: PASS. The change tightens ownership boundaries between tracked generated files and build output.
- **IX. Resilient by Default**: PASS. Manifest verification and strict runtime failures remain in place after the cleanup.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. This spec tracks the repo-wide simplification before implementation.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Canonical docs will describe the new steady-state, and transient implementation notes stay in tmp docs only when needed.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. The old sync workflow and tracked bundle tree are removed rather than kept as compatibility baggage.

## Scope

### In Scope

- Package script cleanup for frontend build vs checked-in generation
- GitHub Actions updates removing `dist/` diff gating
- Removal of tracked `api_service/static/task_dashboard/dist/` output and sync workflow
- Documentation updates for the new source-of-truth model
- Test updates removing reliance on committed `dist/`

### Out of Scope

- Adding real FastAPI-to-Vite dev-server injection
- Restructuring Vite entrypoints or route ownership
- Broader frontend CI deduplication beyond what this cleanup requires

## Structure Decision

- Keep `frontend/vite.config.ts` and `api_service/ui_assets.py` unchanged as the runtime build/serve contract.
- Update `package.json` so `generate` covers only tracked API-type generation while a dedicated build-check script covers clean Vite builds plus manifest verification.
- Update `.github/workflows/pytest-unit-tests.yml` to keep build-from-source verification and artifact upload without any `dist/` git-diff gate.
- Update repo docs to state plainly that `dist/` is untracked generated output.
- Replace the repository-level manifest script regression test with a synthetic-repo fixture so backend unit tests do not require committed bundles.

## Verification Plan

1. Run `./tools/test_unit.sh --python-only --no-xdist tests/tools/test_verify_vite_manifest.py tests/api_service/test_ui_assets.py tests/api_service/test_ui_assets_strict.py`.
2. Run `npm run generate:check`.
3. Run `npm run ci:test`.
4. Confirm `git ls-files api_service/static/task_dashboard/dist` returns no tracked files.
