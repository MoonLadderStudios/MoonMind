# Implementation Plan: Submit Flattened Executable Steps with Provenance

**Branch**: `292-submit-flattened-executable-steps-with-provenance` | **Date**: 2026-05-01 | **Spec**: [spec.md](./spec.md)  
**Input**: Single-story feature specification from `/work/agent_jobs/mm:d840afab-0992-4107-91d1-bcee9ae1b804/repo/specs/292-submit-flattened-executable-steps-with-provenance/spec.md`

## Summary

MM-579 requires preset-derived work to submit and promote as reviewed flat executable Tool and Skill steps while preserving provenance for audit, grouping, reconstruction, and explicit refresh. Repo inspection shows adjacent Step Type work already previews/applies presets in the Create page, rejects unresolved Preset steps at executable boundaries, materializes explicit Tool/Skill steps without live preset lookup, and preserves proposal provenance in several paths. The remaining delivery risk is MM-579-specific provenance completeness, especially the canonical `presetVersion` field and include-path/original-step coverage across submitted steps and promoted proposals. The plan is TDD-first: add focused unit and integration-boundary tests for flat Tool/Skill submission, provenance metadata, proposal promotion, and explicit refresh behavior, then patch the existing task contract, Create page mapping, and proposal surfaces only where those tests expose drift.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_unverified | Create page MM-578 tests preview/apply generated Tool and Skill steps; submission test asserts generated Tool binding only. | Add MM-579-focused submission test proving all applied generated steps submit as Tool/Skill and no Preset placeholder remains; patch serialization if needed. | frontend integration |
| FR-002 | implemented_verified | `TaskStepSpec._reject_forbidden_step_overrides` rejects `type: "preset"`; Create page blocks unresolved Preset submission; proposal service rejects unresolved Preset steps. | No new implementation beyond final verification. | unit + frontend integration |
| FR-003 | partial | `TaskStepSource` preserves `kind`, `presetId`, `includePath`, and `originalStepId`, but current models/tests use `version` rather than the source-design `presetVersion` field. | Add canonical `presetVersion` preservation tests and update task/proposal/frontend metadata mapping if tests fail. | unit + frontend integration |
| FR-004 | partial | Proposal preview reports preset provenance and Create page displays source labels, but MM-579-specific audit/grouping/reconstruction evidence for complete source metadata is missing. | Add tests that complete preset source metadata remains visible to review/proposal surfaces; patch preview/labels if needed. | API unit + frontend integration |
| FR-005 | implemented_verified | Runtime planner maps explicit Tool/Skill steps based on executable type and carries `source` only as metadata; unit tests cover Tool and Skill plan nodes. | No new implementation. | unit |
| FR-006 | implemented_verified | Create page preset preview/apply tests cover expansion before mutation, validation failure preserving the draft, and unresolved Preset blocking. | No new implementation unless MM-579 provenance tests expose expansion metadata loss. | frontend integration |
| FR-007 | partial | `TaskProposalService.promote_proposal` validates stored `CanonicalTaskPayload`, but existing provenance-preservation tests allow legacy source-only proposal steps without explicit Tool/Skill type. | Add promotion tests requiring preset-derived proposals to contain flat executable Tool/Skill steps; patch validation if needed. | unit |
| FR-008 | partial | Promotion validates stored payloads and rejects unresolved Preset steps; no live re-expansion call is evident, but flat executable proposal enforcement is incomplete. | Verify promotion uses stored flat payload and zero live catalog expansion; strengthen validation if proposal tests fail. | unit |
| FR-009 | implemented_unverified | Create page has reapply-needed messaging and stale preview invalidation tests adjacent to this story. | Add MM-579-focused explicit refresh/preview regression for draft refresh; document proposal refresh as out of implementation scope unless existing product surface exposes it. | frontend integration |
| FR-010 | partial | `spec.md` preserves MM-579 and original preset brief; planning artifacts are being generated. | Preserve MM-579 in plan, research, data model, contract, quickstart, tasks, verification, commit, and PR metadata. | artifact review |
| SCN-001 | implemented_unverified | MM-578 tests prove applied preview replaces a Preset placeholder with generated editable steps. | Add MM-579 submission assertion for final flat payload shape. | frontend integration |
| SCN-002 | partial | Task contract and proposal tests preserve some source metadata. | Add complete provenance metadata tests, including canonical preset version and include path. | unit + frontend integration |
| SCN-003 | implemented_verified | Runtime planner uses executable Tool/Skill steps and does not depend on preset provenance. | No new implementation. | unit |
| SCN-004 | partial | Proposal promotion validates stored payload and rejects `type: "preset"`, but does not yet prove all preset-derived proposal steps are explicit Tool/Skill steps. | Add flat proposal payload promotion tests and code contingency. | unit |
| SCN-005 | implemented_unverified | Reapply-needed UI behavior exists, but MM-579-specific explicit preview before refresh needs targeted evidence. | Add focused Create-page regression around explicit preview/validation before refresh changes stored draft steps. | frontend integration |
| DESIGN-REQ-004 | implemented_unverified | Preset preview/apply creates concrete Tool and Skill steps in the Create page. | Verify final submitted payload remains concrete Tool/Skill only. | frontend integration |
| DESIGN-REQ-006 | partial | Source metadata exists but canonical `presetVersion` coverage is missing. | Add metadata model and UI/proposal preservation coverage. | unit + frontend integration |
| DESIGN-REQ-015 | implemented_verified | Runtime/task contract and UI block unresolved Preset steps at executable submission boundaries. | No new implementation beyond final verification. | unit + frontend integration |
| DESIGN-REQ-016 | partial | Runtime ignores source metadata for execution and preview surfaces expose provenance summary; complete audit/reconstruction metadata coverage is missing. | Add complete provenance and review-surface tests. | unit + API unit + frontend integration |
| DESIGN-REQ-023 | partial | Promotion validates stored payloads and rejects Preset steps; explicit flat proposal enforcement and refresh evidence need strengthening. | Add proposal promotion and refresh tests; patch validation if needed. | unit + frontend integration |
| SC-001 | implemented_verified | Existing task contract and Create page tests reject unresolved Preset and accept executable Tool/Skill shapes. | No new implementation beyond final verification. | unit + frontend integration |
| SC-002 | partial | Source metadata preservation exists, but complete required metadata including `presetVersion` is not fully covered. | Add provenance completeness tests. | unit + frontend integration |
| SC-003 | implemented_verified | Runtime planner does not require live preset lookup and uses explicit executable steps. | No new implementation. | unit |
| SC-004 | partial | Promotion has validation and unresolved Preset rejection; zero automatic live re-expansion should be locked by tests. | Add promotion no-reexpand/flat-payload tests. | unit |
| SC-005 | implemented_unverified | Reapply-needed and stale-preview UI behavior exists. | Add MM-579 refresh preview regression. | frontend integration |
| SC-006 | partial | MM-579 traceability is present in `spec.md`; planning and downstream artifacts still need preservation. | Carry traceability through generated artifacts and final verification. | artifact review |

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React for Mission Control Create page behavior  
**Primary Dependencies**: Pydantic v2 task contract models, FastAPI proposal API router, existing task proposal service, React, TanStack Query, Vitest, Testing Library, pytest  
**Storage**: Existing task submission payloads, task proposal rows, and artifact-backed task input snapshots only; no new persistent storage planned  
**Unit Testing**: Focused Python unit tests through `./tools/test_unit.sh tests/unit/workflows/tasks/test_task_contract.py tests/unit/workflows/task_proposals/test_service.py tests/unit/api/routers/test_task_proposals.py tests/unit/workflows/temporal/test_temporal_worker_runtime.py`  
**Integration Testing**: Create page render/submission integration-boundary coverage through `./tools/test_unit.sh --dashboard-only --ui-args frontend/src/entrypoints/task-create.test.tsx`; compose-backed `integration_ci` is not required unless implementation touches worker topology or persisted execution boundaries  
**Target Platform**: MoonMind task authoring, executable task contract, runtime planning, and proposal promotion surfaces  
**Project Type**: Web application plus Python service contracts  
**Performance Goals**: Preset apply and promotion validation remain linear in step count; refresh requires explicit operator action and does not perform background catalog expansion during promotion  
**Constraints**: Preserve MM-579 traceability; keep Preset non-executable by default; do not introduce live preset lookup for runtime correctness; do not add hidden compatibility aliases for unsupported Step Types  
**Scale/Scope**: One runtime behavior story spanning Create page preset application, canonical task payload validation, runtime materialization, and proposal promotion/review metadata

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. Work strengthens existing task/preset/proposal orchestration contracts rather than introducing a new agent layer.
- II. One-Click Agent Deployment: PASS. No new required external services, credentials, or deployment prerequisites.
- III. Avoid Vendor Lock-In: PASS. Tool, Skill, Preset, and proposal behavior remain provider-neutral.
- IV. Own Your Data: PASS. Provenance and proposal data remain in MoonMind-owned task payloads and proposal records.
- V. Skills Are First-Class and Easy to Add: PASS. Skill steps remain executable first-class steps distinct from Tool and Preset.
- VI. Scientific Method: PASS. Plan requires focused failing tests before production changes and final verification evidence.
- VII. Runtime Configurability: PASS. No hardcoded model, provider, or deployment configuration is introduced.
- VIII. Modular and Extensible Architecture: PASS. Planned work stays within existing Create page, task contract, runtime planner, and proposal service/API boundaries.
- IX. Resilient by Default: PASS. Invalid unresolved Preset payloads fail before runtime execution or proposal promotion.
- X. Facilitate Continuous Improvement: PASS. Artifacts preserve MM-579 traceability and will feed final verification.
- XI. Spec-Driven Development: PASS. `spec.md` and this plan precede MM-579 implementation tasks.
- XII. Canonical Documentation Separation: PASS. Runtime rollout details stay in feature artifacts; canonical docs are source requirements only.
- XIII. Pre-release Compatibility Policy: PASS. Unsupported executable Step Type values continue to fail fast; canonical metadata changes should update all callers/tests/docs in one change rather than adding silent semantic aliases.

## Project Structure

### Documentation (this feature)

```text
specs/292-submit-flattened-executable-steps-with-provenance/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── flattened-executable-provenance.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
frontend/src/entrypoints/task-create.tsx
frontend/src/entrypoints/task-create.test.tsx
moonmind/workflows/tasks/task_contract.py
tests/unit/workflows/tasks/test_task_contract.py
moonmind/workflows/task_proposals/service.py
tests/unit/workflows/task_proposals/test_service.py
api_service/api/routers/task_proposals.py
tests/unit/api/routers/test_task_proposals.py
moonmind/workflows/temporal/worker_runtime.py
tests/unit/workflows/temporal/test_temporal_worker_runtime.py
```

**Structure Decision**: Use the existing Create page preset preview/apply flow for authoring and submission behavior, the canonical task contract for executable payload validation and provenance shape, the runtime planner for source-independent execution, and proposal service/API tests for promotion and review behavior.

## Complexity Tracking

No constitution violations.
