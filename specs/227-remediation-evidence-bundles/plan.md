# Implementation Plan: Remediation Evidence Bundles

**Branch**: `227-remediation-evidence-bundles` | **Date**: 2026-04-22 | **Spec**: `specs/227-remediation-evidence-bundles/spec.md`
**Input**: Single-story runtime feature specification from `specs/227-remediation-evidence-bundles/spec.md`, generated from the MM-452 Jira preset brief in `docs/tmp/jira-orchestration-inputs/MM-452-moonspec-orchestration-input.md`.

## Summary

Implement MM-452 by treating the existing remediation context builder and typed evidence tool service as the artifact-first evidence boundary, then adding the missing side-effect guard read before action submission. Existing slices already generate bounded `remediation.context` artifacts and typed artifact/log/live-follow reads. The remaining runtime work is a side-effect-free `prepare_action_request` path that validates the linked context and re-reads current target health plus pinned-vs-current run identity before any future action executor submits work.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `RemediationContextBuilder.build_context`, existing context tests | preserve | focused unit |
| FR-002 | implemented_verified | context payload assertions for target, selectors, policies, liveFollow | preserve | focused unit |
| FR-003 | implemented_verified | context payload uses refs and boundedness flags | preserve | focused unit |
| FR-004 | implemented_verified | tail/task-run clamps and sanitized policy payload | preserve | focused unit |
| FR-005 | implemented_verified | `RemediationEvidenceToolService` context/artifact/log/live-follow methods | preserve | focused unit |
| FR-006 | implemented_verified | link/context checks, declared artifact/taskRunId checks | preserve | focused unit |
| FR-007 | implemented_verified | undeclared evidence rejection and no raw privileged surface | preserve | focused unit |
| FR-008 | implemented_verified | live-follow gating by supported/mode/taskRunId | preserve | focused unit |
| FR-009 | implemented_verified | live-follow cursor recorder and unsupported follow rejection | preserve | focused unit |
| FR-010 | missing | no pre-action health guard existed before this story | add side-effect-free preparation method | focused unit |
| FR-011 | implemented_verified | sanitized context assertions and restricted artifact metadata | preserve | focused unit |
| FR-012 | implemented_verified | missing target and optional evidence degradation tests | preserve | focused unit |
| SC-001-SC-005 | implemented_verified | existing context/evidence unit tests | preserve | focused unit |
| SC-006 | missing | no test asserted current target health re-read | add focused unit | focused unit |
| SC-007 | implemented_unverified | MM-452 preserved in new spec only | preserve across all artifacts and final report | traceability check |
| DESIGN-REQ-006-DESIGN-REQ-009 | partial | context/tool surfaces exist; freshness guard missing | add guard and verification evidence | focused unit |
| DESIGN-REQ-022-DESIGN-REQ-023 | implemented_verified | policy-mediated evidence access and bounded degradation tests | preserve | focused unit |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: SQLAlchemy async ORM, existing Temporal artifact service, remediation link/context models  
**Storage**: Existing `TemporalExecutionRemediationLink`, `TemporalExecutionCanonicalRecord`, and Temporal artifact tables only; no new persistent tables  
**Unit Testing**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py`
**Integration Testing**: `./tools/test_integration.sh` for compose-backed `integration_ci` where Docker is available  
**Target Platform**: MoonMind Temporal control-plane/runtime service boundary  
**Project Type**: Python service modules plus unit/integration tests  
**Performance Goals**: Evidence context and guard reads stay bounded and ref-based; no unbounded log or artifact bodies in workflow history  
**Constraints**: No raw Jira credentials, no raw storage paths/URLs/secrets in durable evidence, no action execution registry in this story, no new tables  
**Scale/Scope**: One independently testable remediation runtime evidence story

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. Adds a thin MoonMind evidence/guard boundary over existing runtime records.
- II. One-Click Agent Deployment: PASS. No new service dependency.
- III. Avoid Vendor Lock-In: PASS. No provider-specific behavior.
- IV. Own Your Data: PASS. Evidence remains MoonMind-owned artifacts and projections.
- V. Skills Are First-Class: PASS. The typed service boundary is reusable by remediation skills.
- VI. Replaceability: PASS. The guard is a compact service method, not a cognitive scaffold.
- VII. Runtime Configurability: PASS. Evidence and action policy refs remain payload/context-driven.
- VIII. Modular Architecture: PASS. Changes stay in remediation temporal boundary modules and tests.
- IX. Resilient by Default: PASS. Missing evidence and target records fail fast or degrade explicitly.
- X. Continuous Improvement: PASS. Verification artifacts record remaining risk and evidence.
- XI. Spec-Driven Development: PASS. MM-452 artifacts define and trace the work.
- XII. Docs Separation: PASS. Jira input and implementation plan stay under `docs/tmp` and `specs/`.
- XIII. Pre-release Compatibility: PASS. Adds the canonical guard surface without compatibility aliases.

## Project Structure

### Documentation (this feature)

```text
specs/227-remediation-evidence-bundles/
├── checklists/requirements.md
├── contracts/remediation-evidence-bundles.md
├── data-model.md
├── plan.md
├── quickstart.md
├── research.md
├── spec.md
├── tasks.md
└── verification.md
```

### Source Code (repository root)

```text
moonmind/workflows/temporal/
├── remediation_context.py
├── remediation_tools.py
└── __init__.py

tests/unit/workflows/temporal/
└── test_remediation_context.py
```

**Structure Decision**: Extend the existing Temporal remediation context/evidence service modules because MM-452 is a runtime boundary story, not a new API transport or storage subsystem.

## Complexity Tracking

None.
