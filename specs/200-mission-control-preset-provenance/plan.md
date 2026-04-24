# Implementation Plan: Mission Control Preset Provenance Surfaces

**Branch**: `200-mission-control-preset-provenance` | **Date**: 2026-04-17 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `specs/200-mission-control-preset-provenance/spec.md`

## Summary

Implement MM-387 by updating the canonical Mission Control architecture contract so task previews, list/detail surfaces, and create/edit flows can explain preset-derived work without implying nested runtime behavior. The technical approach is to update `docs/UI/MissionControlArchitecture.md` as the desired-state UI runtime contract, then validate that the contract covers preset provenance presentation, flat execution ordering, submit-time unresolved include rejection, evidence hierarchy, and vocabulary boundaries.

## Technical Context

**Language/Version**: Markdown documentation for MoonMind runtime UI architecture
**Primary Dependencies**: Existing `docs/UI/MissionControlArchitecture.md`, preserved MM-387 Jira preset brief, existing MoonSpec artifacts
**Storage**: No new persistent storage; documents describe UI semantics for existing task snapshots, step provenance, and execution evidence
**Unit Testing**: Documentation contract checks with `rg` against `docs/UI/MissionControlArchitecture.md` and generated MoonSpec artifacts
**Integration Testing**: End-to-end documentation validation by reviewing the canonical Mission Control contract against MM-387 acceptance scenarios and final `/moonspec-verify`
**Target Platform**: Mission Control task list, detail, create/edit, and submit surfaces
**Project Type**: Runtime UI architecture contract documentation
**Performance Goals**: No runtime performance impact; provenance explanations remain metadata and do not add runtime preset expansion work
**Constraints**: Preserve canonical docs as desired-state documentation, keep volatile planning under `local-only handoffs` or `specs/`, do not introduce compatibility aliases or hidden runtime fallback behavior, and preserve Jira issue key MM-387 in artifacts
**Scale/Scope**: One canonical documentation file plus MoonSpec artifacts for one independently testable story

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. The story keeps preset composition in control-plane semantics and prevents UI language from implying nested runtime execution.
- II. One-Click Agent Deployment: PASS. No services, secrets, dependencies, or setup steps are added.
- III. Avoid Vendor Lock-In: PASS. The UI contract is provider-neutral.
- IV. Own Your Data: PASS. Preset provenance is presented from MoonMind-owned task snapshot and execution evidence metadata.
- V. Skills Are First-Class and Easy to Add: PASS. The story keeps agent skills distinct from presets and runtime concepts.
- VI. Replaceable Scaffolding: PASS. The contract treats provenance as explanatory metadata and preserves flat execution evidence.
- VII. Runtime Configurability: PASS. No hardcoded runtime configuration is introduced.
- VIII. Modular Architecture: PASS. Preview, detail, submit, and evidence presentation boundaries remain separate from execution workers.
- IX. Resilient by Default: PASS. The UI contract avoids live preset lookup or unresolved include submission at runtime.
- X. Continuous Improvement: PASS. Verification evidence identifies remaining documentation or runtime-contract gaps.
- XI. Spec-Driven Development: PASS. This one-story MoonSpec drives the change.
- XII. Canonical Documentation Separation: PASS. Canonical docs describe desired state; migration notes remain outside canonical docs.
- XIII. Pre-Release Compatibility Policy: PASS. No compatibility shim or semantic fallback is introduced.

## Project Structure

### Documentation (this feature)

```text
specs/200-mission-control-preset-provenance/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│ └── mission-control-preset-provenance.md
├── tasks.md
├── verification.md
└── checklists/
 └── requirements.md
```

### Source Code (repository root)

```text
docs/
└── UI/
 └── MissionControlArchitecture.md

└── jira-orchestration-inputs/
 └── MM-387-moonspec-orchestration-input.md
```

**Structure Decision**: Update `docs/UI/MissionControlArchitecture.md` because MM-387 targets Mission Control preview, edit, list, detail, submit, evidence, and vocabulary behavior. Do not create replacement canonical docs or move volatile planning into `docs/`.

## Complexity Tracking

No constitution violations.

## Setup Notes

- The input was classified as a single-story feature request from `spec.md` (Input).
- `.specify/feature.json` points to `specs/200-mission-control-preset-provenance`.
