# Implementation Plan: Sensitive Report Access and Retention

**Branch**: `run-jira-orchestrate-for-mm-463-sensitive-report-access-retention` | **Date**: 2026-04-22 | **Spec**: [spec.md](spec.md)  
**Input**: Single-story feature specification from `specs/231-sensitive-report-access-retention/spec.md`

## Summary

Implement MM-463 by tightening existing Temporal artifact service behavior for report artifacts: preserve preview/default-read behavior for restricted report content, derive report-aware retention defaults from `report.*` link types, restore report-derived retention after unpin, and prove report deletion stays artifact-native without cascading into unrelated observability artifacts. Existing authorization, preview creation, pin/unpin, soft-delete/hard-delete, and report-link validation already exist; the planned code change is narrow retention derivation plus focused unit and integration coverage.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_unverified | `TemporalArtifactService._assert_read_access`, `_assert_raw_access`, and report contract validation already apply to report artifacts. | Add report-specific access/default-read verification. | unit |
| FR-002 | implemented_unverified | `get_read_policy` selects preview `default_read_ref` when raw access is not allowed and `preview_artifact_id` exists. | Add restricted report metadata/default-read test. | unit |
| FR-003 | implemented_unverified | `read`, `read_chunks`, `read_path`, and `presign_download` call `_assert_raw_access`. | Add restricted report raw-denial test. | unit |
| FR-004 | missing | `_derive_retention` currently returns `standard` for unspecified `report.primary`. | Add failing test and derive `long`. | unit |
| FR-005 | missing | `_derive_retention` currently returns `standard` for unspecified `report.summary`. | Add failing test and derive `long`. | unit |
| FR-006 | implemented_unverified | Existing default for unspecified report structured/evidence is `standard`, which satisfies standard-or-long. | Add explicit regression proving non-observability retention. | unit |
| FR-007 | partial | `pin` sets `pinned`; `unpin` currently restores generic `standard`, not link-derived report retention. | Add failing test and restore link-derived retention after unpin. | unit |
| FR-008 | implemented_unverified | `soft_delete`, `hard_delete`, and `sweep_lifecycle` use existing artifact lifecycle path. | Add report deletion integration regression. | integration_ci |
| FR-009 | implemented_unverified | Deletion operates on one artifact ID and does not traverse links, but no report-specific regression exists. | Add integration test with report and runtime observability artifacts on one execution. | integration_ci |
| FR-010 | implemented_unverified | MM-463 preserved in `spec.md` and orchestration input. | Preserve through plan, tasks, verification, and traceability command. | traceability |
| DESIGN-REQ-015 | implemented_unverified | Existing auth and preview/default-read service behavior. | Add report-focused access coverage. | unit |
| DESIGN-REQ-016 | partial | Pin exists; report default retention and unpin restoration are incomplete. | Derive report retention and restore on unpin. | unit |
| DESIGN-REQ-022 | implemented_unverified | Existing lifecycle deletes by artifact ID only. | Add report deletion/no-cascade integration coverage. | integration_ci |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: SQLAlchemy async ORM, Pydantic v2 models, existing Temporal artifact service and report artifact contract helpers  
**Storage**: Existing temporal artifact tables and artifact store only; no new persistent storage  
**Unit Testing**: `pytest` through `./tools/test_unit.sh`; focused command `./tools/test_unit.sh tests/unit/workflows/temporal/test_artifacts.py tests/unit/workflows/temporal/test_artifact_authorization.py`  
**Integration Testing**: `pytest` integration_ci through `./tools/test_integration.sh`; focused local command `pytest tests/integration/temporal/test_temporal_artifact_lifecycle.py -m integration_ci -q --tb=short` when compose is unavailable  
**Target Platform**: MoonMind API/Temporal artifact service runtime  
**Project Type**: Backend service/library  
**Performance Goals**: No additional storage queries on artifact create; unpin may inspect existing links for one artifact only  
**Constraints**: Do not introduce a report-specific storage plane, authorization model, lifecycle model, or cascading deletion behavior  
**Scale/Scope**: One runtime story scoped to Temporal artifact service behavior for report access and retention

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS. Uses existing artifact service behavior.
- II. One-Click Agent Deployment: PASS. No new external dependency.
- III. Avoid Vendor Lock-In: PASS. Report behavior is provider-neutral artifact metadata and links.
- IV. Own Your Data: PASS. Reports remain in operator-controlled artifact storage.
- V. Skills Are First-Class and Easy to Add: PASS. No skill runtime changes.
- VI. Replaceable AI Scaffolding: PASS. Adds deterministic service behavior and tests.
- VII. Runtime Configurability: PASS. Product policy can still explicitly override retention.
- VIII. Modular and Extensible Architecture: PASS. Changes stay within artifact service and tests.
- IX. Resilient by Default: PASS. Lifecycle operations remain idempotent and artifact-native.
- X. Facilitate Continuous Improvement: PASS. Verification will record traceable MM-463 evidence.
- XI. Spec-Driven Development: PASS. Spec, plan, tasks, and verification drive implementation.
- XII. Canonical Documentation Separation: PASS. Runtime work stays in code; volatile orchestration input remains under `docs/tmp`.
- XIII. Pre-release Compatibility Policy: PASS. No compatibility aliases are introduced; internal retention behavior is updated directly.

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

**Structure Decision**: Implement the story at the existing artifact service boundary. Keep report metadata validation in `report_artifacts.py`, retention/lifecycle behavior in `artifacts.py`, and tests in the established unit and integration suites.

## Complexity Tracking

No constitution violations.
