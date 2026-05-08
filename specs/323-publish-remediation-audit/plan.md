# Implementation Plan: Publish Remediation Audit Evidence

**Branch**: `run-jira-orchestrate-for-mm-623-publish-bce76a9b` | **Date**: 2026-05-08 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `/work/agent_jobs/mm:ee5f7fbd-9025-4fd3-b856-ce19a43c453d/repo/specs/323-publish-remediation-audit/spec.md`

## Summary

MM-623 requires remediation runs to leave a durable, operator-reviewable evidence trail: applicable remediation artifacts, bounded decision logs, stable lifecycle summaries, compact queryable audit events, and target-side annotations that supplement target-native evidence. Current repo inspection shows the core artifact publisher, summary builders, action request/result/verification artifacts, and several bounded serialization tests already exist, but queryable remediation audit event persistence, target-side mutation annotation publication, and end-to-end representative path coverage are incomplete. The implementation approach is to extend the existing remediation context/tools boundary and artifact service patterns, reuse existing artifact presentation policy, add compact audit persistence/query behavior through an existing control-event style surface where feasible, and verify with focused unit tests plus hermetic integration tests.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `moonmind/workflows/temporal/remediation_context.py` defines remediation artifact types; `RemediationLifecyclePublisher` can publish required artifacts; tests cover publisher behavior but not every run path | Add or complete lifecycle publication for all applicable path artifacts and document non-applicable artifact handling | unit + integration |
| FR-002 | implemented_verified | `REMEDIATION_ARTIFACT_TYPES` and `RemediationLifecyclePublisher.publish_json_artifact()` preserve `artifact_type`; `tests/unit/workflows/temporal/test_remediation_context.py::test_remediation_lifecycle_publisher_creates_required_artifacts` verifies link types and metadata | Preserve existing behavior while extending coverage for MM-623 paths | unit + integration |
| FR-003 | partial | `build_remediation_decision_log()` and `publish_lifecycle_summary()` publish decision log artifacts; tests cover attempted and cancellation entries | Add coverage for skipped, denied, escalated, prevention/no-PR, and verification refs across representative remediation outcomes | unit + integration |
| FR-004 | implemented_unverified | `build_remediation_summary_block()` and `build_remediation_final_summary()` expose stable fields; unit and integration tests cover repaired and escalated examples, but not every required field combination from MM-623 | Add verification-first coverage for full MM-623 summary field set; patch builders or lifecycle publisher if gaps appear | unit + integration |
| FR-005 | partial | `build_remediation_audit_event()` builds bounded audit payloads, but repo search found no durable remediation-specific audit event persistence/query path | Persist compact queryable audit events for side-effecting action decisions using bounded metadata; expose or reuse a queryable control-event surface | unit + integration |
| FR-006 | missing | Managed-session control events exist, but no remediation target-side mutation annotation contract or publication path was found for action execution | Add target-side annotation publication for remediation mutations without replacing subsystem-native artifacts | unit + integration |
| FR-007 | partial | Summary builders normalize failed/degraded states; unit tests cover bounded degraded and cancellation examples | Add explicit representative tests for missing, degraded, skipped, unsafe, and escalated evidence states | unit + integration |
| FR-008 | implemented_unverified | Artifact presentation contract requires safe metadata, refs, preview/redaction; remediation context artifacts use restricted redaction and artifact refs | Add remediation-specific artifact presentation tests proving no raw URLs, paths, or secrets appear in metadata/default previews | unit + integration |
| FR-009 | implemented_verified | `spec.md` preserves `MM-623`, original preset brief, and source coverage IDs | Preserve traceability across plan, research, data model, contracts, quickstart, tasks, implementation, verification, commit text, and PR metadata | final verify |
| SCN-001 | partial | Diagnosis-only summary behavior can be represented by existing summary helpers, but no MM-623 representative diagnosis-only path test was found | Add diagnosis-only evidence publication test | integration |
| SCN-002 | partial | Decision log builder supports bounded entries; representative skipped/denied/escalated repair candidate coverage is incomplete | Add decision log scenario tests for candidate outcomes and refs | unit + integration |
| SCN-003 | partial | Audit event builder exists; durable query behavior missing | Add side-effecting action audit persistence and query test | unit + integration |
| SCN-004 | missing | No remediation target-side annotation publication path found | Add mutation annotation scenario test against target-side evidence | integration |
| SCN-005 | implemented_unverified | Summary helpers expose degraded/escalated fields; representative integration coverage is limited | Add degraded and escalated summary verification | unit + integration |
| SCN-006 | implemented_unverified | Artifact presentation rules exist; remediation-specific presentation checks are incomplete | Add preview/redaction verification for remediation artifacts | unit + integration |
| SC-001 | partial | Publisher can emit artifact types, but every representative path is not covered | Add full representative-path artifact matrix tests | integration |
| SC-002 | partial | Audit builder exists; no queryable persistence evidence | Add audit persistence/query tests | unit + integration |
| SC-003 | implemented_unverified | Summary helper tests cover core fields, but full representative completion coverage is missing | Add full completion summary tests | unit + integration |
| SC-004 | partial | Decision log builder redacts and refs artifacts; candidate outcome coverage incomplete | Add candidate outcome matrix tests | unit + integration |
| SC-005 | implemented_unverified | Existing redaction helpers and artifact metadata policy are present; remediation-specific presentation tests are incomplete | Add no-secret/no-raw-reference artifact metadata and preview tests | unit + integration |
| SC-006 | implemented_verified | `spec.md`, this plan, and generated design artifacts preserve `MM-623` and source IDs | Keep traceability checks in tasks and final verification | final verify |
| DESIGN-REQ-022 | partial | Required artifact type constants and publisher exist, but path-complete evidence publication is incomplete | Complete and verify applicable artifact set plus safe presentation | unit + integration |
| DESIGN-REQ-023 | partial | Decision log builder exists; target-side annotation and queryable audit event persistence are missing or incomplete | Add compact audit persistence and target-side annotation publication | unit + integration |
| DESIGN-REQ-028 | implemented_unverified | Summary helper and lifecycle summary publisher exist with tests, but MM-623 requires broader representative field validation | Verify all required lifecycle summary fields and repair/prevention outcomes | unit + integration |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2 where exposed, SQLAlchemy async ORM, FastAPI service/router patterns where query surfaces are exposed, Temporal Python SDK activity/service boundaries, existing Temporal artifact service  
**Storage**: Existing Temporal execution records, `execution_remediation_links`, Temporal artifact metadata/content store, and an existing or reusable control-event/audit persistence mechanism; no new persistent table planned unless queryable audit events cannot safely reuse current control-event/audit infrastructure  
**Unit Testing**: `pytest` via `./tools/test_unit.sh`  
**Integration Testing**: Hermetic `pytest` integration suite via `./tools/test_integration.sh`, with `integration` + `integration_ci` markers  
**Target Platform**: MoonMind API/worker services on Linux containers with local-first Docker Compose support  
**Project Type**: Backend orchestration services and Temporal workflow/activity boundary  
**Performance Goals**: Representative remediation evidence publication completes without adding unbounded artifact bodies to workflow history; compact audit events remain bounded metadata suitable for query/display  
**Constraints**: Preserve artifact-first evidence, avoid secrets/raw URLs/raw paths in metadata or previews, keep workflow payloads compact, maintain retry-safe side effects, and preserve in-flight workflow/activity compatibility where boundary shapes are changed  
**Scale/Scope**: One single-story remediation evidence feature for MM-623, covering representative remediation run paths rather than the entire remediation system backlog

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS - Plan extends existing remediation orchestration/tool boundaries rather than introducing a separate agent model.
- II. One-Click Agent Deployment: PASS - No new required external service is planned; tests remain local/hermetic.
- III. Avoid Vendor Lock-In: PASS - Evidence and audit records remain portable JSON/artifact metadata and do not bind to one provider.
- IV. Own Your Data: PASS - Durable evidence stays in MoonMind-owned artifacts/control-plane records.
- V. Skills Are First-Class and Easy to Add: PASS - No skill runtime mutation is planned; generated artifacts preserve the selected skill workflow traceability.
- VI. Scientific Method Scaffold: PASS - Plan uses explicit hypothesis, test-first verification, and publishable evidence.
- VII. Runtime Configurability: PASS - No hardcoded deployment configuration is introduced; any new behavior should follow existing settings/policy patterns if configuration is needed.
- VIII. Modular Architecture: PASS - Planned work stays within remediation context/tools, artifact, and API/control-event boundaries.
- IX. Resilient by Default: PASS - Side effects must be retry-safe/idempotent, bounded, and covered at workflow/activity or service boundaries.
- X. Continuous Improvement: PASS - Evidence summary and traceability support operator review.
- XI. Spec-Driven Development: PASS - `spec.md` is the source of truth and this plan preserves all requirement IDs.
- XII. Canonical Docs vs Migration: PASS - Implementation sequencing remains in this feature artifact, not canonical docs.
- XIII. Pre-Release Velocity: PASS - No compatibility aliases or deprecated parallel contracts are planned.
- Security/Observability clauses: PASS - Planned artifacts and audit records must be secret-safe and operator-diagnosable.

## Project Structure

### Documentation (this feature)

```text
specs/323-publish-remediation-audit/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── remediation-audit-evidence.md
├── checklists/
│   └── requirements.md
└── spec.md
```

### Source Code (repository root)

```text
moonmind/workflows/temporal/
├── remediation_context.py
├── remediation_tools.py
├── remediation_actions.py
└── service.py

api_service/
├── api/routers/
├── db/models.py
└── services/

tests/unit/workflows/temporal/
└── test_remediation_context.py

tests/integration/temporal/
└── test_remediation_action_contracts.py

docs/
├── Tasks/TaskRemediation.md
└── Artifacts/ArtifactPresentationContract.md
```

**Structure Decision**: Use the existing backend orchestration and artifact-service layout. The primary implementation surface is the remediation service/activity boundary in `moonmind/workflows/temporal/`, with API/control-event exposure only if queryable audit requirements cannot be satisfied through an existing service surface. Unit tests stay near remediation helpers; hermetic integration tests validate artifact and persistence behavior through the existing Temporal artifact service and database fixtures.

## Complexity Tracking

No constitution violations are planned.
