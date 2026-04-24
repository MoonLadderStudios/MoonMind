# Implementation Plan: Proposal Promotion Preset Provenance

**Branch**: `202-document-proposal-promotion` | **Date**: 2026-04-17 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `specs/202-document-proposal-promotion/spec.md`

## Summary

Implement MM-388 by updating the canonical Task Proposal System runtime contract so proposal promotion preserves reliable preset provenance metadata while validating and submitting the reviewed flat task payload. The technical approach is to update `docs/Tasks/TaskProposalSystem.md` as the desired-state proposal contract, then validate that the contract covers advisory provenance semantics, optional authored preset fields, no default live re-expansion, generator guidance, observability states, and MM-388 traceability.

## Technical Context

**Language/Version**: Markdown documentation for MoonMind runtime task proposal architecture
**Primary Dependencies**: Existing `docs/Tasks/TaskProposalSystem.md`, preserved MM-388 Jira preset brief, adjacent preset-composability MoonSpec artifacts
**Storage**: No new persistent storage; documents describe semantics for existing proposal payloads and task snapshot metadata
**Unit Testing**: Documentation contract checks with `rg` against `docs/Tasks/TaskProposalSystem.md` and generated MoonSpec artifacts
**Integration Testing**: End-to-end documentation validation by reviewing the canonical Task Proposal System contract against MM-388 acceptance scenarios and final `/moonspec-verify`
**Target Platform**: Proposal generation, proposal detail/observability, and proposal promotion surfaces
**Project Type**: Runtime task proposal architecture contract documentation
**Performance Goals**: No runtime performance impact; promotion avoids live preset expansion by default
**Constraints**: Preserve canonical docs as desired-state documentation, keep volatile planning under `local-only handoffs` or `specs/`, do not introduce compatibility aliases or hidden runtime fallback behavior, and preserve Jira issue key MM-388 in artifacts
**Scale/Scope**: One canonical documentation file plus MoonSpec artifacts for one independently testable story

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. The story keeps proposal promotion on the standard Temporal-backed create path and does not introduce a separate execution model.
- II. One-Click Agent Deployment: PASS. No services, secrets, dependencies, or setup steps are added.
- III. Avoid Vendor Lock-In: PASS. The proposal contract is provider-neutral.
- IV. Own Your Data: PASS. Preset provenance remains MoonMind-owned task snapshot metadata, not live external lookup.
- V. Skills Are First-Class and Easy to Add: PASS. The story keeps preset provenance distinct from agent instruction bundles and executable tools.
- VI. Replaceable Scaffolding: PASS. Promotion relies on stable flat payload contracts rather than re-running volatile preset expansion logic.
- VII. Runtime Configurability: PASS. No hardcoded runtime configuration is introduced.
- VIII. Modular Architecture: PASS. Generator, storage, promotion, and UI/observability semantics remain distinct.
- IX. Resilient by Default: PASS. Promotion avoids drift caused by changed live preset catalog state.
- X. Continuous Improvement: PASS. Verification evidence identifies remaining documentation or runtime-contract gaps.
- XI. Spec-Driven Development: PASS. This one-story MoonSpec drives the change.
- XII. Canonical Documentation Separation: PASS. Canonical docs describe desired state; migration notes remain outside canonical docs.
- XIII. Pre-Release Compatibility Policy: PASS. No compatibility shim or semantic fallback is introduced.

## Project Structure

### Documentation (this feature)

```text
specs/202-document-proposal-promotion/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│ └── proposal-promotion-preset-provenance.md
├── tasks.md
├── verification.md
└── checklists/
 └── requirements.md
```

### Source Code (repository root)

```text
docs/
└── Tasks/
 └── TaskProposalSystem.md

└── jira-orchestration-inputs/
 └── MM-388-moonspec-orchestration-input.md
```

**Structure Decision**: Update `docs/Tasks/TaskProposalSystem.md` because MM-388 targets proposal payload, generator, promotion, and proposal detail semantics. Do not create replacement canonical docs or move volatile planning into `docs/`.

## Complexity Tracking

No constitution violations.

## Setup Notes

- The input was classified as a single-story feature request from `spec.md` (Input).
- `.specify/feature.json` points to `specs/202-document-proposal-promotion`.
