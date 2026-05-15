# Implementation Plan: Frontend Input and Focus Contract

**Branch**: `[355-frontend-input-focus-contract]` | **Date**: 2026-05-15 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from ./spec.md

## Summary

Deliver THOR-404 by establishing a shared frontend menu input and focus contract so generated fallback menu surfaces handle initial focus, confirm activation, cancel/back, and Home focus restoration consistently across mouse, keyboard, and controller input. The target implementation is the THOR Tactics menu runtime; this managed workspace contains the MoonMind repository and no Unreal/THOR source files, so all runtime requirements are classified as missing in this checkout. The planned strategy is TDD-first: unit tests for focus target selection and activation routing, plus automation for native fallback Home, Play, and Options flows.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | missing | No THOR menu screen or panel base class found in current workspace | Add shared default input behavior contract for active menu surfaces in target THOR runtime | unit + integration |
| FR-002 | missing | No generated menu action button runtime found in current workspace | Ensure visible actionable generated buttons are focusable | unit + integration |
| FR-003 | missing | No initial focus assignment code found in current workspace | Assign a valid initial focus target when panels activate | unit + integration |
| FR-004 | missing | No menu coordinator activation path found in current workspace | Route mouse click, keyboard confirm, and controller confirm through the same coordinator behavior | unit + integration |
| FR-005 | missing | No cancel/back state navigation behavior found in current workspace | Dismiss child panels or return to prior frontend state on Back/Cancel | unit + integration |
| FR-006 | missing | No Play-to-Home focus restoration code found in current workspace | Restore focus to Home Play navigation action after returning from Play | integration |
| FR-007 | missing | No Options-to-Home focus restoration code found in current workspace | Restore focus to Home Options navigation action after returning from Options | integration |
| FR-008 | missing | No missing-return-target fallback focus behavior found in current workspace | Choose a valid fallback focus target when the original return target is unavailable | unit + integration |
| FR-009 | missing | No native fallback THOR widgets found in current workspace | Preserve input and focus behavior in native fallback widgets without authored presentation assets | integration |
| FR-010 | missing | No THOR input/focus automation tests found in current workspace | Add automation for initial focus, confirm activation, cancel/back, focus restoration, and native fallback widgets | integration |
| SC-001 | missing | No initial focus automation exists here | Verify initial focus on tested native fallback menu surfaces | integration |
| SC-002 | missing | No activation parity automation exists here | Verify pointer, keyboard, and controller confirm share one coordinator path | unit + integration |
| SC-003 | missing | No cancel/back automation exists here | Verify child panel dismissal or prior-state return | integration |
| SC-004 | missing | No Play return focus automation exists here | Verify Play-to-Home focus restoration | integration |
| SC-005 | missing | No Options return focus automation exists here | Verify Options-to-Home focus restoration | integration |
| SC-006 | missing | No native fallback widget automation exists here | Verify fallback widgets satisfy the complete input/focus contract | integration |

## Technical Context

**Language/Version**: Target implementation is expected to use the THOR Tactics game runtime language/toolchain; current workspace does not contain those project files.  
**Primary Dependencies**: Existing target game frontend menu screens/panels, generated action button renderer, menu coordinator, input handling layer, focus manager, and game automation framework.  
**Storage**: None; focus return targets and active input behavior are runtime UI state only.  
**Unit Testing**: Target THOR unit/automation tests for focus target selection, fallback focus selection, input config defaults, and shared activation routing.  
**Integration Testing**: Target THOR game automation tests for native fallback Home, Play, and Options menu flows using mouse, keyboard, and controller activation paths.  
**Target Platform**: THOR Tactics player-facing menu runtime.  
**Project Type**: Game UI/menu feature.  
**Performance Goals**: Focus assignment and input routing remain immediate during normal menu activation and do not introduce visible navigation delay.  
**Constraints**: Native fallback widgets must remain navigable without authored presentation assets; confirm activation paths must converge; Back/Cancel must not leave players in an invalid focus state; focus restoration must return to the originating Home action when valid.  
**Scale/Scope**: One shared input/focus contract across menu screens, generated action buttons, Home-to-Play, Home-to-Options, and native fallback widget automation.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Result | Notes |
| --- | --- | --- |
| I. Orchestrate, Don't Recreate | PASS | The plan targets the existing game menu runtime instead of creating a separate menu system. |
| II. One-Click Agent Deployment | PASS | No MoonMind deployment changes are planned. |
| III. Avoid Vendor Lock-In | PASS | Game-runtime behavior is product-specific and introduces no MoonMind vendor coupling. |
| IV. Own Your Data | PASS | Menu state and focus behavior remain authored/runtime-owned game data. |
| V. Skills Are First-Class and Easy to Add | PASS | No MoonMind skill runtime changes are planned. |
| VI. Replaceable Scaffolding and Tests | PASS | Tests anchor shared input/focus behavior while presentation can evolve. |
| VII. Runtime Configurability | PASS | Input/focus behavior is runtime UI state, not deployment configuration. |
| VIII. Modular and Extensible Architecture | PASS | Shared menu base behavior supports future panels without per-panel duplication. |
| IX. Resilient by Default | PASS | Fallback focus behavior prevents invalid focus states when targets disappear. |
| X. Facilitate Continuous Improvement | PASS | Unit and automation evidence provide an objective outcome summary. |
| XI. Spec-Driven Development | PASS | This plan traces work to the THOR-404 spec. |
| XII. Documentation Desired State | PASS | Runtime planning stays in feature artifacts, not canonical docs. |
| XIII. Pre-Release Velocity | PASS | No compatibility aliases or deprecated internal paths are planned. |

## Project Structure

### Documentation (this feature)

```text
specs/355-frontend-input-focus-contract/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── frontend-input-focus-contract.md
└── tasks.md
```

### Source Code (target repository)

```text
THOR Tactics repository root/
├── Source/
│   └── ThorTactics/
│       ├── Frontend/
│       │   ├── TacticsMenuInputConfig.h
│       │   ├── TacticsMenuInputConfig.cpp
│       │   ├── TacticsMenuScreenBase.h
│       │   ├── TacticsMenuScreenBase.cpp
│       │   ├── TacticsMenuPanelBase.h
│       │   ├── TacticsMenuPanelBase.cpp
│       │   ├── TacticsGeneratedMenuButton.h
│       │   ├── TacticsGeneratedMenuButton.cpp
│       │   ├── TacticsMenuCoordinator.h
│       │   ├── TacticsMenuCoordinator.cpp
│       │   ├── TacticsHomeMenuPanel.cpp
│       │   ├── TacticsPlayMenuPanel.cpp
│       │   └── TacticsOptionsMenuPanel.cpp
│       └── Tests/
│           └── Frontend/
│               ├── MenuInputFocusContractUnitTests.cpp
│               └── MenuInputFocusContractFlowAutomationTest.cpp
└── Content/
    └── Optional authored menu presentation assets
```

**Structure Decision**: The current workspace is MoonMind and lacks the target game project. The implementation structure above records concrete proposed THOR target paths for `/speckit.tasks`; setup tasks must confirm these paths against the actual THOR repository and adapt them only to existing equivalent menu modules.

## Complexity Tracking

No constitution violations are planned.
