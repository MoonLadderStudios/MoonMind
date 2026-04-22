# Implementation Plan: Remediation V1 Policy Guardrails

**Branch**: `233-remediation-v1-policy-guardrails` | **Date**: 2026-04-22 | **Spec**: `specs/233-remediation-v1-policy-guardrails/spec.md`
**Input**: Single-story feature specification from `/specs/233-remediation-v1-policy-guardrails/spec.md`

## Summary

Implement MM-458 by verifying and preserving the existing remediation runtime guardrails that keep v1 manual by default, deny raw administrative capability surfaces, keep future self-healing policy inert unless bounded policy support exists, and return structured bounded outcomes for edge cases. Existing remediation action authority, mutation guard, link validation, redaction, and lifecycle helpers satisfy the story with focused verification tests for the policy-only and allowed-action metadata paths.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `test_create_execution_keeps_future_remediation_policy_inert` proves policy-only task metadata creates no remediation link; `TemporalExecutionService.create_execution()` only creates remediation links when `task.remediation` is present | no further implementation | final verify |
| FR-002 | implemented_verified | `test_create_execution_keeps_future_remediation_policy_inert` proves `remediationPolicy` is preserved as inert metadata without link creation | no further implementation | final verify |
| FR-003 | implemented_verified | `test_create_execution_keeps_future_remediation_policy_inert` proves explicitly enabled future policy metadata remains non-executable; `RemediationMutationGuardPolicy` provides bounded defaults for existing guard evaluation | no further implementation | final verify |
| FR-004 | implemented_verified | `_RAW_ACCESS_ACTION_KINDS` denies raw requests and `test_remediation_action_authority_does_not_advertise_raw_admin_actions` proves catalog metadata excludes raw host actions | no further implementation | final verify |
| FR-005 | implemented_verified | Raw Docker, SQL, storage, and raw action kinds are denied by guard/action authority | no further implementation | final verify |
| FR-006 | implemented_verified | `RemediationActionAuthorityService` and `RemediationMutationGuardService` return structured denial reasons for raw access | no further implementation | final verify |
| FR-007 | implemented_verified | `RemediationContextBuilder` and evidence tools use artifact refs/bounded target health; no full workflow-history import path found | no further implementation | final verify |
| FR-008 | implemented_verified | Mission Control and docs distinguish Live Logs from durable evidence; remediation context artifacts are separate | no further implementation | final verify |
| FR-009 | implemented_verified | Service rejects nested remediation targets; mutation guard denies self-target/nested remediation by default | no further implementation | final verify |
| FR-010 | implemented_verified | Mutation guard policy defaults self-healing depth to 1 and preserves typed actions/locks/audit/redaction decisions | no further implementation | final verify |
| FR-011 | implemented_verified | Remediation target validation rejects missing, unauthorized, non-run, and run-id targets | no further implementation | final verify |
| FR-012 | implemented_verified | Remediation lifecycle/context tests cover degraded evidence and summary outputs | no further implementation | final verify |
| FR-013 | implemented_verified | Remediation link validation pins target run; freshness guard handles target run changes | no further implementation | final verify |
| FR-014 | implemented_verified | Target freshness guard maps failed preconditions to no-op, rediagnose, or escalation | no further implementation | final verify |
| FR-015 | implemented_verified | Mutation guard tests cover lock conflicts, stale locks, and lock loss | no further implementation | final verify |
| FR-016 | implemented_verified | Action authority and mutation guard return bounded rejected/unsafe outcomes for unsupported or high-risk actions | no further implementation | final verify |
| FR-017 | implemented_verified | Remediation summary helpers and lifecycle tests cover failed summaries and lock conflict counters; automatic self-remediation is not present | no further implementation | final verify |
| FR-018 | implemented_verified | `test_remediation_action_authority_does_not_advertise_raw_admin_actions` proves allowed action metadata exposes typed actions only | no further implementation | final verify |
| FR-019 | implemented_verified | `test_create_execution_keeps_future_remediation_policy_inert` proves future-only automatic self-healing metadata is not executable v1 behavior | no further implementation | final verify |
| FR-020 | implemented_verified | MM-458 is preserved in spec, plan, tasks, quickstart, tests, and implementation evidence | no further implementation | final verify |
| FR-021 | implemented_verified | Spec, plan, and tests distinguish current v1 guarantees from future extension behavior | no further implementation | final verify |
| SC-001 | implemented_verified | `test_create_execution_keeps_future_remediation_policy_inert` verifies policy-only metadata does not spawn remediation | no further implementation | final verify |
| SC-002 | implemented_verified | raw action denial tests and `test_remediation_action_authority_does_not_advertise_raw_admin_actions` cover catalog boundaries | no further implementation | final verify |
| SC-003 | implemented_verified | `test_create_execution_keeps_future_remediation_policy_inert` verifies future policy fields remain inert | no further implementation | final verify |
| SC-004 | implemented_verified | `test_create_execution_keeps_future_remediation_policy_inert` proves enabled self-healing policy metadata does not proceed in v1 without supported bounded runtime validation | no further implementation | final verify |
| SC-005 | implemented_verified | existing remediation context/action tests cover bounded outcomes | no further implementation | final verify |
| SC-006 | implemented_verified | Live Logs docs/UI tests distinguish timeline from durable evidence | no further implementation | final verify |
| SC-007 | implemented_verified | artifact and test traceability preserve MM-458 and mapped design requirements | no further implementation | final verify |
| DESIGN-REQ-016 | implemented_verified | policy-only metadata is inert, self-healing depth defaults to 1, and manual v1 behavior is verified | no further implementation | final verify |
| DESIGN-REQ-022 | implemented_verified | target freshness guard covers rerun/precondition outcomes | no further implementation | final verify |
| DESIGN-REQ-023 | implemented_verified | bounded failure outcomes exist across remediation context/action tests | no further implementation | final verify |
| DESIGN-REQ-024 | implemented_verified | raw administrative non-goals are denied or absent from allowed action metadata | no further implementation | final verify |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: SQLAlchemy async ORM, Temporal execution service boundary, remediation action authority and mutation guard services, existing pytest fixtures  
**Storage**: Existing `TemporalExecutionCanonicalRecord` and `execution_remediation_links`; no new persistent storage  
**Unit Testing**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_temporal_service.py tests/unit/workflows/temporal/test_remediation_context.py`  
**Integration Testing**: Async DB-backed service-boundary tests in the unit suite; no provider credentials or compose-backed integration required for this policy-surface slice  
**Target Platform**: Linux server / Docker Compose deployment  
**Project Type**: FastAPI control plane plus Temporal workflow service boundary  
**Performance Goals**: Policy and capability checks remain bounded local validation with no external calls  
**Constraints**: Runtime mode; preserve MM-458; no raw host, Docker daemon, arbitrary SQL, secret read, storage-key, redaction bypass, or automatic remediation loop; no new compatibility aliases  
**Scale/Scope**: One remediation policy/capability decision at task creation or action-evaluation time

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. The story constrains orchestration behavior without replacing agents.
- II. One-Click Agent Deployment: PASS. No new services or external credentials.
- III. Avoid Vendor Lock-In: PASS. Policy guardrails are provider-neutral.
- IV. Own Your Data: PASS. Runtime decisions remain local and artifact-backed.
- V. Skills Are First-Class and Easy to Add: PASS. No mutation of skill bundles.
- VI. Replaceable Scaffolding / Tests Anchor: PASS. Adds thin boundary tests around existing contracts.
- VII. Runtime Configurability: PASS. Future automatic policy remains explicit and bounded rather than hidden.
- VIII. Modular Architecture: PASS. Work stays in remediation service/action boundaries and tests.
- IX. Resilient by Default: PASS. Fail-closed policy behavior and bounded outcomes improve unattended safety.
- X. Continuous Improvement: PASS. Structured outcomes make policy denials diagnosable.
- XI. Spec-Driven Development: PASS. This plan follows the MM-458 single-story spec.
- XII. Canonical Documentation Separation: PASS. Canonical docs remain desired-state; Jira input remains under `docs/tmp`.
- XIII. Pre-release Compatibility Policy: PASS. No aliases or backward-compat shims.

## Project Structure

### Documentation (this feature)

```text
specs/233-remediation-v1-policy-guardrails/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── remediation-v1-policy-guardrails.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code

```text
moonmind/workflows/temporal/
├── remediation_actions.py
└── service.py

tests/unit/workflows/temporal/
├── test_remediation_context.py
└── test_temporal_service.py
```

**Structure Decision**: Use the existing Temporal execution service for task/remediation creation policy verification and the existing remediation action authority service for typed action capability verification. No new source module is planned unless verification tests expose a missing fail-closed guard.

## Complexity Tracking

None.
