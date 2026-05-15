# Implementation Plan: Full Frontend Runtime Proof Coverage

**Branch**: `[357-full-frontend-runtime-proof-coverage]` | **Date**: 2026-05-15 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `/specs/357-full-frontend-runtime-proof-coverage/spec.md`

## Summary

Deliver THOR-406 by adding runtime proof coverage for the THOR Tactics frontend menu architecture. The requested implementation is runtime validation, not new menu feature behavior: compile evidence, automation coverage for the full frontend flow set, map or entry smoke evidence, Docker fallback when local tooling is unavailable, and PR-ready evidence reporting. This managed workspace contains the MoonMind repository and no THOR Unreal project files, so every runtime requirement is classified as missing in this checkout and implementation is blocked until the workflow runs against the THOR Tactics repository or a workspace containing the target game source.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | missing | No `.uproject`, `TacticsEditor`, or THOR source found in this checkout | Add Tier 1 compile validation harness in target THOR workspace | integration |
| FR-002 | missing | No compile evidence recorder found | Record exact compile command, exit code, and summary | integration |
| FR-003 | missing | No Home startup automation source found | Add Home startup automation coverage | integration |
| FR-004 | missing | No generated Home navigation automation source found | Add generated Home navigation automation coverage | integration |
| FR-005 | missing | No Play panel automation source found | Add Play panel automation coverage | integration |
| FR-006 | missing | No Options panel automation source found | Add Options panel automation coverage | integration |
| FR-007 | missing | No modal automation source found | Add modal behavior automation coverage | integration |
| FR-008 | missing | No Online Co-op blocking automation source found | Add blocked Online Co-op automation coverage | integration |
| FR-009 | missing | No generated selection telemetry evidence found | Add telemetry capture and assertions | unit + integration |
| FR-010 | missing | No `/Game/Maps/MainMenu` map or frontend route source found | Add Tier 3 map or entry route smoke validation | integration |
| FR-011 | missing | No validation evidence record schema or output found in THOR source | Record commands, exit codes, and key `LogTactics` lines for every tier | unit + integration |
| FR-012 | missing | No local tooling or Docker fallback wrapper found for THOR validation | Add fallback handling before CI-only classification | unit + integration |
| FR-013 | missing | No THOR spec quickstart or PR evidence output exists here for THOR-406 | Record validation results in quickstart and PR-ready evidence output | integration |
| FR-014 | implemented_unverified | Spec scope states proof coverage only; no THOR implementation source present to inspect | Preserve non-goal guard in tasks and verification | final verify |
| SCN-001 | missing | No TacticsEditor compile validation source found | Prove Tier 1 compile evidence | integration |
| SCN-002 | missing | No frontend automation suite found | Prove all Tier 2 required flows | integration |
| SCN-003 | missing | No MainMenu map or active route source found | Prove Tier 3 runtime smoke | integration |
| SCN-004 | missing | No evidence recorder found | Prove evidence includes commands, exits, and logs | integration |
| SCN-005 | missing | No fallback wrapper found | Prove Docker fallback is attempted when local tooling is missing | unit + integration |
| SCN-006 | missing | No quickstart or PR-ready evidence output found | Prove reporting surfaces contain validation results | integration |
| SC-001 | missing | No compile evidence artifact found | Validate exact command and exit code | integration |
| SC-002 | missing | No combined Tier 2 evidence artifact found | Validate all seven flow areas in one run | integration |
| SC-003 | missing | No Tier 3 smoke evidence artifact found | Validate entry route success | integration |
| SC-004 | missing | No `LogTactics` evidence artifact found | Validate key log line capture or documented absence | integration |
| SC-005 | missing | No Docker fallback evidence artifact found | Validate fallback attempt before CI-only result | unit + integration |
| SC-006 | missing | No quickstart or PR-ready evidence artifact found | Validate all tiers are reported | integration |

## Technical Context

**Language/Version**: Target THOR Tactics Unreal/C++ project; exact Unreal Engine version must be taken from the target `.uproject` and build files when available  
**Primary Dependencies**: Unreal Editor commandlets, Automation Framework, target THOR frontend runtime, Docker fallback image or project build container where available  
**Storage**: Evidence files committed or attached as text/markdown artifacts; no persistent application storage expected  
**Unit Testing**: Target THOR C++ automation/unit-style tests for evidence record formatting, fallback decision logic, and telemetry parsing  
**Integration Testing**: Unreal editor automation, TacticsEditor compile, and map or entry smoke execution  
**Target Platform**: THOR Tactics developer/runtime validation environment  
**Project Type**: Unreal game frontend runtime validation  
**Performance Goals**: Validation should complete within normal CI limits and emit enough evidence to diagnose failures without rerunning locally  
**Constraints**: Do not implement new frontend features; capture exact commands, exit codes, and key `LogTactics` lines; attempt Docker fallback before declaring CI-only validation  
**Scale/Scope**: One proof coverage story covering three validation tiers and seven frontend flow areas

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The plan validates the target runtime using its native build and automation tools.
- **II. One-Click Agent Deployment**: PASS. Docker fallback is required when local tooling is unavailable.
- **III. Avoid Vendor Lock-In**: PASS. The Unreal-specific validation is target-project behavior; no MoonMind core provider coupling is introduced.
- **IV. Own Your Data**: PASS. Evidence is produced as local artifacts or PR-ready text owned by the workspace.
- **V. Skills Are First-Class and Easy to Add**: PASS. No skill runtime changes are required.
- **VI. Scientific Method**: PASS. The story is explicitly Hypothesize -> Execute -> Verify evidence coverage.
- **VII. Runtime Configurability**: PASS. Active entry route and local/Docker execution path must be discoverable or configurable in the target project.
- **VIII. Modular and Extensible Architecture**: PASS. Validation wrappers and evidence records are planned as separable test/runtime surfaces.
- **IX. Resilient by Default**: PASS. Fallback and evidence capture make failures diagnosable.
- **X. Facilitate Continuous Improvement**: PASS. The run ends with structured validation evidence.
- **XI. Spec-Driven Development**: PASS. This feature has spec, plan, tasks, and traceability.
- **XII. Canonical Documentation Separation**: PASS. Runtime validation notes live in feature artifacts, not canonical docs.
- **XIII. Pre-release Compatibility Policy**: PASS. No internal compatibility alias is planned.

## Project Structure

### Documentation (this feature)

```text
specs/357-full-frontend-runtime-proof-coverage/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── runtime-proof-evidence-contract.md
└── tasks.md
```

### Source Code (target THOR repository)

```text
Source/ThorTactics/
├── Frontend/
│   ├── TacticsFrontendRuntimeProof.*
│   ├── TacticsFrontendTelemetry.*
│   └── TacticsFrontendEvidence.*
├── Tests/
│   └── Frontend/
│       ├── FrontendRuntimeProofUnitTests.cpp
│       └── FrontendRuntimeProofAutomationTest.cpp
└── Tools/
    └── FrontendRuntimeProof/
        ├── RunFrontendRuntimeProof.*
        └── README.md

Config/
└── DefaultEngine.ini
```

**Structure Decision**: The implementation belongs in the THOR Tactics Unreal workspace. This MoonMind checkout is only suitable for producing Moon Spec artifacts and cannot satisfy runtime implementation tasks without the target game source.

## Complexity Tracking

No constitution violations are required.
