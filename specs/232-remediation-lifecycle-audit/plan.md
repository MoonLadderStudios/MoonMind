# Implementation Plan: Remediation Lifecycle Audit

**Branch**: `232-remediation-lifecycle-audit` | **Date**: 2026-04-22 | **Spec**: `specs/232-remediation-lifecycle-audit/spec.md`
**Input**: Single-story feature specification from `/specs/232-remediation-lifecycle-audit/spec.md`

## Summary

Implement MM-456 by extending the existing remediation runtime boundary so each remediation run exposes a bounded lifecycle phase, required remediation artifacts, a stable remediation summary block, target-side linkage metadata, and compact audit events. Existing code already has remediation links, a context artifact builder, action authority audit payloads, link status fields, generic run summaries, and managed-session control artifacts, but it does not yet publish the full required remediation artifact set or summary/audit contract. Unit tests will cover model/serializer/service behavior; integration or service-boundary tests will verify artifact publication and read-model visibility through the existing Temporal artifact and execution services.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | missing | no `remediationPhase` field found | add bounded remediation phase model/read output | unit + integration |
| FR-002 | missing | no allowed remediation phase enum found | add phase validation | unit |
| FR-003 | missing | no phase progression projection found | expose phase progression in summary/read model | integration |
| FR-004 | partial | `execution_remediation_links.outcome` exists but no full resolution set | normalize lifecycle outcomes and summary values | unit |
| FR-005 | implemented_unverified | cancellation audit exists for generic executions; no remediation-specific target mutation proof | add remediation cancellation/failure assertions | unit + integration |
| FR-006 | partial | `RemediationMutationGuardService.release_lock()` exists; no final summary publication path | add best-effort summary/lock-release result evidence | unit |
| FR-007 | partial | mutation guard tracks lock/ledger/budget in service; no Continue-As-New preservation contract | add compact continuation payload model/validation | unit |
| FR-008 | implemented_verified | `RemediationContextBuilder` publishes `remediation.context` | no implementation beyond final traceability | final verify |
| FR-009 | missing | no `remediation.plan` artifact publisher found | add remediation plan artifact publication contract | unit + integration |
| FR-010 | missing | no `remediation.decision_log` artifact publisher found | add decision log artifact publication contract | unit + integration |
| FR-011 | implemented_unverified | action authority result includes request payload but no artifact publication | publish action request artifact | unit + integration |
| FR-012 | implemented_unverified | action authority result includes result payload but no artifact publication | publish action result artifact | unit + integration |
| FR-013 | missing | no `remediation.verification` artifact publisher found | add verification artifact publication contract | unit + integration |
| FR-014 | missing | no `remediation.summary` artifact publisher found | add final remediation summary artifact | unit + integration |
| FR-015 | partial | artifact service supports metadata/redaction; only context artifact uses remediation type | enforce safety metadata for all remediation artifacts | unit |
| FR-016 | missing | generic `reports/run_summary.json`; no remediation block found | add stable remediation summary block | unit + integration |
| FR-017 | implemented_unverified | managed session supervisor publishes `session.control_event`; no remediation mutation test | verify native target artifacts remain linked | integration |
| FR-018 | partial | action authority has audit payload; no parallel remediation audit trail persistence | add remediation audit event contract/service | unit |
| FR-019 | partial | link status fields exist for latest action/outcome; active count/latest metadata read model incomplete | extend target-side linkage summary | unit + integration |
| FR-020 | partial | action authority audit has principal/decision fields; lacks full required audit event fields | add compact audit event model | unit |
| FR-021 | missing | no remediation audit event query surface found | persist/query compact audit trail | integration |
| FR-022 | implemented_unverified | existing redaction helpers sanitize action audit output | add audit metadata boundedness tests | unit |
| FR-023 | partial | `execution_remediation_links` has compact link fields | expose downstream detail summary contract | unit + integration |
| FR-024 | partial | link stores pinned target run; resulting run not recorded | add rerun/resulting-run summary evidence | unit |
| FR-025 | partial | context builder/action guard have bounded errors; lifecycle cases not unified | normalize failure/degraded outcome reasons | unit |
| FR-026 | implemented_unverified | mutation guard has precondition/freshness decisions in adjacent story | verify lifecycle summary records precondition-failed/no-op | unit |
| FR-027 | partial | lock release exists; no failed-remediator summary flow | add failure finalization evidence | unit |
| FR-028 | missing | no degraded evidence summary flag beyond source brief | add degraded evidence reporting | unit |
| FR-029 | partial | context payload records target refs/task runs; missing evidence-class unavailable list | add unavailable evidence class reporting | unit |
| FR-030 | partial | context payload sets liveFollow.supported false; no fallback summary evidence | record live-follow fallback evidence | unit |
| FR-031 | implemented_verified | spec and Jira input preserve MM-456 | preserve traceability in tasks, tests, and verification | final verify |
| SC-001 | missing | no phase tests found | add allowed-phase tests | unit |
| SC-002 | partial | context artifact test exists only for `remediation.context` | add artifact type matrix tests | unit + integration |
| SC-003 | missing | no remediation summary block tests found | add summary block tests | unit + integration |
| SC-004 | implemented_unverified | session control artifact tests exist | add remediation-target mutation preservation test | integration |
| SC-005 | partial | action authority audit tests exist but not full control-plane audit fields | add audit event tests | unit |
| SC-006 | partial | generic cancel audit tests exist; no remediation finalization path | add cancellation/failure finalization tests | unit + integration |
| SC-007 | missing | no continuation payload tests for remediation context | add Continue-As-New preservation tests | unit |
| SC-008 | partial | context/action guard degradation exists in pieces | add unified bounded outcome tests | unit |
| DESIGN-REQ-017 | partial | generic run state and link records exist; no phase model | implement phase and lifecycle finalization | unit + integration |
| DESIGN-REQ-018 | partial | context artifact exists; other required artifact types missing | implement remediation artifact publisher | unit + integration |
| DESIGN-REQ-019 | partial | link status fields and action audit payloads exist | implement target linkage and audit trail | unit + integration |
| DESIGN-REQ-022 | partial | pinned target run exists; current/resulting run evidence incomplete | implement rerun/precondition/continuation summary evidence | unit |
| DESIGN-REQ-023 | partial | bounded errors exist in adjacent services | consolidate failure/degraded outcome evidence | unit |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: SQLAlchemy async ORM, Pydantic v2, Temporal Python SDK service boundaries, existing Temporal artifact service, existing remediation context/action services  
**Storage**: Existing Temporal execution records, `execution_remediation_links`, Temporal artifact metadata/content store, and existing execution memo/search/projection paths; no new persistent database table planned unless audit events cannot reuse an existing control-event mechanism  
**Unit Testing**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py` plus focused schema/service tests as needed  
**Integration Testing**: `./tools/test_integration.sh` for hermetic integration when artifact/read-model routes are touched; targeted service-boundary tests may run through `./tools/test_unit.sh` first  
**Target Platform**: Linux server / Docker Compose deployment  
**Project Type**: FastAPI control plane plus Temporal workflow/service boundary  
**Performance Goals**: Remediation lifecycle evidence publication is bounded by small JSON artifacts and compact audit/link fields; no unbounded log or artifact bodies enter workflow history  
**Constraints**: Runtime mode; preserve MM-456 traceability; keep artifacts server-mediated; no raw credentials, presigned URLs, storage keys, local filesystem paths, or unbounded logs in durable summaries/audit metadata  
**Scale/Scope**: One remediation run linked to one target execution/run, with multiple bounded artifact and audit records for that run

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. The work makes remediation orchestration visible without replacing agent behavior.
- II. One-Click Agent Deployment: PASS. No external service or credential is required.
- III. Avoid Vendor Lock-In: PASS. Lifecycle evidence is provider-neutral.
- IV. Own Your Data: PASS. Evidence remains in local artifacts, links, and audit records.
- V. Skills Are First-Class and Easy to Add: PASS. The story does not mutate skill sources or runtime skill resolution.
- VI. Replaceable Scaffolding / Tests Anchor: PASS. The plan uses thin contracts and explicit tests around runtime evidence.
- VII. Runtime Configurability: PASS. No hardcoded provider behavior; lifecycle values are bounded product semantics.
- VIII. Modular Architecture: PASS. Changes stay in remediation, artifact, summary, and read-model boundaries.
- IX. Resilient by Default: PASS. Cancellation, failure, degraded evidence, and continuation are explicit.
- X. Continuous Improvement: PASS. Summary and audit evidence improves post-run diagnosis.
- XI. Spec-Driven Development: PASS. This plan follows the MM-456 one-story spec.
- XII. Canonical Documentation Separation: PASS. Temporary Jira input remains under `docs/tmp`; canonical docs are not turned into migration notes.
- XIII. Pre-release Compatibility Policy: PASS. No compatibility alias layer is planned.

## Project Structure

### Documentation (this feature)

```text
specs/232-remediation-lifecycle-audit/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── remediation-lifecycle-audit.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code

```text
moonmind/workflows/temporal/
├── remediation_context.py
├── remediation_actions.py
├── remediation_tools.py
├── artifacts.py
└── service.py

api_service/db/
└── models.py

api_service/api/routers/
├── executions.py
└── task_runs.py

tests/unit/workflows/temporal/
└── test_remediation_context.py

tests/unit/api/routers/
├── test_executions.py
└── test_task_runs.py
```

**Structure Decision**: Keep lifecycle evidence at existing remediation service and artifact boundaries. Extend compact DB/read-model fields only where needed for target-side linkage and queryable audit evidence; store deep evidence in artifacts.

## Complexity Tracking

None.
