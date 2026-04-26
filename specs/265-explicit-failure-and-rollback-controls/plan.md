# Implementation Plan: Explicit Failure and Rollback Controls

**Branch**: `run-jira-orchestrate-for-mm-523-explicit-9be53fc7` | **Date**: 2026-04-26 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `specs/265-explicit-failure-and-rollback-controls/spec.md`

**Setup Note**: `.specify/scripts/bash/setup-plan.sh --json` could not complete because the active branch name is `run-jira-orchestrate-for-mm-523-explicit-9be53fc7`, while the script currently requires numbered feature-branch naming. Planning proceeds from the existing `.specify/feature.json` and feature directory.

## Summary

Implement `MM-523` by completing explicit failure and rollback controls for the existing `deployment.update_compose_stack` path. The current deployment executor already fails verification closed, emits audit/progress evidence, redacts artifacts, uses a max-attempts-one tool retry policy, and enforces allowlisted typed inputs. The missing work is to make failure classes first-class in outputs and recent actions, expose rollback eligibility only when trusted before-state evidence can safely identify a previous image target, submit rollback as the same admin-confirmed typed deployment update path, and add regression coverage proving no silent retry or rollback happens by default.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `DeploymentUpdateExecutor.execute()` returns failed/partial verification and command failures with reasons, but failure-class coverage is not complete for every documented class | add failure-class normalization and tests for invalid input, authorization, policy, lock, compose validation, image pull, service recreation, and verification failure | unit + integration |
| FR-002 | implemented_verified | `deployment_tools.py` sets `policies.retries.max_attempts = 1`; executor has no internal retry loop; `test_deployment_tool_contracts.py` covers retry policy | no new implementation; preserve with MM-523 traceability | final verify |
| FR-003 | implemented_unverified | API update submission queues a new typed update each time; no MM-523 test proves retry is a distinct explicit operator action after failure | add verification test showing a second submission is a new audited update request, not automatic retry continuation | unit/API |
| FR-004 | partial | `DeploymentOperationsService.queue_update()` already queues typed deployment updates to any policy-valid reference; there is no rollback-specific eligibility or submission metadata | model rollback as a normal typed update with rollback metadata and previous-image target from trusted evidence | unit + frontend |
| FR-005 | partial | admin authorization, reason validation, lock, before/after artifacts, and verification exist for normal updates; rollback-specific confirmation and evidence requirements are missing | require rollback confirmation and safe target evidence before using the same update path | unit + frontend + integration |
| FR-006 | missing | frontend has no rollback action and backend state response has no rollback eligibility | expose rollback eligibility derived from before-state/recent-action evidence and render rollback only when safe | unit + frontend |
| FR-007 | missing | no explicit unsafe/ambiguous rollback withholding behavior exists | add fail-closed eligibility states and UI/API tests for missing, ambiguous, or unsafe before-state evidence | unit + frontend |
| FR-008 | implemented_unverified | no automatic rollback path was found; existing code has no rollback call on failure | add regression tests proving failure does not enqueue rollback or mutate target without explicit policy/input | unit + integration |
| FR-009 | partial | executor audit output exists and frontend schema can render `recentActions`, but API stack state currently returns no recent deployment action records | surface failure/rollback records in deployment stack state using existing execution/artifact evidence; render status/reason/timestamps/links | API + frontend |
| FR-010 | implemented_verified | API schema forbids extra fields; service rejects non-allowlisted stack/repository/mode; executor rejects forbidden runner/path fields; existing tests cover these boundaries | no broadening; extend rollback through the same allowlisted update contract only | final verify |
| FR-011 | missing | MM-523 exists in `spec.md` and this plan, but downstream artifacts are not generated yet | preserve MM-523 in research, data model, contract, quickstart, tasks, implementation notes, verification, commit, and PR metadata | traceability grep |
| SC-001 | partial | command and verification failure tests exist; full failure-class matrix does not | add matrix tests | unit |
| SC-002 | implemented_verified | tool retry policy max attempts one is tested | final verification only | none beyond final verify |
| SC-003 | implemented_unverified | repeated API submission naturally creates a new execution; no explicit post-failure proof | add API/service test | unit/API |
| SC-004 | partial | normal update has admin/reason/lock/artifact/verification; rollback path missing | add rollback request/eligibility tests | unit + frontend |
| SC-005 | missing | no rollback eligibility model | add backend eligibility tests and UI rendering tests | unit + frontend |
| SC-006 | implemented_unverified | no auto-rollback code found | add regression test around failed execution | unit + integration |
| SC-007 | partial | frontend can render recent actions if present; backend state lacks real recent failure/rollback data | add API and UI tests | API + frontend |
| SC-008 | partial | MM-523 traceability present in spec and plan | preserve through generated artifacts and final verification | traceability grep |
| DESIGN-REQ-001 | partial | several failure modes fail closed; not all documented classes are represented as explicit classes | add failure-class output mapping | unit |
| DESIGN-REQ-002 | implemented_verified | max-attempts-one policy and no executor retry loop | no new implementation | final verify |
| DESIGN-REQ-003 | partial | normal update path has required controls; rollback path missing | implement rollback through same typed update path with explicit metadata | unit + frontend |
| DESIGN-REQ-004 | missing | no rollback eligibility/action surface | add safe-evidence eligibility and silent rollback guard tests | unit + frontend |
| DESIGN-REQ-005 | implemented_verified | existing API/executor reject shell/path/runner/non-allowlisted inputs | preserve boundary | final verify |
| DESIGN-REQ-006 | partial | allowlists and artifacts exist; recent action visibility and rollback audit output are incomplete | add recent failure/rollback action surface using existing execution/artifact evidence | API + frontend |

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React for Mission Control Settings UI  
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy async session fixtures where API projection is needed, Temporal execution service/projection models, React, TanStack Query, Vitest, pytest  
**Storage**: Existing Temporal execution records and artifact-backed deployment evidence; no new persistent database tables planned  
**Unit Testing**: pytest via focused deployment executor/API tests and final `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`; Vitest via `./tools/test_unit.sh --ui-args frontend/src/components/settings/OperationsSettingsSection.test.tsx` or `npm run ui:test -- frontend/src/components/settings/OperationsSettingsSection.test.tsx` during focused iteration  
**Integration Testing**: hermetic pytest for `tests/integration/temporal/test_deployment_update_execution_contract.py` and any new `integration_ci` deployment dispatch coverage; final `./tools/test_integration.sh` when Docker is available  
**Target Platform**: MoonMind deployment-control runtime and Mission Control Settings Operations  
**Project Type**: backend workflow skill execution plus FastAPI API and React UI surface  
**Performance Goals**: rollback eligibility and recent-action projection should inspect bounded recent execution/artifact metadata and keep UI payloads compact  
**Constraints**: no raw credentials in artifacts/logs/UI; no arbitrary Docker or shell input; no silent rollback; no hidden compatibility aliases; rollback must reuse the typed allowlisted update path  
**Scale/Scope**: one deployment update story spanning executor failure classification, deployment operation API projection/submission metadata, and Settings Operations rollback controls

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. Work stays on the existing typed tool and Mission Control orchestration surface.
- II. One-Click Agent Deployment: PASS. Unit coverage uses fake runner/store/projection boundaries; integration coverage stays hermetic.
- III. Avoid Vendor Lock-In: PASS. No provider-exclusive behavior is introduced.
- IV. Own Your Data: PASS. Rollback eligibility uses operator-owned execution/artifact evidence.
- V. Skills Are First-Class and Easy to Add: PASS. The executable deployment tool contract remains the runtime boundary.
- VI. Replaceable Scaffolding: PASS. Rollback eligibility and failure classification remain explicit, small contracts.
- VII. Runtime Configurability: PASS. Unsupported values fail closed; rollback is not silently enabled.
- VIII. Modular and Extensible Architecture: PASS. Changes are scoped to deployment execution, deployment operations API, and Operations UI.
- IX. Resilient by Default: PASS. Failure handling is explicit and rollback is operator-driven.
- X. Facilitate Continuous Improvement: PASS. Recent actions and audit output improve operator diagnosis.
- XI. Spec-Driven Development: PASS. Plan follows the MM-523 spec and preserves traceability.
- XII. Canonical Documentation: PASS. Implementation details stay in feature artifacts, not canonical docs.
- XIII. Pre-Release Compatibility Policy: PASS. No internal compatibility aliases or deprecated rollback paths are planned.

## Project Structure

### Documentation (this feature)

```text
specs/265-explicit-failure-and-rollback-controls/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── deployment-failure-rollback-controls.md
└── tasks.md
```

### Source Code

```text
moonmind/workflows/skills/
├── deployment_execution.py
└── deployment_tools.py

api_service/api/routers/
└── deployment_operations.py

api_service/services/
└── deployment_operations.py

frontend/src/components/settings/
├── OperationsSettingsSection.tsx
└── OperationsSettingsSection.test.tsx

tests/unit/workflows/skills/
└── test_deployment_update_execution.py

tests/unit/api/routers/
└── test_deployment_operations.py

tests/integration/temporal/
└── test_deployment_update_execution_contract.py
```

**Structure Decision**: Extend the existing deployment update tool, deployment operation API, and Settings Operations card because MM-523 constrains behavior in the current deployment-control workflow rather than introducing a separate rollback service.

## Complexity Tracking

No constitution violations.
