# Implementation Plan: Unify Manifest Route And Navigation

**Branch**: `run-jira-orchestrate-for-mm-418-unify-ma-f2f0f2c8` | **Date**: 2026-04-19 | **Spec**: [spec.md](./spec.md)

## Summary

Unify manifest operations on `/tasks/manifests` by moving the existing manifest run form into the Manifests React entrypoint above recent runs, redirecting the legacy submit route, and removing the extra `Manifest Submit` navigation item. Keep the backend manifest API unchanged and validate behavior with focused router and frontend tests.

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React  
**Primary Dependencies**: FastAPI, Jinja templates, React, TanStack Query, Vitest, pytest  
**Testing**: Focused pytest router tests and focused Vitest entrypoint tests; full `./tools/test_unit.sh` for final verification when practical  
**Constraints**: Preserve existing manifest API semantics; do not add raw secret entry; keep route aliases explicit and observable  
**Scale/Scope**: Single Mission Control page and route/navigation cleanup

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. The change keeps the existing manifest execution model and API.
- **II. One-Click Agent Deployment**: PASS. No new deployment dependency is introduced.
- **III. Avoid Vendor Lock-In**: PASS. No vendor-specific integration changes.
- **IV. Own Your Data**: PASS. Manifest runs continue through MoonMind-owned APIs and local artifacts.
- **V. Skills Are First-Class and Easy to Add**: PASS. No skill contract changes.
- **VI. Evolving Scaffolds**: PASS. Removes an unnecessary UI split without deepening scaffolding.
- **VII. Runtime Configurability**: PASS. Uses existing runtime config and API base.
- **VIII. Modular Architecture**: PASS. Changes are isolated to router, navigation, and one React entrypoint.
- **IX. Resilient by Default**: PASS. Legacy route redirects deterministically and submission errors remain visible.
- **X. Continuous Improvement**: PASS. Adds regression coverage for the simplified flow.
- **XI. Spec-Driven Development**: PASS. Spec, plan, tasks, and verification evidence are recorded.
- **XII. Canonical Documentation Separation**: PASS. Uses existing canonical UI doc and does not add migration narrative to canonical docs.
- **XIII. Pre-Release Compatibility**: PASS. Removes an internal UI route as a legacy redirect rather than preserving two active semantics.

## Project Structure

```text
api_service/api/routers/task_dashboard.py
api_service/templates/_navigation.html
frontend/src/entrypoints/manifests.tsx
frontend/src/entrypoints/mission-control-app.tsx
tests/unit/api/routers/test_task_dashboard.py
frontend/src/entrypoints/manifests.test.tsx
```

## Implementation Strategy

1. Redirect `/tasks/manifests/new` to `/tasks/manifests` with HTTP 307.
2. Remove `Manifest Submit` from the top navigation and dashboard 404 canonical route copy.
3. Merge the manifest submit form into `ManifestsPage`, keep recent runs below it, and refresh the query after successful submission.
4. Remove the now-unused standalone manifest submit entrypoint.
5. Add router and frontend tests for route cleanup, navigation cleanup, unified rendering, and in-place submission refresh.

## Validation

- `./tools/test_unit.sh --python-only --no-xdist tests/unit/api/routers/test_task_dashboard.py`
- `npm run ui:test -- frontend/src/entrypoints/manifests.test.tsx`
- `./tools/test_unit.sh`
