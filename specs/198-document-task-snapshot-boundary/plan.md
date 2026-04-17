# Implementation Plan: Document Task Snapshot And Compilation Boundary

**Branch**: `198-document-task-snapshot-boundary` | **Date**: 2026-04-17 | **Spec**: [spec.md](spec.md)  
**Input**: Single-story feature specification from `specs/198-document-task-snapshot-boundary/spec.md`

## Summary

Implement MM-385 by updating the canonical task architecture contract so preset compilation is explicitly a control-plane phase and submitted tasks preserve authored preset metadata alongside resolved flat steps. The technical approach is to edit `docs/Tasks/TaskArchitecture.md` only, keeping it desired-state documentation while making the runtime boundary testable through documentation contract checks. Verification focuses on source traceability, required contract terms, snapshot durability language, and execution-plane separation from live preset lookup.

## Technical Context

**Language/Version**: Markdown documentation for MoonMind runtime architecture  
**Primary Dependencies**: Existing `docs/Tasks/TaskArchitecture.md`, preserved MM-385 Jira preset brief, existing MoonSpec artifacts  
**Storage**: No new persistent storage; documents describe existing task input snapshot and payload semantics  
**Unit Testing**: Documentation contract checks with `rg` against `docs/Tasks/TaskArchitecture.md` and generated MoonSpec artifacts  
**Integration Testing**: End-to-end documentation validation by reviewing the canonical task architecture contract against MM-385 acceptance scenarios and running final `/moonspec-verify`  
**Target Platform**: MoonMind control plane and managed runtime documentation contract  
**Project Type**: Runtime architecture contract documentation  
**Performance Goals**: No runtime performance impact; documentation must identify compile-time preset composition boundaries without adding execution-plane work  
**Constraints**: Preserve canonical docs as desired-state documentation, keep volatile planning under `docs/tmp/`, do not introduce compatibility aliases or hidden runtime fallback behavior, and preserve Jira issue key MM-385 in artifacts  
**Scale/Scope**: One canonical documentation file plus MoonSpec artifacts for one independently testable story

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. The story clarifies orchestration control-plane responsibilities and keeps agent/runtime workers from owning preset expansion.
- II. One-Click Agent Deployment: PASS. No services, secrets, dependencies, or setup steps are added.
- III. Avoid Vendor Lock-In: PASS. The contract is provider-neutral and applies before runtime adapter execution.
- IV. Own Your Data: PASS. The snapshot contract preserves authored task data and provenance under operator-controlled MoonMind artifacts.
- V. Skills Are First-Class and Easy to Add: PASS. Preset metadata and step provenance remain compatible with skill-backed task steps.
- VI. Replaceable Scaffolding: PASS. The contract isolates volatile preset composition from execution workers and keeps verification evidence explicit.
- VII. Runtime Configurability: PASS. No hardcoded runtime configuration is introduced.
- VIII. Modular Architecture: PASS. Control-plane compilation, task snapshot durability, and execution-plane consumption remain separate boundaries.
- IX. Resilient by Default: PASS. Submitted tasks remain executable and reconstructible after catalog changes.
- X. Continuous Improvement: PASS. Verification evidence will identify any remaining documentation or runtime-contract gaps.
- XI. Spec-Driven Development: PASS. This one-story MoonSpec drives the change.
- XII. Canonical Documentation Separation: PASS. Canonical docs describe desired state; migration or orchestration notes remain under `docs/tmp/` and `specs/`.
- XIII. Pre-Release Compatibility Policy: PASS. No compatibility layer or semantic fallback is introduced.

## Project Structure

### Documentation (this feature)

```text
specs/198-document-task-snapshot-boundary/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── task-snapshot-compilation-boundary.md
├── tasks.md
├── verification.md
└── checklists/
    └── requirements.md
```

### Source Code (repository root)

```text
docs/
└── Tasks/
    └── TaskArchitecture.md

docs/tmp/
└── jira-orchestration-inputs/
    └── MM-385-moonspec-orchestration-input.md
```

**Structure Decision**: Update `docs/Tasks/TaskArchitecture.md` because MM-385 targets the task architecture system snapshot, task contract normalization, snapshot durability, and execution-plane boundary. Do not create replacement canonical docs or move volatile planning into `docs/`.

## Complexity Tracking

No constitution violations.

## Setup Notes

- `.specify/scripts/bash/setup-plan.sh --json` was attempted but rejected the managed branch name `mm-385-76c8ce17` because it expects a branch like `001-feature-name`.
- Continued from `.specify/feature.json`, which points to `specs/198-document-task-snapshot-boundary`.
