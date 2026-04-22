# Implementation Plan: Report Workflow Rollout and Examples

**Branch**: `232-report-workflow-rollout-examples` | **Date**: 2026-04-22 | **Spec**: [spec.md](./spec.md)  
**Input**: Single-story feature specification from `specs/232-report-workflow-rollout-examples/spec.md`

## Summary

Implement MM-464 by adding runtime helpers that encode report-producing workflow family examples as deterministic, validated mappings. The technical approach is to extend the existing report artifact contract module with immutable mapping data, rollout classification, and projection-summary validation while keeping storage and artifact publication unchanged. Unit tests cover mapping contents, generic fallback classification, required `report.primary` validation, and unsafe projection rejection; integration-style activity tests continue to cover the existing artifact publication boundary.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `REPORT_WORKFLOW_MAPPINGS`; `test_supported_report_workflow_mappings_separate_report_and_observability` | complete | unit passed |
| FR-002 | implemented_verified | `ReportWorkflowMapping.recommended_metadata_keys`; mapping tests | complete | unit passed |
| FR-003 | implemented_verified | `validate_report_workflow_artifact_classes`; validation tests | complete | unit passed |
| FR-004 | implemented_verified | Mapping separates `report_link_types` and `observability_link_types`; mapping tests | complete | unit passed |
| FR-005 | implemented_verified | Existing generic output artifact tests plus fallback classification test | complete | unit passed |
| FR-006 | implemented_verified | `classify_report_rollout_artifacts`; fallback classification test | complete | unit passed |
| FR-007 | implemented_verified | `REPORT_WORKFLOW_ROLLOUT_PHASES`; rollout phase test | complete | unit passed |
| FR-008 | implemented_verified | `build_report_projection_summary`; projection summary tests | complete | unit passed |
| FR-009 | implemented_verified | MM-464 traceability in spec, tasks, verification, code, and tests | complete | traceability passed |
| DESIGN-REQ-003 | implemented_verified | Supported mappings use stable `report.*` classes | complete | unit passed |
| DESIGN-REQ-007 | implemented_verified | Mapping fields distinguish report/evidence/observability classes | complete | unit passed |
| DESIGN-REQ-019 | implemented_verified | Mapping metadata guidance covers finding/severity/sensitivity keys | complete | unit passed |
| DESIGN-REQ-020 | implemented_verified | Generic fallback classification preserves generic output behavior | complete | unit passed |
| DESIGN-REQ-021 | implemented_verified | Runtime rollout phases are ordered and tested | complete | unit passed |
| DESIGN-REQ-022 | implemented_verified | Projection summaries validate compact refs and bounded metadata | complete | unit passed |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Existing MoonMind Temporal artifact service helpers, dataclasses, pytest  
**Storage**: Existing artifact store only; no new persistent storage  
**Unit Testing**: pytest through `./tools/test_unit.sh`  
**Integration Testing**: pytest integration suite through `./tools/test_integration.sh`; targeted local validation uses existing artifact service tests  
**Target Platform**: Linux service/runtime workers  
**Project Type**: Python backend/runtime library  
**Performance Goals**: Mapping/classification helpers are deterministic in-memory operations over small bounded inputs  
**Constraints**: Preserve existing artifact APIs, no report-specific storage, no widening generic output semantics, no inline report bodies in projection summaries  
**Scale/Scope**: One runtime story covering four workflow-family mappings, fallback classification, rollout phases, and projection summary guardrails

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS - extends runtime artifact contract helpers without introducing a new agent or report execution engine.
- II. One-Click Agent Deployment: PASS - no new services, secrets, or deployment prerequisites.
- III. Avoid Vendor Lock-In: PASS - workflow family mappings are provider-neutral artifact classes.
- IV. Own Your Data: PASS - all report data remains artifact-backed in operator-controlled storage.
- V. Skills Are First-Class and Easy to Add: PASS - no changes to skill runtime contracts.
- VI. Design for Deletion / Thick Contracts: PASS - adds compact helper contracts rather than workflow-specific scaffolding.
- VII. Powerful Runtime Configurability: PASS - no hardcoded external endpoints or credentials.
- VIII. Modular and Extensible Architecture: PASS - scoped to `report_artifacts.py`.
- IX. Resilient by Default: PASS - no workflow payload shape changes; helpers fail fast on invalid rollout payloads.
- X. Facilitate Continuous Improvement: PASS - final verification and traceability preserve MM-464 evidence.
- XI. Spec-Driven Development: PASS - spec, plan, tasks, implementation, and verification are created before completion.
- XII. Canonical Documentation Separation: PASS - migration notes remain in spec/tmp artifacts; canonical docs are not rewritten as a construction diary.
- XIII. Pre-release Compatibility Policy: PASS - no compatibility aliases or hidden semantic transforms are introduced.

## Project Structure

### Documentation (this feature)

```text
specs/232-report-workflow-rollout-examples/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── report-workflow-rollout.md
├── tasks.md
└── verification.md
```

### Source Code (repository root)

```text
moonmind/workflows/temporal/
└── report_artifacts.py

tests/unit/workflows/temporal/
└── test_report_workflow_rollout.py
```

**Structure Decision**: Keep MM-464 runtime helpers beside existing report artifact contract helpers because they validate report-family semantics without changing artifact persistence or API routers.

## Complexity Tracking

No constitution violations.
