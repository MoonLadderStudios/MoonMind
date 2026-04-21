# Implementation Plan: Run Manifest Page Form

**Branch**: `216-run-manifest-page-form` | **Date**: 2026-04-21 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `/specs/216-run-manifest-page-form/spec.md`

## Summary

Deliver MM-419 by validating and hardening the existing Manifests-page Run Manifest form. Repo analysis shows the unified page, registry/inline modes, in-place refresh, accessible labels, and collapsed advanced options already exist from the related MM-418 work. The remaining runtime gaps were client-side rejection of invalid `max docs` values and raw secret-shaped values before manifest upsert or run requests; those gaps are now implemented in `frontend/src/entrypoints/manifests.tsx` and covered by focused frontend tests in `frontend/src/entrypoints/manifests.test.tsx`.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 / SCN-001 / DESIGN-REQ-001 | implemented_verified | `frontend/src/entrypoints/manifests.tsx`, `frontend/src/entrypoints/manifests.test.tsx` | preserve existing behavior | final validation |
| FR-002 / SCN-002 | implemented_verified | registry mode field and test coverage in `frontend/src/entrypoints/manifests.test.tsx` | preserve existing behavior | final validation |
| FR-003 / SCN-003 | implemented_verified | inline mode field and test coverage in `frontend/src/entrypoints/manifests.test.tsx` | preserve existing behavior | final validation |
| FR-004 / SCN-004 / DESIGN-REQ-002 | implemented_verified | separate registry/inline state and mode-switch preservation test | preserve existing behavior | final validation |
| FR-005 / SCN-005 / DESIGN-REQ-004 | implemented_verified | `frontend/src/entrypoints/manifests.tsx` rejects invalid max docs before side effects; `frontend/src/entrypoints/manifests.test.tsx` verifies no manifest API call is made | preserve existing behavior | final validation |
| FR-006 / SCN-006 / DESIGN-REQ-004 | implemented_verified | `frontend/src/entrypoints/manifests.tsx` rejects raw secret-shaped values before side effects; `frontend/src/entrypoints/manifests.test.tsx` verifies rejection and allowed env-style references | preserve existing behavior | final validation |
| FR-007 / DESIGN-REQ-005 | implemented_verified | valid submit tests assert API calls and in-place refresh | preserve existing behavior | final validation |
| FR-008 / SCN-007 / DESIGN-REQ-006 | implemented_verified | controls are labels/selects/buttons reachable by role or label in tests | preserve existing behavior | final validation |
| FR-009 / SCN-008 / DESIGN-REQ-007 | implemented_verified | Run Manifest button is outside collapsed advanced options | preserve existing behavior | final validation |
| FR-010 | implemented_verified | MM-419 and the original preset brief are preserved in `spec.md`; implementation and tests remain tied to `specs/216-run-manifest-page-form/` | preserve in final report and any commit/PR metadata | final validation |

## Technical Context

**Language/Version**: TypeScript/React for Mission Control UI; Python 3.12 remains present but is not expected for runtime changes
**Primary Dependencies**: React, TanStack Query, existing manifest REST endpoints, Vitest, Testing Library
**Storage**: Existing manifest registry and execution records only; no new persistent storage
**Unit Testing**: `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/manifests.test.tsx`
**Integration Testing**: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/manifests.test.tsx` for runner-integrated frontend validation; full `./tools/test_unit.sh` before finalization when practical
**Target Platform**: Mission Control web UI at `/tasks/manifests`
**Project Type**: Web application frontend with existing FastAPI-backed runtime endpoints
**Performance Goals**: Validation must run synchronously before submit without visible delay
**Constraints**: Preserve existing manifest backend semantics; do not add raw secret entry; do not add unsupported priority fields; keep MM-419 traceability
**Scale/Scope**: One existing React entrypoint and focused frontend tests

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. Uses existing manifest execution APIs and orchestration.
- **II. One-Click Agent Deployment**: PASS. No new service or deployment dependency.
- **III. Avoid Vendor Lock-In**: PASS. No vendor-specific coupling.
- **IV. Own Your Data**: PASS. Manifest data remains in MoonMind-managed storage and execution records.
- **V. Skills Are First-Class and Easy to Add**: PASS. No skill contract changes.
- **VI. Evolving Scaffolds**: PASS. Hardens the existing UI without adding a parallel flow.
- **VII. Runtime Configurability**: PASS. Uses existing runtime endpoint configuration.
- **VIII. Modular Architecture**: PASS. Changes stay within the Manifests UI boundary.
- **IX. Resilient by Default**: PASS. Invalid input fails before side effects.
- **X. Continuous Improvement**: PASS. Adds regression tests for prior gaps.
- **XI. Spec-Driven Development**: PASS. Spec, plan, tasks, and verification are maintained under `specs/216-run-manifest-page-form/`.
- **XII. Canonical Documentation Separation**: PASS. Existing canonical UI doc remains desired-state; temporary orchestration artifacts stay under `docs/tmp`.
- **XIII. Pre-Release Compatibility**: PASS. No compatibility shim or legacy duplicate behavior is introduced.

## Project Structure

### Documentation (this feature)

```text
specs/216-run-manifest-page-form/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── ui-contract.md
└── tasks.md
```

### Source Code (repository root)

```text
frontend/src/entrypoints/
├── manifests.tsx
└── manifests.test.tsx

docs/tmp/jira-orchestration-inputs/
└── MM-419-moonspec-orchestration-input.md
```

**Structure Decision**: This is a frontend validation hardening story for an existing page; production and test work remain in the existing Manifests entrypoint and its colocated Vitest suite.

## Complexity Tracking

No constitution violations.
