# Implementation Plan: Claude Governance Telemetry

**Branch**: `191-claude-governance-telemetry` | **Date**: 2026-04-16 | **Spec**: [spec.md](./spec.md)  
**Input**: Single-story feature specification from `specs/191-claude-governance-telemetry/spec.md`

## Summary

Implement the MM-349 Claude governance telemetry story as importable runtime contracts and deterministic validation helpers at the managed-session schema boundary. The work builds on existing Claude session, policy, decision, context, checkpoint, child-work, and surface contracts by adding payload-light event subscription, storage evidence, policy-controlled retention, telemetry normalization, usage rollup, governance export, and provider-mode-aware dashboard summary models. Validation uses focused unit tests plus an integration-style schema boundary fixture without live Claude provider execution.

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2, existing MoonMind schema validation helpers  
**Storage**: No new persistent storage; this story defines compact runtime contracts and deterministic outputs that can later be persisted by the managed-session store or export sinks  
**Unit Testing**: pytest via `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`; focused iteration with `pytest tests/unit/schemas/test_claude_governance_telemetry.py -q`  
**Integration Testing**: pytest integration-style boundary tests with focused iteration via `pytest tests/integration/schemas/test_claude_governance_telemetry_boundary.py -q`; final required hermetic integration runner is `./tools/test_integration.sh` when Docker is available  
**Target Platform**: Linux containers and local development environments supported by MoonMind  
**Project Type**: Python orchestration service schema/runtime boundary  
**Performance Goals**: Governance telemetry contract construction and fixture flow generation are deterministic, bounded, payload-light, and import-safe  
**Constraints**: Preserve MM-342 through MM-348 Claude schema contracts; do not call live Claude providers; do not centralize source code, transcripts, file reads, checkpoint payloads, or local caches by default; unsupported names and governance values fail validation  
**Scale/Scope**: Covers STORY-008 / MM-349 event subscriptions, normalized event envelopes, storage evidence, retention evidence, telemetry evidence, usage rollups, governance evidence, compliance exports, dashboard summaries, and synthetic fixture-flow semantics

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The change models Claude governance evidence at the orchestration boundary without replacing Claude Code or OpenTelemetry behavior.
- **II. One-Click Agent Deployment**: PASS. No deployment prerequisites, credentials, external services, or live provider calls are added.
- **III. Avoid Vendor Lock-In**: PASS. Governance, storage, event, telemetry, and usage evidence are normalized contracts with Claude-specific values isolated to the Claude schema surface.
- **IV. Own Your Data**: PASS. Central-plane evidence is payload-light by default and keeps source code, transcripts, file reads, checkpoint payloads, and local caches out of central records.
- **V. Skills Are First-Class and Easy to Add**: PASS. The feature does not mutate skills or checked-in skill folders.
- **VI. The Bittersweet Lesson**: PASS. The implementation is a compact tested contract layer that can be replaced by live provider telemetry and persistence later.
- **VII. Powerful Runtime Configurability**: PASS. Retention evidence is policy-controlled, and unsupported values fail validation instead of falling back silently.
- **VIII. Modular and Extensible Architecture**: PASS. Contracts live in the existing managed-session schema boundary and do not fork core orchestration.
- **IX. Resilient by Default**: PASS. Strict validation prevents ambiguous audit, retention, storage, event, and telemetry state from being treated as usable evidence.
- **X. Facilitate Continuous Improvement**: PASS. Verification evidence is captured through focused unit and integration-style tests.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. The plan follows the MM-349 spec and preserves Jira traceability.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Implementation artifacts remain under `specs/`; no canonical docs are rewritten as backlog.
- **XIII. Pre-release Clean Breaks**: PASS. Unsupported internal values fail validation instead of compatibility aliases or silent fallback.

## Project Structure

### Documentation (this feature)

```text
specs/191-claude-governance-telemetry/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── claude-governance-telemetry.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/
└── schemas/
    ├── managed_session_models.py
    └── __init__.py

tests/
├── unit/
│   └── schemas/
│       └── test_claude_governance_telemetry.py
└── integration/
    └── schemas/
        └── test_claude_governance_telemetry_boundary.py
```

**Structure Decision**: Extend the existing managed-session schema module used by the Claude core, policy, decision, context, checkpoint, child-work, and surface stories so governance telemetry contracts share validation helpers and remain adjacent to canonical Claude session records.

## Complexity Tracking

No constitution violations require justification.
