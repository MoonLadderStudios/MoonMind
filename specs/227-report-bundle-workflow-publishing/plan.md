# Implementation Plan: Report Bundle Workflow Publishing

**Branch**: `run-jira-orchestrate-for-mm-461-report-b-3e6e18fa` | **Date**: 2026-04-22 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `specs/227-report-bundle-workflow-publishing/spec.md`

## Summary

Implement MM-461 by adding a compact report bundle publication contract on top of the existing report artifact link-type work from MM-460. The technical approach is to keep report bytes and evidence in the current artifact store, add a workflow-safe `report_bundle_v = 1` result model/helper, provide an activity-facing publication path that writes report component artifacts and returns compact refs, and cover validation with unit tests plus an activity-boundary integration-style test.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | missing | Existing `TemporalArtifactService.create`/`write_complete` can publish individual artifacts; no bundle publication path exists. | Add activity-side report bundle publication helper. | unit + integration |
| FR-002 | partial | `moonmind/workflows/temporal/report_artifacts.py` validates report artifact metadata; no bundle payload validator blocks embedded bodies. | Add compact bundle validation. | unit |
| FR-003 | implemented_unverified | `TemporalArtifactRepository.add_link` stores namespace/workflow_id/run_id/link_type/label. | Cover bundle helper writes expected links. | unit |
| FR-004 | partial | Report metadata allows `step_id` and `attempt`; no bundle helper applies step-aware metadata consistently. | Add optional step metadata support in bundle publication. | unit + integration |
| FR-005 | missing | MM-460 supports `report.primary` and metadata keys, but no final bundle invariant enforces exactly one final marker. | Add final report validation. | unit |
| FR-006 | missing | No `report_bundle_v` model/helper exists in runtime code. | Add compact result shape. | unit |
| FR-007 | missing | No bundle-level validation rejects embedded bodies, evidence blobs, logs, screenshots, URLs, transcripts, or large findings in workflow-facing payloads. | Add fail-fast validator. | unit |
| FR-008 | missing | Evidence can be linked as `report.evidence`; no bundle helper returns separate evidence refs. | Add evidence component support. | unit |
| FR-009 | implemented_unverified | MM-461 preserved in `spec.md` and orchestration input. | Preserve through plan, tasks, verification, and code/test comments. | traceability check |
| DESIGN-REQ-006 | missing | No compact bundle result exists. | Add report bundle contract. | unit |
| DESIGN-REQ-008 | partial | Artifact-backed report metadata exists, but workflow-facing bundle validation is absent. | Add embedded-content rejection. | unit |
| DESIGN-REQ-010 | missing | No `report_bundle_v = 1` runtime result shape exists. | Add result model/helper. | unit |
| DESIGN-REQ-014 | implemented_unverified | Existing artifact link model stores required execution identity. | Verify through bundle helper. | unit |
| DESIGN-REQ-017 | partial | Metadata keys exist; helper does not yet attach step metadata. | Add step metadata parameters. | unit |
| DESIGN-REQ-018 | missing | No activity-facing report bundle publication path exists. | Add facade method for activity use. | integration |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2 patterns where schemas are needed, SQLAlchemy async ORM, existing Temporal artifact service/activity facade  
**Storage**: Existing temporal artifact tables and configured artifact store; no new persistent storage  
**Unit Testing**: `./tools/test_unit.sh` with targeted pytest during iteration  
**Integration Testing**: `./tools/test_integration.sh` for required `integration_ci`; focused activity-boundary pytest for local iteration  
**Target Platform**: MoonMind Temporal worker and API service runtime  
**Project Type**: Python backend workflow/activity service  
**Performance Goals**: Bundle return payload remains bounded by refs and compact metadata; report bytes remain artifact-backed  
**Constraints**: Do not embed large report bodies, logs, screenshots, transcripts, raw URLs, or evidence blobs in workflow history; use existing artifact storage/linkage only  
**Scale/Scope**: One runtime story covering report bundle publication and compact workflow return values

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS. Uses existing activity and artifact service boundaries.
- II. One-Click Agent Deployment: PASS. No new service or external dependency.
- III. Avoid Vendor Lock-In: PASS. Contract is provider-neutral artifact metadata and refs.
- IV. Own Your Data: PASS. Report bodies and evidence remain in operator-controlled artifacts.
- V. Skills Are First-Class and Easy to Add: PASS. No skill runtime contract changes.
- VI. Replaceable AI Scaffolding: PASS. Adds thick artifact contract rather than provider prompts.
- VII. Runtime Configurability: PASS. Uses existing artifact storage configuration.
- VIII. Modular and Extensible Architecture: PASS. Keeps behavior in report artifact/artifact service modules.
- IX. Resilient by Default: PASS. Activity boundary writes durable artifacts and returns compact deterministic refs.
- X. Facilitate Continuous Improvement: PASS. Verification will produce traceable evidence.
- XI. Spec-Driven Development: PASS. Spec, plan, tasks, and verification artifacts drive work.
- XII. Canonical Documentation Separation: PASS. Migration/orchestration input remains under `docs/tmp`.
- XIII. Pre-release Compatibility Policy: PASS. New internal contract is additive for a pre-release feature and does not add compatibility aliases.

## Project Structure

### Documentation (this feature)

```text
specs/227-report-bundle-workflow-publishing/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── report-bundle-contract.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/workflows/temporal/
├── report_artifacts.py
└── artifacts.py

tests/unit/workflows/temporal/
├── test_artifacts.py
└── test_artifacts_activities.py
```

**Structure Decision**: Implement the compact bundle contract in `report_artifacts.py`, wire activity-facing publication through the existing artifact service/facade in `artifacts.py`, and test at service plus activity-boundary levels.

## Complexity Tracking

No constitution violations.
