# Implementation Plan: Record Audit Events and Failure Diagnostics for Skills On Demand

**Branch**: `run-jira-orchestrate-for-mm-616-record-a-d28398f6` | **Date**: 2026-05-08 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `specs/319-audit-failure-diagnostics/spec.md`

**Setup Note**: `.specify/scripts/bash/setup-plan.sh --json` was attempted, but the script rejected the managed runtime branch name because it is not numeric-prefixed. Planning continued manually against `.specify/feature.json`, which points to `specs/319-audit-failure-diagnostics`.

## Summary

MM-616 requires every Skills On Demand query and request path to leave bounded audit evidence and safe diagnostics while preserving active snapshots on denial, materialization failure, and runtime refresh failure. Current code already has Skills On Demand request/query models, failure-code mapping, query hash metadata, snapshot-preserving denials, and materialization/refresh tests. The planned work is to add explicit audit/diagnostic event contracts, emit one bounded event per query/request outcome, attach controlled diagnostics refs where available, and expand unit plus Temporal activity-boundary integration coverage for the full observability matrix.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `moonmind/services/skills_on_demand.py` returns denied results without snapshot ids for query/request failures; `tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py` covers disabled and invalid request preservation. | Preserve behavior while adding audit event emission. | final verify |
| FR-002 | implemented_verified | `tests/integration/temporal/test_skills_on_demand_request_activation.py` covers materialization, checksum, and runtime refresh failures preserving active snapshot/projection. | Preserve behavior while adding diagnostics refs/events. | final verify |
| FR-003 | implemented_unverified | `SkillsOnDemandRequestResult` and `SkillsOnDemandQueryResult` carry `code` and `message`, but no dedicated failure diagnostic model exists. | Add/confirm explicit failure diagnostic contract and tests. | unit |
| FR-004 | implemented_unverified | Denied request results include `active_snapshot_id` and `parent_snapshot_ref` where available. | Add targeted assertion that current snapshot ref is present when safe/relevant. | unit |
| FR-005 | missing | No `diagnostics_ref` field or controlled diagnostics-ref contract exists on Skills On Demand results. | Add bounded diagnostics reference handling for failure cases where diagnostics are produced. | unit + integration |
| FR-006 | partial | Literal codes cover documented codes plus `already_active`/`enabled_mode_not_implemented`; mapping exists for several resolver/runtime failures. | Normalize remaining code coverage and prove all documented applicable codes are stable. | unit |
| FR-007 | missing | Query result metadata has `query_hash`, but no emitted `skills_on_demand.query` event contract. | Add query audit event model/emission. | unit + integration |
| FR-008 | partial | `SkillsOnDemandService._query_hash()` hashes query text in result metadata. | Ensure audit event uses hash and never raw long query text. | unit |
| FR-009 | partial | Query result metadata includes result count and denial data but lacks workflow/run/step/runtime/snapshot event envelope. | Add bounded query event fields. | unit + integration |
| FR-010 | missing | Request result metadata exists but no emitted `skills_on_demand.request` audit event contract. | Add request audit event model/emission. | unit + integration |
| FR-011 | partial | Request results include requested skills, status/result data, snapshot refs, materialization summary, and no body refs in existing tests; no audit event coverage. | Add request event fields and body/ref redaction assertions. | unit + integration |
| FR-012 | partial | `SkillsOnDemandRequestStatus` currently supports `activated`, `denied`, and `no_change`; `requires_approval` is not accepted. | Decide in contract to reserve or model `requires_approval` without enabling approval behavior; update schema/tests accordingly. | unit |
| FR-013 | implemented_unverified | Existing query/request tests assert Skill body refs and hidden paths are not serialized in results. | Extend audit/diagnostics tests to assert no secrets, Skill bodies, raw long query text, arbitrary artifact/database refs, or repo projection mutations. | unit + integration |
| FR-014 | missing | No controlled Skills On Demand diagnostic artifact/ref model exists. | Add diagnostic ref behavior for oversized/details-needed diagnostics. | unit + integration |
| FR-015 | partial | Query eligibility blocks repo/local Skills by policy; request path relies on resolver policy. | Add denial audit evidence that is operator-understandable without policy bypass. | unit |
| FR-016 | implemented_unverified | MM-615 tests prove repo-authored `.agents/skills` source is not overwritten; no MM-616 audit assertion exists. | Add audit/diagnostic assertion that repo projection changes are not represented as repo-authored source changes. | integration |
| FR-017 | partial | Existing tests cover disabled feature, bounded query metadata, already-active request, allowed request, policy denial, materialization failure, and runtime refresh failure across unit/integration suites, but not audit event emission. | Add audit-focused unit and integration tests for the matrix. | unit + integration |
| FR-018 | implemented_verified | `spec.md` preserves `MM-616` and original Jira preset brief. | Preserve traceability in plan, tasks, implementation notes, verification, commit/PR metadata when produced. | final verify |
| SC-001 | missing | No query audit event emission exists. | Emit exactly one query audit event for each exercised query path. | unit + integration |
| SC-002 | missing | No request audit event emission exists. | Emit exactly one request audit event for each exercised request path. | unit + integration |
| SC-003 | partial | Failure codes and snapshot preservation exist for many paths. | Complete stable code coverage and audit diagnostics for each failure path. | unit + integration |
| SC-004 | partial | Query hash exists in result metadata. | Verify audit metric/event records use hash and omit raw long query text. | unit |
| SC-005 | implemented_unverified | Existing result tests assert no Skill body refs; audit/diagnostic surfaces are not yet covered. | Add redaction/bounds assertions for new audit and diagnostics outputs. | unit + integration |
| SC-006 | partial | Current tests cover most behavior matrix without audit event assertions. | Extend matrix to cover audit and diagnostics outputs. | unit + integration |
| SC-007 | implemented_verified | `spec.md` and this plan preserve MM-616 and source mappings. | Preserve through downstream tasks and final verification. | final verify |
| DESIGN-REQ-001 | implemented_verified | Snapshot-preserving denials and materialization/refresh tests exist. | Preserve while adding audit evidence. | final verify |
| DESIGN-REQ-002 | partial | Structured code/message exists; optional diagnostics ref is missing. | Add diagnostics reference model/contract. | unit |
| DESIGN-REQ-003 | missing | No `skills_on_demand.query` event is emitted. | Add query audit event. | unit + integration |
| DESIGN-REQ-004 | missing | No `skills_on_demand.request` event is emitted. | Add request audit event. | unit + integration |
| DESIGN-REQ-005 | partial | Query hash exists in metadata, not in a formal audit event. | Use query hash in audit and guard against raw query leakage. | unit |
| DESIGN-REQ-006 | implemented_unverified | Existing outputs avoid body refs; audit diagnostics need equivalent proof. | Extend redaction and access-boundary tests to audit diagnostics. | unit + integration |
| DESIGN-REQ-007 | partial | Existing behavior matrix is partly covered; audit/diagnostics coverage missing. | Add tests for all required cases with audit assertions. | unit + integration |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2, Temporal Python SDK activities, existing Agent Skill resolver/materializer services, structured logging/artifact helpers where appropriate  
**Storage**: Existing workflow/activity payloads, structured logs, result metadata, and artifact-backed diagnostics only; no new persistent table planned  
**Unit Testing**: `./tools/test_unit.sh` with focused pytest targets during iteration  
**Integration Testing**: `./tools/test_integration.sh` for `integration_ci` Temporal activity-boundary tests  
**Target Platform**: MoonMind managed runtime workers and Temporal activity workers  
**Project Type**: Python service/workflow orchestration  
**Performance Goals**: One bounded audit event per query/request path; audit payloads remain compact and avoid high-cardinality raw query text  
**Constraints**: Preserve active snapshots on failure; do not expose secrets, Skill bodies, arbitrary artifact/database access, or repo-authored projection mutations; keep workflow history compact  
**Scale/Scope**: One single-story runtime feature for `MM-616`, covering Skills On Demand query/request audit and diagnostics only

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS - work stays in MoonMind orchestration/activity boundaries and does not rebuild agent behavior.
- II. One-Click Agent Deployment: PASS - no new mandatory external service or credential is planned.
- III. Avoid Vendor Lock-In: PASS - contracts are MoonMind Skills On Demand events, not provider-specific behavior.
- IV. Own Your Data: PASS - diagnostics and audit evidence remain operator-controlled in existing local workflow/artifact/log surfaces.
- V. Skills Are First-Class and Easy to Add: PASS - the story improves Skill request/query observability without mutating source Skill folders.
- VI. Scientific Method / Tests Anchor: PASS - plan requires unit and integration coverage before implementation.
- VII. Runtime Configurability: PASS - existing feature flag behavior remains observable and safe by default.
- VIII. Modular Architecture: PASS - event/diagnostic behavior is planned at service/activity boundaries.
- IX. Resilient by Default: PASS - failure codes, snapshot preservation, and diagnostics are first-class requirements.
- X. Continuous Improvement: PASS - audit and diagnostics improve operator-visible run evidence.
- XI. Spec-Driven Development: PASS - `spec.md` is the source of truth and this plan precedes tasks/implementation.
- XII. Canonical Documentation Separation: PASS - implementation tracking remains in `specs/319-audit-failure-diagnostics/`.
- XIII. Pre-Release Compatibility Policy: PASS - no compatibility aliases or hidden fallback semantics are planned; unsupported values should fail through explicit validation.

Re-check after Phase 1: PASS. The generated data model, contract, and quickstart preserve the same gates and introduce no new constitution violation.

## Project Structure

### Documentation (this feature)

```text
specs/319-audit-failure-diagnostics/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── skills-on-demand-audit-diagnostics-contract.md
├── checklists/
│   └── requirements.md
└── tasks.md              # created later by /speckit.tasks
```

### Source Code (repository root)

```text
moonmind/
├── schemas/
│   └── agent_skill_models.py
├── services/
│   └── skills_on_demand.py
└── workflows/
    └── agent_skills/
        └── agent_skills_activities.py

tests/
├── unit/
│   └── workflows/
│       └── agent_skills/
│           └── test_skills_on_demand_controls.py
└── integration/
    └── temporal/
        ├── test_skills_on_demand_disabled.py
        └── test_skills_on_demand_request_activation.py
```

**Structure Decision**: This is a backend workflow/service feature. Planning targets existing Skills On Demand schemas, service logic, Temporal activity wrappers, and their unit plus integration_ci test suites.

## Complexity Tracking

No constitution violations require complexity exceptions.
