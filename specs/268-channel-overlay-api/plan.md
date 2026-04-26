# Implementation Plan: Channel-Owned Overlay Intent API

**Branch**: `268-channel-overlay-api` | **Date**: 2026-04-26 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `specs/268-channel-overlay-api/spec.md`

## Summary

Deliver `MM-526` by adding channel-owned desired overlay state to AGridUI: explicit overlay channel and layer state models, BlueprintCallable SetOverlayLayer and ClearOverlayLayer APIs using tile indexes, per-channel storage, reducer behavior into the existing marker/decal renderer, legacy marker compatibility routing, warning diagnostics for non-approved legacy calls, and preservation of existing decal pooling/idempotence behavior. Repository gap analysis found that the referenced Tactics frontend source document and runtime implementation are not present in this checkout, so implementation cannot safely proceed in this repository without the target source tree.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | missing | No `AGridUI`, `EGridOverlayChannel`, or target Grid UI runtime files exist in this checkout. | Requires target Tactics frontend source tree before channel model can be added. | unit + integration in target project |
| FR-002 | missing | No target overlay layer state type exists in this checkout. | Requires target AGridUI data model files. | unit serialization/state tests |
| FR-003 | missing | No BlueprintCallable or equivalent AGridUI API surface exists in this checkout. | Requires target AGridUI public interface files. | API/Blueprint-facing tests |
| FR-004 | missing | No target marker/decal renderer path exists in this checkout. | Requires target reducer and existing renderer path. | unit + integration rendering tests |
| FR-005 | missing | No Movement overlay channel implementation exists in this checkout. | Requires target channel-state clear logic. | unit/controller channel-isolation test |
| FR-006 | missing | No legacy marker API surface or diagnostics exists in this checkout. | Requires target legacy API routing and diagnostic hooks. | unit + integration diagnostics tests |
| FR-007 | missing | No target decal pooling/idempotence tests exist in this checkout. | Requires target test suite or equivalent assertions. | existing target tests |
| FR-008 | implemented_unverified | Spec and plan preserve the boundary not to split controller/renderer responsibilities or migrate producers. | Preserve as implementation guard when target source is available. | final verification |
| FR-009 | implemented_verified | `spec.md` preserves `MM-526` and the canonical Jira preset brief. | Preserve through downstream artifacts, commits, and PR metadata. | final verification |
| SCN-001 | missing | No target overlay layer state exists. | Requires target source tree. | unit state tests |
| SCN-002 | missing | No target channel clear logic exists. | Requires target source tree. | unit channel-isolation test |
| SCN-003 | missing | No target reducer/renderer path exists. | Requires target renderer integration. | integration renderer test |
| SCN-004 | missing | No legacy marker API routing or diagnostic surface exists. | Requires target source tree. | unit + integration diagnostics tests |
| SCN-005 | missing | No decal pooling/idempotence tests exist in this checkout. | Requires target test suite. | existing/equivalent lifecycle tests |
| DESIGN-REQ-001 | missing | Source design document and target AGridUI implementation are absent. | Requires target source/design artifact. | final verification |
| DESIGN-REQ-002 | missing | No target channel enum/model exists. | Requires target source tree. | unit model tests |
| DESIGN-REQ-003 | missing | No target layer state model exists. | Requires target source tree. | unit state tests |
| DESIGN-REQ-004 | missing | No target API surface exists. | Requires target AGridUI public API. | API-facing tests |
| DESIGN-REQ-005 | missing | No target reducer/renderer exists. | Requires target source tree. | unit + integration tests |
| DESIGN-REQ-006 | missing | No Movement channel isolation logic exists. | Requires target source tree. | unit/controller test |
| DESIGN-REQ-007 | missing | No legacy compatibility route exists. | Requires target source tree. | unit + diagnostics tests |
| DESIGN-REQ-008 | missing | No target decal pooling/idempotence tests exist. | Requires target test suite. | existing/equivalent tests |
| DESIGN-REQ-009 | implemented_unverified | Spec and plan explicitly constrain scope. | Preserve as implementation guard. | final verification |

## Technical Context

**Language/Version**: Target appears to be a Tactics frontend runtime, likely Unreal/C++ from AGridUI and BlueprintCallable naming, but the target source tree is unavailable in this checkout.  
**Primary Dependencies**: Target AGridUI runtime, marker/decal renderer, diagnostics, and Blueprint-facing API system unavailable in this checkout.  
**Storage**: In-memory per-channel overlay state inside AGridUI; no persistent storage expected.  
**Unit Testing**: Target project unit/controller tests required; unavailable in this checkout.
**Integration Testing**: Target project rendering/lifecycle/diagnostic integration tests required; unavailable in this checkout.
**Target Platform**: Tactics frontend runtime; exact platform cannot be verified from this checkout.  
**Project Type**: Runtime frontend/game UI feature; target project unavailable.  
**Performance Goals**: Overlay reduction must preserve existing marker/decal rendering behavior and decal pooling/idempotence expectations.  
**Constraints**: Preserve `MM-526` traceability; use runtime mode; do not split controller/renderer responsibilities; do not migrate individual gameplay producers beyond compatibility routing; do not invent target files or APIs when the target source tree is absent.  
**Scale/Scope**: One AGridUI overlay API story covering channel model, layer state, channel isolation, reducer behavior, legacy compatibility, diagnostics, and existing decal test preservation.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The plan does not replace provider behavior or MoonMind orchestration.
- **II. One-Click Agent Deployment**: PASS. No deployment path changes.
- **III. Avoid Vendor Lock-In**: PASS. No vendor-specific MoonMind integration introduced.
- **IV. Own Your Data**: PASS. Jira-derived source context is preserved in local MoonSpec artifacts.
- **V. Skills Are First-Class and Easy to Add**: PASS. No skill runtime changes.
- **VI. Scientific Method / Tests Are Anchor**: PASS with blocker. The plan requires tests before runtime changes, but the target test harness is absent.
- **VII. Powerful Runtime Configurability**: PASS. No MoonMind runtime configuration changes.
- **VIII. Modular and Extensible Architecture**: PASS. The target implementation is scoped to AGridUI and existing renderer boundaries.
- **IX. Resilient by Default**: PASS. No workflow/activity contract changes.
- **X. Facilitate Continuous Improvement**: PASS. The blocker is captured as structured evidence.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. Spec artifacts exist before implementation.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Feature-local artifacts hold execution notes.
- **XIII. Pre-release Compatibility Policy**: PASS. No compatibility aliases or hidden transforms are introduced in MoonMind internal contracts.

## Project Structure

### Documentation (this feature)

```text
specs/268-channel-overlay-api/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── overlay-api-contract.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
# Current checkout contains MoonMind orchestration code only.
api_service/
frontend/
moonmind/
docs/
specs/

# Required target paths are absent from this checkout.
Docs/TacticsFrontend/GridUiOverlaySystem.md
Tactics frontend AGridUI runtime source
Tactics frontend marker/decal renderer source
Tactics frontend Grid UI tests
```

**Structure Decision**: Do not map implementation tasks onto the MoonMind codebase. The requested runtime story belongs to the referenced Tactics frontend target source tree, which is not present here. This repository can preserve MoonSpec artifacts and blocker evidence only until the target source tree is available.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |
