# Implementation Plan: Apply Report Access and Lifecycle Policy

**Branch**: `231-sensitive-report-access-retention` | **Date**: 2026-04-24 | **Spec**: [spec.md](spec.md)  
**Input**: Single-story feature specification from `specs/231-sensitive-report-access-retention/spec.md`

## Summary

Resume the existing runtime story in `specs/231-sensitive-report-access-retention` using MM-495 as the canonical Jira input. Existing production code and test evidence already cover the required runtime behavior: report metadata validation, preview/default-read behavior, report-aware retention defaults, unpin restoration, and non-cascading deletion. The remaining work for this request is MoonSpec artifact alignment so the spec, plan, tasks, and verification chain preserve MM-495 and the newer source design IDs DESIGN-REQ-011, DESIGN-REQ-017, and DESIGN-REQ-018.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | verified | Existing artifact read/raw access boundaries plus report authorization tests. | Preserve MM-495 traceability in artifacts. | unit |
| FR-002 | verified | `get_read_policy` preview/default-read behavior plus restricted report tests. | Preserve MM-495 traceability in artifacts. | unit |
| FR-003 | verified | `validate_report_artifact_contract` and report metadata validation tests reject unsafe or oversized metadata. | Preserve MM-495 traceability in artifacts. | unit |
| FR-004 | verified | Raw presign/read paths enforce restricted raw access. | Preserve MM-495 traceability in artifacts. | unit |
| FR-005 | verified | `_derive_retention` maps `report.primary`, `report.summary`, `report.appendix`, `report.findings_index`, and `report.export` to `long`. | Preserve MM-495 traceability in artifacts. | unit |
| FR-006 | verified | `_derive_retention` keeps `report.structured` and `report.evidence` non-observability retention and honors explicit overrides. | Preserve MM-495 traceability in artifacts. | unit |
| FR-007 | verified | `TemporalArtifactService.unpin` restores report-derived retention. | Preserve MM-495 traceability in artifacts. | unit |
| FR-008 | verified | Existing artifact lifecycle path performs soft/hard deletion. | Preserve MM-495 traceability in artifacts. | integration_ci |
| FR-009 | verified | Integration coverage proves report deletion does not mutate unrelated observability artifacts. | Preserve MM-495 traceability in artifacts. | integration_ci |
| FR-010 | verified | MM-495 is preserved across the orchestration input and aligned downstream MoonSpec artifacts. | Keep traceability verification current. | traceability |
| DESIGN-REQ-011 | verified | Report metadata validation and bounded metadata tests. | Keep mapped evidence in verification. | unit |
| DESIGN-REQ-017 | verified | Existing authorization plus preview/default-read behavior. | Keep mapped evidence in verification. | unit |
| DESIGN-REQ-018 | verified | Existing retention, pin/unpin, and lifecycle coverage. | Keep mapped evidence in verification. | unit + integration_ci |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: SQLAlchemy async ORM, Pydantic v2 models, existing Temporal artifact service and report artifact contract helpers  
**Storage**: Existing temporal artifact tables and artifact store only; no new persistent storage  
**Unit Testing**: `./tools/test_unit.sh`; focused evidence exists in `tests/unit/workflows/temporal/test_artifacts.py` and `tests/unit/workflows/temporal/test_artifact_authorization.py`  
**Integration Testing**: `./tools/test_integration.sh`; focused evidence exists in `tests/integration/temporal/test_temporal_artifact_lifecycle.py`  
**Target Platform**: MoonMind API/Temporal artifact service runtime  
**Project Type**: Backend service/library  
**Performance Goals**: No additional storage queries on artifact creation; unpin may inspect existing links for one artifact only  
**Constraints**: Do not introduce a report-specific storage plane, authorization model, lifecycle model, or cascading deletion behavior  
**Scale/Scope**: One runtime story scoped to Temporal artifact service behavior for report access and lifecycle policy

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. Reuses the existing artifact service and report contract boundary.
- II. One-Click Agent Deployment: PASS. No new dependency or deployment step.
- III. Avoid Vendor Lock-In: PASS. Behavior stays provider-neutral and artifact-backed.
- IV. Own Your Data: PASS. Reports remain in operator-controlled artifact storage.
- V. Skills Are First-Class and Easy to Add: PASS. No skill runtime changes.
- VI. Replaceable AI Scaffolding: PASS. Work is deterministic service behavior plus test evidence.
- VII. Runtime Configurability: PASS. Product policy can still explicitly override retention.
- VIII. Modular and Extensible Architecture: PASS. Scope remains at the artifact service and report contract boundary.
- IX. Resilient by Default: PASS. Lifecycle operations remain idempotent and artifact-native.
- X. Facilitate Continuous Improvement: PASS. Verification records traceable MM-495 evidence.
- XI. Spec-Driven Development: PASS. Spec, plan, tasks, and verification remain aligned to the canonical Jira input.
- XII. Canonical Documentation Separation: PASS. Volatile Jira orchestration input remains under `docs/tmp`.
- XIII. Pre-release Compatibility Policy: PASS. No compatibility aliases are introduced.

## Project Structure

### Documentation (this feature)

```text
specs/231-sensitive-report-access-retention/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── sensitive-report-access-retention.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/workflows/temporal/
├── artifacts.py
└── report_artifacts.py

tests/unit/workflows/temporal/
├── test_artifacts.py
└── test_artifact_authorization.py

tests/integration/temporal/
└── test_temporal_artifact_lifecycle.py
```

**Structure Decision**: Keep the existing feature directory and implementation boundary. Realign the MoonSpec artifacts to MM-495 instead of regenerating or moving validated code/test assets.

## Complexity Tracking

No constitution violations.
