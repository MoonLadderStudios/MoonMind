# Implementation Plan: Report Artifact Contract

**Branch**: `244-define-report-artifact-contract` | **Date**: 2026-04-23 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `specs/244-define-report-artifact-contract/spec.md`

## Summary

Plan MM-492 as a verification-first report contract story. The current repository already implements most of the contract in `moonmind/workflows/temporal/report_artifacts.py`, activity/service publication helpers in `moonmind/workflows/temporal/artifacts.py`, UI consumption in `frontend/src/entrypoints/task-detail.tsx`, and focused unit/contract tests. The plan is therefore to preserve the existing desired behavior in planning artifacts, keep unit and integration strategies explicit, and carry an implementation contingency only if later task generation or verification exposes drift between the current code and the MM-492 specification.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `REPORT_ARTIFACT_LINK_TYPES`; `validate_report_artifact_contract`; report link tests in `tests/unit/workflows/temporal/test_artifacts.py` | no new implementation | unit only if later verification finds drift |
| FR-002 | implemented_verified | report validation runs in existing artifact create/write paths in `moonmind/workflows/temporal/artifacts.py` | no new implementation | unit + final verify |
| FR-003 | implemented_verified | `REPORT_ARTIFACT_LINK_TYPES` includes primary/summary/structured/evidence/appendix/findings_index/export | no new implementation | unit + final verify |
| FR-004 | implemented_verified | `GENERIC_OUTPUT_LINK_TYPES`; fallback validation in `classify_report_rollout_artifacts` and `validate_report_workflow_artifact_classes` | no new implementation | unit + final verify |
| FR-005 | implemented_verified | `build_report_bundle_result`; `validate_report_bundle_result`; bundle tests in `tests/unit/workflows/temporal/test_artifacts.py` | no new implementation | unit |
| FR-006 | implemented_verified | unsafe bundle key/value validation in `validate_report_bundle_result`; rejection tests cover inline body and raw URL | no new implementation | unit |
| FR-007 | implemented_verified | `REPORT_METADATA_KEYS`; `_validate_report_metadata`; unsafe metadata tests in `tests/unit/workflows/temporal/test_artifacts.py` | no new implementation | unit |
| FR-008 | implemented_verified | `classify_report_rollout_artifacts`; latest `report.primary` API/query usage in backend and `frontend/src/entrypoints/task-detail.tsx` | no new implementation | unit + contract |
| FR-009 | implemented_verified | report workflow mappings separate report and observability link types; task detail keeps related report content distinct | no new implementation | unit + frontend unit |
| FR-010 | implemented_unverified | canonical terminology exists in `docs/Artifacts/ReportArtifacts.md` and report helper docstrings, but no dedicated contract artifact exists yet in this feature directory | add planning contract/data-model artifacts and verify traceability; implementation only if later verify finds missing runtime-facing terminology | none beyond final verify |
| FR-011 | partial | MM-492 is preserved in `spec.md` and Jira orchestration input | preserve MM-492 through plan, tasks, verification, and any later implementation artifacts | traceability check |
| SC-001 | implemented_verified | report link classification and generic fallback tests exist in unit suites | no new implementation | unit |
| SC-002 | implemented_verified | bundle validation tests reject inline payloads and unsafe keys | no new implementation | unit |
| SC-003 | implemented_verified | metadata validation tests reject unsafe keys/values and oversized payloads | no new implementation | unit |
| SC-004 | implemented_verified | latest `report.primary` contract tests and report-first UI tests exist | no new implementation | unit + contract + frontend unit |
| SC-005 | implemented_verified | rollout mapping tests and task-detail behavior keep report/evidence/observability separated | no new implementation | unit + frontend unit |
| SC-006 | partial | traceability exists in Jira brief and spec but not yet in downstream plan/tasks/verification artifacts | preserve traceability in remaining MoonSpec artifacts | traceability check |
| DESIGN-REQ-001 | implemented_verified | report contract reuses existing artifact store/service | no new implementation | final verify |
| DESIGN-REQ-002 | implemented_verified | stable `report.*` link constants and validation exist | no new implementation | unit |
| DESIGN-REQ-003 | implemented_verified | generic output fallback validation exists | no new implementation | unit |
| DESIGN-REQ-004 | implemented_verified | compact `report_bundle_v = 1` result helpers exist | no new implementation | unit |
| DESIGN-REQ-007 | implemented_verified | standardized report metadata key set exists | no new implementation | unit |
| DESIGN-REQ-008 | implemented_verified | metadata sanitization rejects unsafe values | no new implementation | unit |
| DESIGN-REQ-009 | implemented_unverified | terminology is present in docs and code comments but not yet preserved in feature-local contract artifacts | preserve in `data-model.md` and `contracts/` | final verify |
| DESIGN-REQ-010 | implemented_verified | canonical report resolution is server/link-driven, with tests for latest `report.primary` lookup and UI consumption | no new implementation | contract + frontend unit |
| DESIGN-REQ-011 | implemented_verified | rollout mapping and task-detail behavior preserve report/evidence/observability separation | no new implementation | unit + frontend unit |

