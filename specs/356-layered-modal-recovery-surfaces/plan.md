# Implementation Plan: Layered Modal Recovery Surfaces

**Branch**: `[356-layered-modal-recovery-surfaces]` | **Date**: 2026-05-15 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `/work/agent_jobs/mm:f936f8ea-012f-4100-a03c-3cb6f1e7cdf6/repo/specs/356-layered-modal-recovery-surfaces/spec.md`

## Summary

Deliver THOR-405 by routing progress, blocking error, retry, dismiss, and confirmation modal flows through a shared frontend modal layer behavior so recovery outcomes are predictable. The target implementation is the THOR Tactics game menu/runtime layer; this managed workspace contains the MoonMind repository and no Unreal/THOR source files, so all runtime requirements are classified as missing in this checkout. The planned strategy is TDD-first: unit tests for modal state policy, retry/dismiss/confirmation outcomes, and layer stack behavior, plus integration automation for native fallback modal flows without authored presentation subclasses.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | missing | No `UI.Layer.Modal`, THOR UI manager, or THOR modal layer stack found in current workspace | Route production progress, blocking error, retry, dismiss, and confirmation modal states through the target modal layer stack | unit + integration |
| FR-002 | missing | No THOR native modal base or reusable modal behavior contract found | Add a shared native modal base or equivalent behavior contract for all required modal states | unit + integration |
| FR-003 | missing | No progress modal runtime found | Ensure progress modals block conflicting interaction while active | unit + integration |
| FR-004 | missing | No blocking error modal runtime found | Ensure blocking error modals expose acknowledgement or recovery behavior | unit + integration |
| FR-005 | missing | No captured recovery action model or retry modal behavior found | Execute captured recovery action once when Retry is selected | unit + integration |
| FR-006 | missing | No missing-recovery-action guard found | Prevent undefined recovery execution when Retry has no captured action | unit |
| FR-007 | missing | No dismiss-to-Home behavior found | Return to Home when modal Dismiss has no explicit prior state | unit + integration |
| FR-008 | missing | No explicit prior-state dismiss behavior found | Return to configured prior state when Dismiss has one | unit + integration |
| FR-009 | missing | No confirmation modal outcome routing found | Route confirmation outcomes consistently and remove modal from the layer stack | unit + integration |
| FR-010 | missing | No native fallback THOR modal widgets found | Provide native fallback modals without authored presentation subclasses | integration |
| FR-011 | missing | No modal push/dismiss stack behavior found | Preserve layer stack consistency during modal add, replace, and remove operations | unit + integration |
| FR-012 | missing | No THOR modal automation tests found in current workspace | Add automation for progress, blocking error, retry, dismiss, confirmation, fallback, and layer stack behavior | integration |
| SC-001 | missing | No progress modal automation exists here | Verify progress modal layer presentation and interaction blocking | integration |
| SC-002 | missing | No blocking error modal automation exists here | Verify blocking error modal layer presentation and shared behavior | integration |
| SC-003 | missing | No retry automation exists here | Verify captured recovery action executes exactly once per retry attempt | unit + integration |
| SC-004 | missing | No dismiss-to-Home automation exists here | Verify Dismiss returns to Home without explicit prior state | integration |
| SC-005 | missing | No dismiss-to-prior-state automation exists here | Verify Dismiss returns to configured prior state when present | integration |
| SC-006 | missing | No fallback modal automation exists here | Verify native fallback modals work without authored subclasses | integration |
| SC-007 | missing | No layer stack automation exists here | Verify modal push/dismiss operations leave expected stack state | unit + integration |

## Technical Context

