# Implementation Plan: Native Options Menu Surface

**Branch**: `[353-native-options-menu-surface]` | **Date**: 2026-05-15 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `/work/agent_jobs/mm:0b69a150-35ed-4afe-bcc4-511f6d02cb47/repo/specs/353-native-options-menu-surface/spec.md`

## Summary

Deliver a Home-reachable Options surface for THOR-402 that presents baseline Video, Audio, and Input category actions from authored data when available and fallback data when authored assets are missing. The planned implementation target is the THOR Tactics game menu runtime, but this managed workspace currently contains the MoonMind repository and no Unreal project files, so all in-scope requirements are classified as missing from the current repo. The test strategy is TDD-first: unit tests for category resolution, fallback behavior, and focus-return state, plus an integration/automation test that drives Home -> Options -> Back using only the baseline surface.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | missing | No `frontend.nav.options`, Home menu, or Unreal menu source found in current workspace | Add Home Options navigation action and opening behavior in target THOR runtime | unit + integration |
| FR-002 | missing | No stable Options category identifiers found in current workspace | Define stable Video, Audio, and Input category identifiers | unit |
| FR-003 | missing | No authored Options category data reader or renderer found | Render category actions from authored data when present | unit + integration |
| FR-004 | missing | No fallback Options category source found | Add fallback category entries when authored data is absent or incomplete | unit + integration |
| FR-005 | missing | No baseline Options surface found | Provide usable baseline surface independent of final authored presentation assets | integration |
| FR-006 | missing | No Options back/cancel behavior found | Return from Options to Home on Back or Cancel | unit + integration |
| FR-007 | missing | No focus restoration behavior found | Restore Home focus to the Options navigation action after return | unit + integration |
| FR-008 | missing | No settings persistence surface found; no implementation evidence for non-persistence boundary | Keep settings persistence out of scope and assert no save requirement is introduced | unit |
| FR-009 | missing | No Home -> Options -> Back automation found | Add automation coverage for the baseline flow | integration |
| SC-001 | missing | No executable menu flow exists in current workspace | Verify Home -> Options -> Back avoids missing or empty menu states | integration |
| SC-002 | missing | No baseline category list exists | Verify at least Video, Audio, and Input actions are visible | unit + integration |
| SC-003 | missing | No missing-asset automation exists | Verify the flow passes without authored Options assets/data | integration |
| SC-004 | missing | No focus automation exists | Verify focus returns to Options in every automated attempt | unit + integration |

## Technical Context

**Language/Version**: Target implementation is expected to use the THOR Tactics game runtime language/toolchain; current MoonMind workspace does not contain those project files.  
**Primary Dependencies**: Existing target game menu/navigation framework, authored menu data assets, and baseline runtime widget/panel primitives in the THOR repository.  
**Storage**: None for this story; settings persistence is explicitly out of scope.  
**Unit Testing**: Target game unit/automation tests for category resolution, fallback list construction, back/cancel state, and focus-restoration state.  
**Integration Testing**: Target game automation test that opens Home, activates Options, validates fallback category actions, triggers Back/Cancel, and verifies restored focus.  
**Target Platform**: THOR Tactics player-facing menu runtime.  
**Project Type**: Game UI/menu feature.  
**Performance Goals**: Opening Options should produce an immediately usable, non-empty menu in the normal Home-menu interaction flow.  
**Constraints**: Must work without final authored Options presentation assets or authored category data; must not add settings persistence; must keep stable category identifiers for downstream presentation/data authoring.  
**Scale/Scope**: One Home navigation action, one Options surface, three required baseline categories, and one end-to-end navigation automation path.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Result | Notes |
| --- | --- | --- |
| I. Orchestrate, Don't Recreate | PASS | The plan targets the existing game menu runtime rather than creating a parallel menu engine. |
| II. One-Click Agent Deployment | PASS | No MoonMind deployment changes are planned. |
| III. Avoid Vendor Lock-In | PASS | Game-runtime implementation is product-specific; no MoonMind vendor coupling is introduced. |
| IV. Own Your Data | PASS | Category data remains local/authored or fallback runtime data. |
| V. Skills Are First-Class and Easy to Add | PASS | No MoonMind skill runtime changes are planned. |
| VI. Replaceable Scaffolding and Tests | PASS | The baseline surface is intentionally replaceable by final authored presentation while tests anchor behavior. |
| VII. Runtime Configurability | PASS | Category data can come from authored data or fallback entries; no hard dependency on persistence/config services. |
| VIII. Modular and Extensible Architecture | PASS | Options category resolution and surface navigation should remain separable from settings persistence. |
| IX. Resilient by Default | PASS | Missing authored assets/data degrade to fallback usable menu behavior. |
| X. Facilitate Continuous Improvement | PASS | Tests provide objective evidence for this single story. |
| XI. Spec-Driven Development | PASS | Plan traces all work to `spec.md` requirements. |
| XII. Documentation Desired State | PASS | Planning remains in feature artifacts, not canonical docs. |

## Project Structure

### Documentation (this feature)

```text
specs/353-native-options-menu-surface/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── options-menu-ui-contract.md
└── tasks.md
```

### Source Code (target repository)

```text
THOR Tactics repository root/
├── Source/
│   └── ThorTactics/
│       ├── Frontend/
│       │   ├── ThorHomeMenuWidget.h
│       │   ├── ThorHomeMenuWidget.cpp
│       │   ├── ThorOptionsMenuWidget.h
│       │   ├── ThorOptionsMenuWidget.cpp
│       │   ├── ThorOptionsMenuTypes.h
│       │   └── ThorOptionsMenuTypes.cpp
│       └── Tests/
│           └── Frontend/
│               ├── OptionsMenuUnitTests.cpp
│               └── OptionsMenuFlowAutomationTest.cpp
└── Content/
    └── Optional authored Options presentation/data assets
```

**Structure Decision**: The current workspace is MoonMind and lacks the target game project. The implementation structure above records concrete proposed THOR target paths for `/speckit.tasks`; the first setup tasks must confirm these paths against the actual THOR repository and adapt them only if the repository has an established equivalent frontend module layout.

## Complexity Tracking

No constitution violations are planned.