## Technical Context

**Language/Version**: Python 3.12 backend/runtime plus existing TypeScript/React Mission Control consumer surfaces  
**Primary Dependencies**: Existing Temporal artifact service/helpers, Pydantic v2 patterns already in repo, React/TanStack Query consumer path, pytest  
**Storage**: Existing artifact metadata tables and configured artifact store only; no new persistent storage  
**Unit Testing**: `./tools/test_unit.sh`; focused iteration can use targeted pytest paths and dashboard UI args  
**Integration Testing**: `./tools/test_integration.sh`; contract regression can also use `./tools/test_unit.sh tests/contract/test_temporal_artifact_api.py` for read-model/API coverage  
**Target Platform**: MoonMind Temporal worker and API service runtime with Mission Control consuming report artifacts  
**Project Type**: Python backend/runtime contract with existing frontend consumer coverage  
**Performance Goals**: Keep report payloads compact and ref-based; validation remains deterministic in-memory checks over bounded metadata  
**Constraints**: No separate report storage system; no inline report/evidence/log payloads in workflow-facing bundle data; keep generic outputs distinct from report outputs; preserve MM-492 traceability  
**Scale/Scope**: One contract story covering report link semantics, bundle/result shape, metadata validation, canonical resolution semantics, and report/evidence/observability separation

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS - uses existing artifact and presentation boundaries rather than inventing a parallel report engine.
- II. One-Click Agent Deployment: PASS - no new services, secrets, or operator prerequisites.
- III. Avoid Vendor Lock-In: PASS - report semantics are provider-neutral artifact contracts.
- IV. Own Your Data: PASS - report bytes and evidence remain in operator-controlled artifact storage.
- V. Skills Are First-Class and Easy to Add: PASS - no changes to skill/runtime loading.
- VI. Replaceable AI Scaffolding: PASS - emphasizes compact contracts and verification over scaffolding.
- VII. Powerful Runtime Configurability: PASS - no new hardcoded external dependencies or runtime switches.
- VIII. Modular and Extensible Architecture: PASS - behavior stays within existing artifact/report modules and consumers.
- IX. Resilient by Default: PASS - workflow-facing report results remain bounded and deterministic.
- X. Facilitate Continuous Improvement: PASS - planning artifacts make current evidence and remaining verification explicit.
- XI. Spec-Driven Development: PASS - this plan follows the new single-story MM-492 spec.
- XII. Canonical Documentation Separation: PASS - migration/orchestration notes remain in `docs/tmp`, while canonical behavior remains in `docs/Artifacts/ReportArtifacts.md`.
- XIII. Pre-release Compatibility Policy: PASS - no compatibility aliasing or hidden semantic transforms are introduced.

## Project Structure

### Documentation (this feature)

```text
specs/244-define-report-artifact-contract/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── report-artifact-contract.md
└── checklists/
    └── requirements.md
```

### Source Code (repository root)

```text
moonmind/workflows/temporal/
├── report_artifacts.py
└── artifacts.py

frontend/src/entrypoints/
└── task-detail.tsx

tests/unit/workflows/temporal/
├── test_artifacts.py
├── test_artifacts_activities.py
└── test_report_workflow_rollout.py

tests/contract/
└── test_temporal_artifact_api.py
```

**Structure Decision**: Keep MM-492 scoped to the existing report artifact contract helpers, artifact publication service, and current report-consuming UI/API paths. The feature-local design artifacts document the contract; implementation proceeds only if later verification shows the current runtime deviates from the spec.

## Complexity Tracking

No constitution violations.
