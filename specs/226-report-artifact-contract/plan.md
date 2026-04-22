# Implementation Plan: Report Artifact Contract

**Branch**: `226-report-artifact-contract` | **Date**: 2026-04-22 | **Spec**: [spec.md](spec.md)  
**Input**: Single-story feature specification from `specs/226-report-artifact-contract/spec.md`

**Note**: `.specify/scripts/bash/setup-plan.sh --json` is not used because the managed branch name is not guaranteed to match the numeric feature-branch pattern. This plan follows the same output contract manually using `.specify/feature.json`.

## Summary

MM-460 adds the runtime artifact boundary needed for report-producing workflows to publish explicit report deliverables in the existing artifact system. The repository already supports arbitrary execution artifact link types, metadata storage, latest-by-link lookup, and generic output link types, but it does not define the stable `report.*` contract or reject unsafe/unbounded report metadata. The implementation will add report link-type constants and metadata validation, wire validation into artifact creation and link operations, preserve generic outputs, and verify the behavior through unit and artifact-service integration-style tests.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `TemporalArtifactService.create()` and `link_artifact()` already store and link artifacts | add report-specific validation without adding storage | unit + service integration |
| FR-002 | missing | no report link-type constants or validation exist | define supported report link types | unit |
| FR-003 | missing | arbitrary `report.*` link types currently pass through | reject unsupported report link types | unit + service integration |
| FR-004 | missing | artifact metadata is stored as arbitrary JSON | restrict report metadata keys and value shapes | unit + service integration |
| FR-005 | missing | no secret-like or large inline report metadata guard exists | reject unsafe report metadata before storage/linking | unit + service integration |
| FR-006 | implemented_unverified | generic output link types are used in tests and workload output declarations | add regression proving generic outputs remain accepted | service integration |
| FR-007 | implemented_verified | artifact schema already stores metadata and links in existing tables | no new storage; confirm via diff/final verify | final verify |
| FR-008 | implemented_unverified | `latest_for_execution_link()` already filters by namespace, workflow_id, run_id, link_type | add report-primary latest lookup regression | service integration |
| FR-009 | implemented_verified | source scope excludes provider prompting, PDF, indexing, legal review, and mutable updates | no implementation work | final verify |
| FR-010 | partial | MM-460 preserved in spec and input brief | preserve in plan, tasks, and verification | final verify |
| DESIGN-REQ-001 | partial | existing artifact store is the runtime storage plane | validate reports in existing artifact service | service integration |
| DESIGN-REQ-002 | implemented_unverified | execution artifact linkage and latest lookup exist | add report link tests against existing linkage | service integration |
| DESIGN-REQ-003 | missing | no `report.*` constants | add constants and validation | unit |
| DESIGN-REQ-004 | implemented_unverified | generic outputs exist; no regression specific to report validation | prove generic outputs unaffected | service integration |
| DESIGN-REQ-005 | missing | no bounded report metadata contract | add validator and tests | unit + service integration |
| DESIGN-REQ-009 | implemented_verified | no implementation planned for excluded areas | final verify only | final verify |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2, SQLAlchemy async ORM, existing Temporal artifact service and artifact API schemas  
**Storage**: Existing Temporal artifact and artifact link tables only; no new persistent storage  
**Unit Testing**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh <python test targets>`  
**Integration Testing**: Artifact-service integration-style tests under `tests/unit/workflows/temporal/test_artifacts.py`; optional full hermetic integration via `./tools/test_integration.sh` when Docker is available  
**Target Platform**: MoonMind backend/workers on Linux containers  
**Project Type**: Backend artifact contract and validation feature  
**Performance Goals**: Report metadata validation is bounded by a small allowlist and rejects large inline values before storage  
**Constraints**: No raw secrets in metadata; no new storage plane; generic output flows must remain compatible; report semantics must be explicit; MM-460 traceability must be preserved  
**Scale/Scope**: Seven report link types and one bounded metadata contract for existing artifact create/link paths

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The feature extends the artifact orchestration boundary without changing agent behavior.
- **II. One-Click Agent Deployment**: PASS. No new infrastructure or required external service is added.
- **III. Avoid Vendor Lock-In**: PASS. The report contract is provider-neutral.
- **IV. Own Your Data**: PASS. Reports remain in operator-controlled artifacts.
- **V. Skills Are First-Class and Easy to Add**: PASS. The feature does not alter skill registration.
- **VI. Scientific Method Scaffold**: PASS. Tests are planned before implementation.
- **VII. Runtime Configurability**: PASS. No hardcoded deployment-specific values are introduced.
- **VIII. Modular and Extensible Architecture**: PASS. Validation is isolated behind artifact contract helpers.
- **IX. Resilient by Default**: PASS. The contract fails closed for unsafe report metadata.
- **X. Facilitate Continuous Improvement**: PASS. Explicit report metadata improves downstream observability.
- **XI. Spec-Driven Development**: PASS. This plan follows `specs/226-report-artifact-contract/spec.md`.
- **XII. Documentation Separation**: PASS. Runtime design remains in canonical docs; implementation artifacts stay in `specs/`.
- **XIII. Pre-Release Compatibility Policy**: PASS. The story adds strict report validation without compatibility aliases.

## Project Structure

### Documentation (this feature)

```text
specs/226-report-artifact-contract/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── report-artifact-contract.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/
└── workflows/
    └── temporal/
        ├── artifacts.py
        └── report_artifacts.py

tests/
└── unit/
    └── workflows/
        └── temporal/
            └── test_artifacts.py
```

**Structure Decision**: Implement the report contract inside the backend Temporal artifact layer. Keep report validation separate from persistence code in `report_artifacts.py`, and call it from artifact create/link paths.

## Complexity Tracking

No constitution violations are planned.
