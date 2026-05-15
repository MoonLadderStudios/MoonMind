# Implementation Plan: Unit Death And Ragdoll

**Branch**: `run-jira-orchestrate-for-thor-407-5bc400d9` | **Date**: 2026-05-15 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `specs/358-unit-death-ragdoll/spec.md`

## Summary

Deliver THOR-407 by implementing runtime unit defeat behavior: a living unit reaches one stable dead state, leaves active tactical participation, triggers a clear death or ragdoll presentation, handles duplicate post-death events idempotently, and cleans up without dangling active-unit references. The current managed workspace is the MoonMind repository and contains no THOR gameplay source, `.uproject`, unit combat systems, or ragdoll/death implementation evidence, so every gameplay behavior requirement is classified as missing in this checkout. Planning therefore targets the THOR gameplay workspace and requires tests-first implementation when that source is available.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | missing | No THOR unit health, damage, or defeat code found in this checkout | Add defeat detection for lethal damage and valid non-damage defeat causes | unit + integration |
| FR-002 | missing | No unit dead-state model found | Add a single stable dead state transition with duplicate-transition guard | unit + integration |
| FR-003 | missing | No living-combatant participation filters found | Remove dead units from turns, living actions, and living-combatant targeting | unit + integration |
| FR-004 | missing | No death animation or ragdoll trigger code found | Trigger death/ragdoll presentation on first death transition | unit + integration |
| FR-005 | missing | No idempotent death event handling found | Guard repeated damage, delayed effects, collisions, and duplicate notifications | unit + integration |
| FR-006 | missing | No turn order, targeting, victory, encounter, or cleanup integration found | Update combat flow queries to account for dead units consistently | integration |
| FR-007 | missing | No cleanup/despawn/corpse persistence handling found | Ensure cleanup leaves no active-unit references | unit + integration |
| FR-008 | missing | No ragdoll fallback handling found | Add equivalent visible death presentation fallback when ragdoll is unavailable | unit + integration |
| FR-009 | implemented_unverified | `spec.md` preserves `THOR-407` and the original preset brief | Preserve traceability through tasks, quickstart, verification, commits, and PR text | final verify |
| SCN-001 | missing | No lethal-damage scenario test found | Add lethal damage scenario proof | integration |
| SCN-002 | missing | No death presentation scenario test found | Add death/ragdoll presentation proof | integration |
| SCN-003 | missing | No repeated post-death event test found | Add duplicate event idempotency proof | unit + integration |
| SCN-004 | missing | No combat flow dead-unit query tests found | Add turn, targeting, victory, and cleanup query proof | integration |
| SCN-005 | missing | No cleanup reference safety test found | Add cleanup/despawn/corpse persistence proof | unit + integration |
| SC-001 | missing | No validation evidence for exactly-once death transition | Prove exactly one dead-state transition in lethal damage validation | unit + integration |
| SC-002 | missing | No validation evidence for removal from living-unit action and turn checks | Prove dead unit absence from active participation checks | integration |
| SC-003 | missing | No validation evidence for visible presentation start | Prove death or ragdoll presentation starts on first death | integration |
| SC-004 | missing | No validation evidence for post-death duplicate suppression | Prove repeated events do not create a second gameplay death transition | unit + integration |
| SC-005 | missing | No validation evidence for targeting, victory, encounter, and cleanup flow | Prove combat flow continues correctly after death | integration |
| SC-006 | implemented_unverified | `spec.md`, this plan, and generated design artifacts preserve `THOR-407` and the preset brief | Preserve traceability in later tasks and verification | final verify |

## Technical Context

**Language/Version**: Target THOR Tactics gameplay project, expected Unreal/C++ based on THOR Tactics naming; exact engine and language version must be read from the target `.uproject` and build files when available  
**Primary Dependencies**: Target unit health/damage model, combat turn/targeting systems, animation or physics presentation systems, encounter cleanup systems, and native game test framework  
**Storage**: No new persistent product storage expected; unit state may need runtime state fields and test evidence artifacts  
**Unit Testing**: Target gameplay unit tests for defeat detection, dead-state idempotency, participation filters, cleanup reference safety, and ragdoll fallback decisions  
**Integration Testing**: Target gameplay/automation tests that drive lethal damage, presentation start, turn/targeting/victory checks, and cleanup in an encounter-like runtime scenario  
**Target Platform**: THOR Tactics gameplay runtime  
**Project Type**: Game gameplay/runtime system  
**Performance Goals**: Death handling should complete within the same gameplay resolution step that applies lethal damage and should not introduce repeated processing for already-dead units  
**Constraints**: Preserve `THOR-407` traceability; use runtime implementation behavior, not docs-only output; make death handling idempotent; support a visible fallback when ragdoll is unavailable; do not invent source-document requirements beyond the current spec because `Docs/TacticsUnits_Core/UnitDeathAndRagdoll.md` is unavailable in this workspace  
**Scale/Scope**: One independently testable gameplay story covering unit death state, presentation, combat flow integration, duplicate event safety, and cleanup

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The plan targets native THOR gameplay/runtime systems rather than replacing them with MoonMind abstractions.
- **II. One-Click Agent Deployment**: PASS. No MoonMind deployment dependency is introduced; target-project setup must remain repo-native.
- **III. Avoid Vendor Lock-In**: PASS. The gameplay behavior is target-project domain logic and does not add proprietary MoonMind provider coupling.
- **IV. Own Your Data**: PASS. Verification evidence and artifacts remain in the operator-controlled workspace.
- **V. Skills Are First-Class and Easy to Add**: PASS. No MoonMind skill source or runtime skill mutation is planned.
- **VI. Scientific Method**: PASS. The plan requires red-first unit and integration evidence before implementation.
- **VII. Runtime Configurability**: PASS. Ragdoll availability and fallback behavior are treated as target runtime configuration/capability, not hidden assumptions.
- **VIII. Modular and Extensible Architecture**: PASS. Death state, presentation, combat flow, and cleanup are planned as explicit boundaries.
- **IX. Resilient by Default**: PASS. Idempotent death handling and cleanup safety reduce repeated-event and dangling-reference failures.
- **X. Facilitate Continuous Improvement**: PASS. Quickstart and verification evidence preserve the outcome and remaining blockers.
- **XI. Spec-Driven Development**: PASS. Planning is derived from `spec.md`; tasks and implementation must not drift from it.
- **XII. Canonical Documentation Separation**: PASS. Runtime planning stays in this feature artifact, not canonical docs.
- **XIII. Pre-release Compatibility Policy**: PASS. No compatibility aliases or hidden fallback semantics for internal contracts are planned.

## Project Structure

### Documentation (this feature)

```text
specs/358-unit-death-ragdoll/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── unit-death-ragdoll-contract.md
└── tasks.md
```

### Source Code (target THOR repository)

```text
Source/ThorTactics/
├── Units/
│   ├── UnitHealth.*
│   ├── UnitDeathState.*
│   └── UnitDeathPresentation.*
├── Combat/
│   ├── TurnOrder.*
│   ├── Targeting.*
│   └── EncounterResolution.*
└── Tests/
    └── Units/
        ├── UnitDeathStateTests.*
        └── UnitDeathIntegrationTests.*
```

**Structure Decision**: The implementation belongs in the THOR Tactics gameplay repository or workspace that contains unit, combat, and presentation systems. This MoonMind checkout can produce Moon Spec artifacts but cannot implement or verify runtime gameplay code because the target source is absent.

## Complexity Tracking

No constitution violations are required.
