# Implementation Plan: Document Flattened Plan Execution Contract

**Branch**: `199-document-flattened-plan-contract` | **Date**: 2026-04-17 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `specs/199-document-flattened-plan-contract/spec.md`

## Summary

Implement MM-386 by updating the canonical execution tool and plan contract so stored plans are explicitly flat execution graphs after authoring-time preset composition, unresolved include objects are invalid runtime inputs, and optional source provenance remains traceability metadata rather than executable logic. The technical approach is to edit `docs/Tasks/SkillAndPlanContracts.md` only unless planning or verification discovers executable validation drift. Verification focuses on documentation contract checks, source traceability, validation-rule language, and final MoonSpec verification against the preserved MM-386 Jira preset brief.

## Technical Context

**Language/Version**: Markdown documentation for MoonMind runtime plan contracts
**Primary Dependencies**: Existing `docs/Tasks/SkillAndPlanContracts.md`, preserved MM-386 Jira preset brief, existing MoonSpec artifacts
**Storage**: No new persistent storage; documents describe stored plan artifact and provenance semantics
**Unit Testing**: Documentation contract checks with `rg` against `docs/Tasks/SkillAndPlanContracts.md` and generated MoonSpec artifacts
**Integration Testing**: End-to-end documentation validation by reviewing the canonical plan contract against MM-386 acceptance scenarios and running final `/moonspec-verify`
**Target Platform**: MoonMind control plane, plan producers, and runtime plan executor contract
**Project Type**: Runtime architecture contract documentation
**Performance Goals**: No runtime performance impact; documentation must preserve a flat executor model without adding runtime preset expansion work
**Constraints**: Preserve canonical docs as desired-state documentation, keep volatile planning under `docs/tmp/` and `specs/`, do not introduce compatibility aliases or hidden runtime fallback behavior, and preserve Jira issue key MM-386 in artifacts
**Scale/Scope**: One canonical documentation file plus MoonSpec artifacts for one independently testable story

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. The story clarifies plan execution boundaries without replacing agent or tool behavior.
- II. One-Click Agent Deployment: PASS. No services, secrets, dependencies, or setup steps are added.
- III. Avoid Vendor Lock-In: PASS. The plan contract is provider-neutral and applies to all plan producers and executors.
- IV. Own Your Data: PASS. Stored plan artifacts and provenance remain operator-controlled MoonMind artifacts.
- V. Skills Are First-Class and Easy to Add: PASS. The story preserves executable tool and agent instruction skill separation while documenting provenance for skill-derived plan nodes.
- VI. Replaceable Scaffolding: PASS. Authoring-time preset composition is isolated from the runtime executor so scaffolding can evolve without changing execution semantics.
- VII. Runtime Configurability: PASS. No hardcoded runtime configuration is introduced.
- VIII. Modular Architecture: PASS. Plan production, validation, and execution remain separate boundaries.
- IX. Resilient by Default: PASS. Stored plans fail fast on unresolved include objects and avoid hidden runtime fallback behavior.
- X. Continuous Improvement: PASS. Verification evidence will identify any remaining documentation or runtime-contract gaps.
- XI. Spec-Driven Development: PASS. This one-story MoonSpec drives the change.
- XII. Canonical Documentation Separation: PASS. Canonical docs describe desired state; migration or orchestration notes remain under `docs/tmp/` and `specs/`.
- XIII. Pre-Release Compatibility Policy: PASS. No compatibility layer or semantic fallback is introduced.

## Project Structure

### Documentation (this feature)

```text
specs/199-document-flattened-plan-contract/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── flattened-plan-execution-contract.md
├── tasks.md
└── checklists/
    └── requirements.md
```

### Source Code (repository root)

```text
docs/
└── Tasks/
    └── SkillAndPlanContracts.md

docs/tmp/
└── jira-orchestration-inputs/
    └── MM-386-moonspec-orchestration-input.md
```

**Structure Decision**: Update `docs/Tasks/SkillAndPlanContracts.md` because MM-386 targets plan artifact shape, PlanDefinition production rules, validation behavior, DAG semantics, and execution invariants. Do not create replacement canonical docs or move volatile planning into `docs/`.

## Complexity Tracking

No constitution violations.

## Setup Notes

- `scripts/bash/setup-plan.sh --json` was attempted but the repository stores the script under `.specify/scripts/bash/setup-plan.sh`.
- `.specify/scripts/bash/setup-plan.sh --json` was then attempted but rejected the managed branch name `mm-386-8a30061f` because it expects a branch like `001-feature-name`.
- Continued from `.specify/feature.json`, which points to `specs/199-document-flattened-plan-contract`.
