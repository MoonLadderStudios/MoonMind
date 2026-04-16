# Implementation Plan: Claude Checkpoints Rewind

**Branch**: `187-claude-checkpoints-rewind` | **Date**: 2026-04-16 | **Spec**: [spec.md](./spec.md)  
**Input**: Single-story feature specification from `specs/187-claude-checkpoints-rewind/spec.md`

## Summary

Implement the MM-346 Claude checkpoint and rewind story as importable runtime contracts and deterministic checkpoint/rewind helpers at the managed-session schema boundary. The work builds on the existing Claude managed-session, decision, and context contracts by adding typed checkpoint records, checkpoint index output, rewind request/result models, normalized checkpoint/rewind work events, capture-rule helpers, active-cursor lineage, and payload-light validation. Validation will use focused unit tests and an integration-style schema boundary test without live Claude execution or provider-specific restore mechanics.

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2, existing MoonMind schema validation helpers  
**Storage**: No new persistent storage; this story defines compact runtime contracts and deterministic outputs that can later be persisted by the managed-session store  
**Unit Testing**: pytest via `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`; focused iteration with `pytest tests/unit/schemas/test_claude_checkpoints.py -q`  
**Integration Testing**: pytest integration-style boundary tests with focused iteration via `pytest tests/integration/schemas/test_claude_checkpoints_boundary.py -q`; final required hermetic integration runner is `./tools/test_integration.sh` when Docker is available  
**Target Platform**: Linux containers and local development environments supported by MoonMind  
**Project Type**: Python orchestration service schema/runtime boundary  
**Performance Goals**: Checkpoint contract construction, capture evaluation, and rewind result creation are deterministic, bounded, and import-safe  
**Constraints**: Preserve MM-342 through MM-345 Claude contracts; do not call live Claude providers; do not embed checkpoint payloads, transcripts, or diffs in central metadata; unsupported enum values must fail validation; summarize-from-here must not claim code restore  
**Scale/Scope**: Covers STORY-005 / MM-346 checkpoint metadata, capture rules, checkpoint index, rewind modes, active cursor lineage, event log preservation references, summary artifact references, and normalized work evidence

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The change models Claude checkpoint provenance for orchestration without replacing Claude Code restore behavior.
- **II. One-Click Agent Deployment**: PASS. No deployment prerequisites, credentials, external services, or live provider calls are added.
- **III. Avoid Vendor Lock-In**: PASS. Checkpoint records are normalized session-plane contracts with Claude-specific values isolated to the Claude schema surface.
- **IV. Own Your Data**: PASS. Checkpoint payloads stay runtime-local by default while central records carry inspectable metadata and references.
- **V. Skills Are First-Class and Easy to Add**: PASS. No skill runtime or checked-in skill folder mutation is introduced.
- **VI. The Bittersweet Lesson**: PASS. The implementation is a compact contract/helper layer with tests, designed to be replaceable as Claude runtime capabilities evolve.
- **VII. Powerful Runtime Configurability**: PASS. Capture mode, retention state, and payload locality are explicit data, not hidden fallback behavior.
- **VIII. Modular and Extensible Architecture**: PASS. Contracts live in the existing managed-session schema boundary and do not fork core orchestration.
- **IX. Resilient by Default**: PASS. Rewind lineage and preserved event-log references prevent ambiguous recovery history.
- **X. Facilitate Continuous Improvement**: PASS. Verification evidence is captured through focused unit and integration tests.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. The plan follows the MM-346 spec and preserves Jira traceability.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Implementation artifacts remain under `specs/`; no canonical docs are rewritten as backlog.
- **XIII. Pre-release Clean Breaks**: PASS. Unsupported checkpoint triggers, modes, states, and events fail validation instead of receiving compatibility aliases.

## Project Structure

### Documentation (this feature)

```text
specs/187-claude-checkpoints-rewind/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── claude-checkpoints-rewind.md
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
│       └── test_claude_checkpoints.py
└── integration/
    └── schemas/
        └── test_claude_checkpoints_boundary.py
```

**Structure Decision**: Extend the existing managed-session schema module used by the Claude core, policy, decision, and context stories so checkpoints share validation helpers and remain adjacent to session, turn, work-item, context, decision, and policy records.

## Complexity Tracking

No constitution violations require justification.
