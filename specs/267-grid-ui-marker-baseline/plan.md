# Implementation Plan: Grid UI Marker Baseline

**Branch**: `267-grid-ui-marker-baseline` | **Date**: 2026-04-26 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `specs/267-grid-ui-marker-baseline/spec.md`

## Summary

Deliver `MM-525` by adding baseline evidence for the current Grid UI marker/decal lifecycle: a checked-in direct mutation inventory, producer-role classifications, regression coverage for Movement overlay interference, preservation of existing lifecycle/idempotence expectations, and diagnostic evidence that distinguishes producer churn from renderer churn. Repository gap analysis found that the referenced Tactics frontend source document and implementation are not present in this checkout, so implementation cannot safely proceed in this repository without the target source tree.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | missing | No `SpawnTileMarkers`, `QueueSpawnTileMarkers`, `ClearTileMarkers`, `SpawnDecalsAtLocations`, or `ClearSpecifiedDecals` references exist in this checkout. | Requires target Tactics frontend source tree before inventory can be authored. | unit + integration in target project |
| FR-002 | missing | No target call sites exist in this checkout to classify. | Requires target Tactics frontend source tree and gameplay context. | inventory validation |
| FR-003 | missing | No Movement overlay implementation or tests exist in this checkout. | Requires target tests for two Movement producers and clear behavior. | unit or controller test first |
| FR-004 | missing | No existing Grid UI marker lifecycle/idempotence tests exist in this checkout. | Requires target test suite or equivalent assertions. | existing lifecycle/idempotence tests |
| FR-005 | missing | No marker/decal diagnostic event surface exists in this checkout. | Requires target diagnostic implementation or test hooks. | unit + integration diagnostics validation |
| FR-006 | implemented_unverified | Spec explicitly constrains scope to baseline and no ownership semantics code has been changed. | Preserve as implementation guard when target source is available. | final verification |
| FR-007 | implemented_verified | `spec.md` preserves `MM-525` and the canonical Jira preset brief. | Preserve through any downstream artifacts, commits, and PR metadata. | final verification |
| SCN-001 | missing | No target APIs are present to inventory. | Requires target source tree. | inventory validation |
| SCN-002 | missing | No target call sites are present to classify. | Requires target source tree. | inventory validation |
| SCN-003 | missing | No Movement overlay behavior is present. | Requires target tests. | unit/controller test |
| SCN-004 | missing | No lifecycle/idempotence tests are present. | Requires target test suite. | unit + integration |
| SCN-005 | missing | No diagnostic surface is present. | Requires target diagnostics. | unit + integration |
| DESIGN-REQ-001 | missing | Source design document is not present; Jira brief is preserved. | Requires target source/design artifact. | final verification |
| DESIGN-REQ-002 | missing | No named APIs are present. | Requires target source tree. | inventory validation |
| DESIGN-REQ-003 | missing | No call sites are present. | Requires target source tree. | inventory validation |
| DESIGN-REQ-004 | missing | No Movement overlay implementation exists. | Requires target tests. | unit/controller test |
| DESIGN-REQ-005 | missing | No existing lifecycle/idempotence tests exist. | Requires target test suite. | unit + integration |
| DESIGN-REQ-006 | missing | No diagnostic event surface exists. | Requires target diagnostics. | unit + integration |
| DESIGN-REQ-007 | implemented_unverified | No ownership semantics have been changed in this repo. | Preserve as a target implementation guard. | final verification |

## Technical Context

**Language/Version**: Target appears to be a Tactics frontend runtime, likely Unreal/C++ from API naming, but the target source tree is unavailable in this checkout.  
**Primary Dependencies**: Target Grid UI marker/decal runtime and diagnostics system unavailable in this checkout.  
**Storage**: Checked-in source inventory file in the target project; no persistent storage expected.  
**Unit Testing**: Target project unit/controller tests required; unavailable in this checkout.  
**Integration Testing**: Target project lifecycle/diagnostic integration tests required; unavailable in this checkout.  
**Target Platform**: Tactics frontend runtime; exact platform cannot be verified from this checkout.  
**Project Type**: Runtime frontend/game UI feature baseline; target project unavailable.  
**Performance Goals**: Inventory and diagnostics must preserve current runtime behavior; no ownership semantics migration in this story.  
**Constraints**: Do not change marker ownership semantics; preserve `MM-525` traceability; do not invent target files or APIs when the target source tree is absent.  
**Scale/Scope**: One baseline story covering existing direct marker/decal mutation call sites and related diagnostics.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The plan does not replace provider behavior or MoonMind orchestration.
- **II. One-Click Agent Deployment**: PASS. No deployment path changes.
- **III. Avoid Vendor Lock-In**: PASS. No vendor-specific lock-in introduced.
- **IV. Own Your Data**: PASS. Jira-derived source context is preserved in local MoonSpec artifacts.
- **V. Skills Are First-Class and Easy to Add**: PASS. No skill runtime changes.
- **VI. Scientific Method / Tests Are Anchor**: PASS with blocker. The plan requires tests before runtime changes, but target test harness is absent.
- **VII. Powerful Runtime Configurability**: PASS. No runtime configuration changes.
- **VIII. Modular and Extensible Architecture**: PASS. No architecture changes are proposed without target source.
- **IX. Resilient by Default**: PASS. No workflow/activity contract changes.
- **X. Facilitate Continuous Improvement**: PASS. The blocker is captured as structured evidence.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. Spec artifacts exist before implementation.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Feature-local artifacts hold execution notes.
- **XIII. Pre-release Compatibility Policy**: PASS. No compatibility aliases or transforms introduced.

## Project Structure

### Documentation (this feature)

```text
specs/267-grid-ui-marker-baseline/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── diagnostic-evidence.md
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
Tactics frontend Grid UI runtime source
Tactics frontend marker lifecycle tests
```

**Structure Decision**: Do not map implementation tasks onto the MoonMind codebase. The requested runtime story belongs to the referenced Tactics frontend target source tree, which is not present here. This repository can preserve MoonSpec artifacts and blocker evidence only until the target source tree is available.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |
