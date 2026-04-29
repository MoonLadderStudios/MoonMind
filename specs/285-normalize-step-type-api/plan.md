# Implementation Plan: Normalize Step Type API and Executable Submission Payloads

**Branch**: `285-normalize-step-type-api` | **Date**: 2026-04-29 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `specs/285-normalize-step-type-api/spec.md`

## Summary

MM-566 requires Step Type payloads to remain explicit across draft reconstruction and executable submission. Repo inspection shows the executable submission boundary is largely implemented by `specs/279-submit-discriminated-executable-payloads`, including backend validation for Tool and Skill steps and rejection of Preset, Activity, and mixed payloads. The remaining gap is draft reconstruction for editable Temporal task inputs: explicit `type: "preset"` draft steps are currently inferred as Skill because the frontend draft model does not preserve Step Type. The plan adds focused draft reconstruction coverage and a narrow frontend model update, then verifies existing backend submission tests and the canonical Step Type documentation.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `TaskStepSpec` validates explicit executable `type`; `frontend/src/lib/temporalTaskEditing.ts` does not preserve explicit draft Step Type. | Add draft Step Type fields and reconstruction logic. | frontend unit |
| FR-002 | implemented_verified | `TaskStepSpec._reject_forbidden_step_overrides` accepts `tool` and `skill`; `tests/unit/workflows/tasks/test_task_contract.py` covers executable Tool and Skill. | No new implementation. | final unit |
| FR-003 | implemented_verified | Backend tests reject `preset`, `activity`, and `Activity` Step Types. | No new implementation. | final unit |
| FR-004 | missing | `draftStepFrom` infers Skill when a draft step is not a tool step. | Preserve `type: "preset"` and preset payload during draft reconstruction. | frontend unit |
| FR-005 | implemented_verified | Backend tests reject Tool steps with Skill payloads and Skill steps with non-skill Tool payloads. | No new implementation. | final unit |
| FR-006 | partial | Legacy draft reconstruction keeps old Tool/Skill fields readable but lacks explicit Step Type output. | Keep legacy inference while adding explicit discriminator preservation. | frontend unit |
| FR-007 | partial | `docs/Steps/StepTypes.md` uses Step Type terminology but contains a duplicated migration bullet. | Fix the contradictory duplicate and verify no new output uses Activity as primary Step Type. | docs check + final unit |
| SCN-001 | partial | Draft Tool reconstruction derives `skillId` from `tool`, but no explicit Step Type assertion. | Add Tool draft reconstruction assertion. | frontend unit |
| SCN-002 | partial | Draft Skill reconstruction derives `skillId` from `skill`, but no explicit Step Type assertion. | Add Skill draft reconstruction assertion. | frontend unit |
| SCN-003 | missing | Preset draft steps are not represented in `TemporalSubmissionDraft`. | Add Preset draft reconstruction test and implementation. | frontend unit |
| SCN-004 | implemented_verified | Backend task contract tests reject non-executable Step Types. | No new implementation. | final unit |
| SCN-005 | implemented_verified | Backend task contract tests reject conflicting payloads. | No new implementation. | final unit |
| SCN-006 | partial | Legacy reconstruction is supported, but explicit Step Type output is not asserted. | Add regression assertion for legacy skill inference. | frontend unit |
| DESIGN-REQ-012 | partial | Executable boundary implemented; draft Preset representation missing. | Implement draft Preset reconstruction. | frontend unit |
| DESIGN-REQ-014 | implemented_verified | Existing task contract validation covers mixed payload rejection. | No new implementation. | final unit |
| DESIGN-REQ-015 | partial | Existing model preserves identity/title/instructions; explicit draft discriminator missing. | Add `stepType` and `preset` draft fields. | frontend unit |
| DESIGN-REQ-019 | partial | Compatibility readers exist; doc duplicate needs cleanup. | Keep legacy inference and fix doc duplicate. | frontend unit + docs check |
| SC-001 | missing | No draft reconstruction test covers all three Step Types. | Add frontend unit test. | frontend unit |
| SC-002 | implemented_verified | Backend tests cover executable validation matrix. | No new implementation. | final unit |
| SC-003 | partial | Legacy tests cover edit reconstruction but not Step Type inference. | Add legacy inference assertion. | frontend unit |
| SC-004 | partial | Docs mostly converge, but duplicate migration bullet exists. | Fix duplicate and verify terminology. | docs check |
| SC-005 | missing | MM-566 verification artifact not yet written. | Write final verification. | moonspec verify |

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React for Mission Control task draft editing
**Primary Dependencies**: Pydantic v2 task contract models, React/Vitest test harness, existing Temporal task editing helpers
**Storage**: No new persistent storage; existing task input snapshots and execution parameters only
**Unit Testing**: `./tools/test_unit.sh`; focused Vitest command through `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`
**Integration Testing**: No compose-backed integration test is required for this narrow draft/contract story; integration-boundary evidence comes from frontend task reconstruction and existing backend executable submission validation
**Target Platform**: MoonMind API task contract and Mission Control Create/Edit task surfaces
**Project Type**: Web service plus frontend application
**Performance Goals**: Draft reconstruction remains synchronous and linear in submitted step count
**Constraints**: Preserve MM-566 traceability; do not make Preset steps executable by default; do not use Activity as a user-facing Step Type
**Scale/Scope**: Existing task step limits and draft reconstruction paths only

## Constitution Check

- Orchestrate, Don't Recreate: PASS. The change preserves MoonMind's typed task orchestration boundary.
- One-Click Agent Deployment: PASS. No new required external dependency or deployment prerequisite.
- Avoid Vendor Lock-In: PASS. Step Type semantics are provider-neutral.
- Own Your Data: PASS. Existing task snapshots remain portable JSON-like payloads.
- Skills Are First-Class and Easy to Add: PASS. Skill steps remain explicit and separate from Tool and Preset steps.
- Thin Scaffolding, Thick Contracts: PASS. The work strengthens discriminated payload contracts rather than adding orchestration scaffolding.
- Powerful Runtime Configurability: PASS. No hardcoded runtime mode or model behavior changes.
- Modular and Extensible Architecture: PASS. Changes stay in task draft reconstruction and task contract boundaries.
- Resilient by Default: PASS. Invalid executable payloads continue to fail before runtime materialization.
- Facilitate Continuous Improvement: PASS. Final verification records the outcome and evidence.
- Spec-Driven Development: PASS. MM-566 artifacts precede the narrow code/doc changes.
- Canonical Documentation Separation: PASS. Desired-state docs are adjusted only to remove contradiction; migration notes stay in this spec.
- Compatibility Policy: PASS. No new compatibility alias is introduced; legacy readability remains scoped to existing reader behavior.

## Project Structure

### Documentation (this feature)

```text
specs/285-normalize-step-type-api/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── contracts/
│   └── step-type-payloads.md
├── checklists/
│   └── requirements.md
├── quickstart.md
├── tasks.md
└── verification.md
```

### Source Code (repository root)

```text
frontend/src/lib/temporalTaskEditing.ts
frontend/src/entrypoints/task-create.test.tsx
docs/Steps/StepTypes.md
tests/unit/workflows/tasks/test_task_contract.py
```

**Structure Decision**: Update frontend draft reconstruction for authoring/editing behavior, reuse existing backend executable submission validation, and clean the canonical Step Types doc contradiction.

## Complexity Tracking

No constitution violations.
