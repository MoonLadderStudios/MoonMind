# Implementation Plan: Menu Action Availability and Unavailable Presentation

**Branch**: `[354-menu-action-availability]` | **Date**: 2026-05-15 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `/work/agent_jobs/mm:77a58456-73de-47fa-bf11-8f027af0e7c3/repo/specs/354-menu-action-availability/spec.md`

## Summary

Deliver THOR-403 by extending the THOR Tactics generated menu action model so every generated-button panel can distinguish enabled actions, disabled-visible unavailable actions, and actions hidden by the current menu window. The target implementation is the THOR Tactics menu runtime; this managed workspace contains the MoonMind repository and no Unreal/THOR source files, so all runtime requirements are classified as missing in this checkout. The planned strategy is TDD-first: unit tests for eligibility and unavailable-copy resolution, plus automation proving generated button rendering and blocked Online Co-op selection behavior without travel or session side effects.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | missing | No THOR menu action eligibility model found in current workspace | Add enabled, disabled-visible, and hidden-by-window eligibility outcomes in target THOR runtime | unit + integration |
| FR-002 | missing | No unavailable reason field/result found in current workspace | Expose player-facing unavailable reason through action entry or eligibility result | unit |
| FR-003 | missing | No generated menu button renderer found in current workspace | Render disabled-visible actions as disabled controls with unavailable copy | unit + integration |
| FR-004 | missing | No generated menu action visibility filtering found in current workspace | Omit hidden-by-window actions from generated button output | unit + integration |
| FR-005 | missing | No enabled generated action path found in current workspace | Preserve enabled action selection behavior without disabled copy | unit + integration |
| FR-006 | missing | No Online Co-op menu action found in current workspace | Keep blocked Online Co-op visible and show unavailable feedback | integration |
| FR-007 | missing | No travel/session side-effect guard found in current workspace | Block Online Co-op selection before travel or session operations | unit + integration |
| FR-008 | missing | No shared generated-button panel path found in current workspace | Apply the shared behavior to Play, Home navigation, Options, and future panels | integration |
| FR-009 | missing | No fallback unavailable reason behavior found in current workspace | Provide deterministic fallback copy for disabled-visible actions without authored reason | unit |
| FR-010 | missing | No THOR automation tests found in current workspace | Add automation for enabled, disabled-visible, hidden-by-window, and blocked-selection behavior | integration |
| SC-001 | missing | No eligible generated action test exists here | Verify enabled generated action rendering and selection | unit + integration |
| SC-002 | missing | No disabled-visible generated action test exists here | Verify disabled action rendering with unavailable copy | unit + integration |
| SC-003 | missing | No hidden-by-window generated action test exists here | Verify hidden actions are not rendered | unit + integration |
| SC-004 | missing | No Online Co-op blocked-selection test exists here | Verify feedback and zero travel/session side effects | integration |
| SC-005 | missing | No cross-panel generated-button test exists here | Verify shared behavior across Play, Home navigation, Options, and a future-compatible panel fixture | integration |

## Technical Context

**Language/Version**: Target implementation is expected to use the THOR Tactics game runtime language/toolchain; current workspace does not contain those project files.  
**Primary Dependencies**: Existing target game menu action registry, generated menu button renderer, navigation panels, Online Co-op travel/session entry points, and game automation framework.  
**Storage**: None; unavailable reasons and eligibility are runtime menu state only.  
**Unit Testing**: Target THOR unit/automation tests for action eligibility, unavailable reason resolution, fallback copy, and blocked-selection side-effect guards.  
**Integration Testing**: Target THOR game automation tests for generated menu panels and Online Co-op blocked selection.  
**Target Platform**: THOR Tactics player-facing menu runtime.  
**Project Type**: Game UI/menu feature.  
**Performance Goals**: Generated menu rendering remains immediate for normal menu interaction and does not perform travel/session work for blocked actions.  
**Constraints**: Disabled-visible actions must remain discoverable; hidden-by-window actions must not render; Online Co-op must not start travel/session side effects while blocked; behavior must be shared by generated-button panels instead of copied per panel.  
**Scale/Scope**: One shared action eligibility contract, generated button rendering, Online Co-op blocked feedback, and automation across Play, Home navigation, Options, and one future-compatible panel path.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Result | Notes |
| --- | --- | --- |
| I. Orchestrate, Don't Recreate | PASS | The plan targets the existing game menu runtime instead of creating a separate menu engine. |
| II. One-Click Agent Deployment | PASS | No MoonMind deployment changes are planned. |
| III. Avoid Vendor Lock-In | PASS | Game-runtime behavior is product-specific and introduces no MoonMind vendor coupling. |
| IV. Own Your Data | PASS | Menu metadata and unavailable copy remain authored/runtime-owned game data. |
| V. Skills Are First-Class and Easy to Add | PASS | No MoonMind skill runtime changes are planned. |
| VI. Replaceable Scaffolding and Tests | PASS | Tests anchor shared menu behavior while presentation can evolve. |
| VII. Runtime Configurability | PASS | Availability is runtime-evaluated menu state, not hardcoded deployment configuration. |
| VIII. Modular and Extensible Architecture | PASS | Shared generated-button behavior supports future panels without per-panel duplication. |
| IX. Resilient by Default | PASS | Blocked actions fail closed with user feedback and no side effects. |
| X. Facilitate Continuous Improvement | PASS | Unit and automation evidence provide an objective outcome summary. |
| XI. Spec-Driven Development | PASS | This plan traces work to the THOR-403 spec. |
| XII. Documentation Desired State | PASS | Runtime planning stays in feature artifacts, not canonical docs. |
| XIII. Pre-Release Velocity | PASS | No compatibility aliases or deprecated internal paths are planned. |

## Project Structure

### Documentation (this feature)

```text
specs/354-menu-action-availability/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── menu-action-availability-contract.md
└── tasks.md
```

### Source Code (target repository)

```text
THOR Tactics repository root/
├── Source/
│   └── ThorTactics/
│       ├── Frontend/
│       │   ├── TacticsMenuActionTypes.h
│       │   ├── TacticsMenuActionTypes.cpp
│       │   ├── TacticsGeneratedMenuButton.h
│       │   ├── TacticsGeneratedMenuButton.cpp
│       │   ├── TacticsGeneratedMenuPanel.h
│       │   ├── TacticsGeneratedMenuPanel.cpp
│       │   ├── TacticsPlayMenuPanel.cpp
│       │   ├── TacticsHomeMenuPanel.cpp
│       │   └── TacticsOptionsMenuPanel.cpp
│       ├── Online/
│       │   └── TacticsOnlineCoopActions.cpp
│       └── Tests/
│           └── Frontend/
│               ├── MenuActionAvailabilityUnitTests.cpp
│               └── MenuActionAvailabilityFlowAutomationTest.cpp
└── Content/
    └── Optional authored menu action data
```

**Structure Decision**: The current workspace is MoonMind and lacks the target game project. The implementation structure above records concrete proposed THOR target paths for `/speckit.tasks`; setup tasks must confirm these paths against the actual THOR repository and adapt them only to existing equivalent menu modules.

## Complexity Tracking

No constitution violations are planned.