**Language/Version**: Target implementation is expected to use the THOR Tactics game runtime language/toolchain; current workspace does not contain those project files.  
**Primary Dependencies**: Existing target game UI manager, modal layer stack, frontend state coordinator, native widget/panel primitives, fallback widget factory, and game automation framework.  
**Storage**: None; modal state, recovery actions, and prior-state destinations are runtime UI/navigation state only.  
**Unit Testing**: Target THOR unit/automation tests for modal state construction, retry action capture/execution, dismiss destination selection, confirmation outcome routing, and layer stack push/dismiss invariants.  
**Integration Testing**: Target THOR game automation tests for progress modal, blocking error modal, retry, dismiss to Home, dismiss to explicit prior state, confirmation outcome handling, and native fallback modal behavior.  
**Target Platform**: THOR Tactics player-facing frontend runtime.  
**Project Type**: Game UI/menu modal feature.  
**Performance Goals**: Modal presentation and dismissal complete without visible frontend navigation delay during normal menu/runtime interactions.  
**Constraints**: Production modal presentation must use the modal layer stack; native fallback modals must work without authored presentation subclasses; Retry must only execute captured recovery actions; Dismiss defaults to Home unless an explicit prior state exists.  
**Scale/Scope**: One shared modal behavior surface covering progress, blocking error, retry, dismiss, confirmation, native fallback, and modal layer push/dismiss behavior.

## Test Strategy

**Unit**: Add target THOR unit/automation coverage for shared modal state policy, interaction blocking flags, recovery action capture and single execution, absent recovery guardrails, dismiss destination resolution, confirmation outcome routing, and modal layer stack push/dismiss invariants.

**Integration**: Add target THOR game automation coverage for production modal presentation through the modal layer, progress interaction blocking, blocking error acknowledgement/recovery, retry execution, dismiss to Home, dismiss to explicit prior state, confirmation outcomes, native fallback modals with authored assets absent, and layer stack cleanup after dismiss.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Result | Notes |
| --- | --- | --- |
| I. Orchestrate, Don't Recreate | PASS | The plan targets the existing game UI manager and modal layer stack rather than creating a parallel modal system. |
| II. One-Click Agent Deployment | PASS | No MoonMind deployment changes are planned. |
| III. Avoid Vendor Lock-In | PASS | Game-runtime implementation is product-specific and introduces no MoonMind vendor coupling. |
| IV. Own Your Data | PASS | Modal state and recovery actions remain local runtime state. |
| V. Skills Are First-Class and Easy to Add | PASS | No MoonMind skill runtime changes are planned. |
| VI. Replaceable Scaffolding and Tests | PASS | Native fallback surfaces are replaceable by authored presentation while tests anchor behavior. |
| VII. Runtime Configurability | PASS | Modal behavior is runtime UI state, not deployment configuration. |
| VIII. Modular and Extensible Architecture | PASS | A shared modal base or equivalent behavior contract avoids per-modal duplication. |
| IX. Resilient by Default | PASS | Retry and dismiss guardrails provide deterministic recovery outcomes. |
| X. Facilitate Continuous Improvement | PASS | Unit and automation evidence provide objective completion criteria. |
| XI. Spec-Driven Development | PASS | This plan traces work to the THOR-405 spec. |
| XII. Documentation Desired State | PASS | Runtime planning stays in feature artifacts, not canonical docs. |
| XIII. Pre-Release Velocity | PASS | No compatibility aliases or deprecated internal paths are planned. |

## Project Structure

### Documentation (this feature)

```text
specs/356-layered-modal-recovery-surfaces/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── modal-recovery-ui-contract.md
└── tasks.md
```

### Source Code (target repository)

```text
THOR Tactics repository root/
├── Source/
│   └── ThorTactics/
│       ├── Frontend/
│       │   ├── TacticsModalTypes.h
│       │   ├── TacticsModalTypes.cpp
│       │   ├── TacticsModalLayerController.h
│       │   ├── TacticsModalLayerController.cpp
│       │   ├── TacticsNativeModalBase.h
│       │   ├── TacticsNativeModalBase.cpp
│       │   ├── TacticsProgressModalWidget.cpp
│       │   ├── TacticsErrorModalWidget.cpp
│       │   ├── TacticsConfirmationModalWidget.cpp
│       │   └── TacticsFrontendCoordinator.cpp
│       └── Tests/
│           └── Frontend/
│               ├── ModalRecoveryUnitTests.cpp
│               └── ModalRecoveryFlowAutomationTest.cpp
└── Content/
    └── Optional authored modal presentation assets
```

**Structure Decision**: The current workspace is MoonMind and lacks the target game project. The implementation structure above records concrete proposed THOR target paths for `/speckit.tasks`; setup tasks must confirm these paths against the actual THOR repository and adapt them only to existing equivalent modal/frontend modules.

## Complexity Tracking

No constitution violations are planned.
