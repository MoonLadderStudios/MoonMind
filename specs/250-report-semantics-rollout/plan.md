# Implementation Plan: Report Semantics Rollout

**Branch**: `250-report-semantics-rollout` | **Date**: 2026-04-24 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `specs/250-report-semantics-rollout/spec.md`

## Summary

Plan MM-497 as a verification-first runtime alignment story for the existing report-artifact rollout. The current repository already distinguishes generic `output.primary` behavior from explicit `report.*` semantics, ships representative report mappings for unit-test, coverage, pentest, and benchmark-style workflows, and surfaces canonical report behavior through existing API and Mission Control paths. The implementation plan is therefore to preserve MM-497 traceability, verify the existing rollout boundary and representative mappings with focused unit, contract, and UI evidence, and treat any code change as a contingency only if verification exposes drift from the story.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `docs/Artifacts/ReportArtifacts.md` states generic outputs continue using `output.primary`; `tests/unit/workflows/temporal/test_report_workflow_rollout.py` verifies report workflows cannot treat `output.primary` as the canonical report. | Preserve feature-local traceability and rerun focused verification. | unit |
| FR-002 | implemented_verified | `moonmind/workflows/temporal/report_artifacts.py` defines explicit `report.*` link-type sets and representative report types; rollout validation tests cover explicit report-only semantics. | Preserve the explicit report-semantics contract in feature-local artifacts; no new implementation planned unless verification fails. | unit |
| FR-003 | implemented_unverified | `docs/Artifacts/ReportArtifacts.md` defines the non-flag-day migration path; `api_service/api/routers/executions.py`, `frontend/src/entrypoints/task-detail.tsx`, and related tests already consume canonical report behavior. | Verify that the current repo still matches the staged rollout boundary and generic-output fallback behavior. | unit + contract + UI |
| FR-004 | implemented_verified | `docs/Artifacts/ReportArtifacts.md` sections 2, 5, and 20 keep PDF rendering, provider-native parsing, legal review, separate report storage, and mutable report updates out of scope for the rollout. | Preserve non-goals in downstream tasks and verification; no code change planned. | traceability review |
| FR-005 | implemented_verified | `moonmind/workflows/temporal/report_artifacts.py` defines representative mappings for `unit_test_report`, `coverage_report`, `security_pentest_report`, and `benchmark_report`; `tests/unit/workflows/temporal/test_report_workflow_rollout.py` covers them. | Re-verify representative mappings and preserve them in feature-local artifacts. | unit |
| FR-006 | partial | `docs/Artifacts/ReportArtifacts.md` preserves open questions, but MM-497-specific plan, tasks, and verification artifacts do not yet carry those deferred decisions. | Record open questions explicitly in plan, tasks, and later verification artifacts. | traceability review |
| FR-007 | partial | `spec.md` and `docs/tmp/jira-orchestration-inputs/MM-497-moonspec-orchestration-input.md` preserve MM-497, but downstream planning and later verification artifacts were missing at planning start. | Preserve MM-497 across plan, tasks, and verification artifacts. | traceability review |
| DESIGN-REQ-021 | implemented_verified | `docs/Artifacts/ReportArtifacts.md` sections 2, 17, 19, and 21 plus rollout validation tests show generic outputs remain valid while explicit report workflows use canonical report semantics. | Focused verification only. | unit + contract + UI |
| DESIGN-REQ-023 | implemented_unverified | Source documentation preserves explicit non-goals and migration boundaries, but MM-497-specific downstream artifacts were missing. | Preserve explicit non-goals in all downstream feature artifacts; implementation contingency only if verification shows runtime drift. | traceability review |
| DESIGN-REQ-024 | implemented_unverified | Representative mappings and open-question handling exist in source docs and runtime helpers, but MM-497-specific research/plan/tasks/verification evidence was missing. | Add feature-local design artifacts and verify mappings end to end. | unit + traceability review |

## Technical Context

**Language/Version**: Python 3.12 backend/runtime and existing TypeScript/React Mission Control consumers  
**Primary Dependencies**: FastAPI, Pydantic v2, existing Temporal artifact/report helpers, React/Vitest for Mission Control task detail  
**Storage**: Existing temporal artifact metadata tables and configured artifact store; no new persistent storage  
**Unit Testing**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_report_workflow_rollout.py tests/unit/workflows/temporal/test_artifacts.py tests/unit/api/routers/test_executions.py --ui-args frontend/src/entrypoints/task-detail.test.tsx`  
**Integration Testing**: `./tools/test_integration.sh` if verification reveals drift that requires changes to artifact persistence, report publication boundaries, or execution-detail/artifact serialization across the compose-backed stack  
**Target Platform**: MoonMind Temporal artifact/runtime layer, execution detail API, and Mission Control task detail report presentation  
**Project Type**: Backend/runtime contract and UI-consumer verification story  
**Performance Goals**: Reuse existing bounded report helpers and artifact-backed report flows without introducing new storage, unbounded payloads, or extra lookup passes  
**Constraints**: Preserve generic-output compatibility, keep explicit `report.*` semantics for new report workflows, preserve documented non-goals and deferred decisions, keep MM-497 traceability intact  
**Scale/Scope**: One story covering report rollout semantics, representative mappings, and verification-first gap analysis

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS - planning reuses the existing report-artifact runtime, execution API, and Mission Control paths.
- II. One-Click Agent Deployment: PASS - no new service, secret, or setup step is introduced.
- III. Avoid Vendor Lock-In: PASS - report semantics stay artifact-backed and provider-agnostic.
- IV. Own Your Data: PASS - reports remain in the existing MoonMind artifact system rather than a new external store.
- V. Skills Are First-Class and Easy to Add: PASS - no skill-runtime contract change is required.
- VI. Replaceable AI Scaffolding: PASS - the work centers on stable artifact/report contracts and verification evidence.
- VII. Powerful Runtime Configurability: PASS - no new runtime configuration is introduced.
- VIII. Modular and Extensible Architecture: PASS - the story stays within existing report-artifact, execution-detail, and Mission Control boundaries.
- IX. Resilient by Default: PASS - rollout behavior remains bounded, artifact-backed, and deterministic.
- X. Facilitate Continuous Improvement: PASS - planning preserves deferred questions and verification contingencies explicitly.
- XI. Spec-Driven Development: PASS - MM-497 is preserved from Jira brief through `spec.md` and this plan.
- XII. Canonical Documentation Separation: PASS - source design remains under `docs/`, while feature-local planning artifacts stay in `specs/250-report-semantics-rollout`.
- XIII. Pre-release Compatibility Policy: PASS - no compatibility alias or hidden semantic transform is introduced.

## Project Structure

### Documentation (this feature)

```text
specs/250-report-semantics-rollout/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── report-rollout-semantics-contract.md
└── tasks.md
```

### Source Code (repository root)

```text
docs/
└── Artifacts/ReportArtifacts.md

moonmind/workflows/temporal/
├── artifacts.py
└── report_artifacts.py

api_service/api/routers/
└── executions.py

frontend/src/entrypoints/
├── task-detail.tsx
└── task-detail.test.tsx

tests/
├── contract/test_temporal_execution_api.py
├── unit/api/routers/test_executions.py
└── unit/workflows/temporal/
    ├── test_artifacts.py
    └── test_report_workflow_rollout.py
```

**Structure Decision**: Keep MM-497 bounded to the existing report rollout design, runtime validation helpers, execution detail consumption, and Mission Control report surfacing. This story does not introduce a new report subsystem; it verifies and preserves the current staged rollout contract.

## Complexity Tracking

No constitution violations.
