# Implementation Plan: Claude Context Snapshots

**Branch**: `186-claude-context-snapshots` | **Date**: 2026-04-16 | **Spec**: [spec.md](./spec.md)  
**Input**: Single-story feature specification from `specs/186-claude-context-snapshots/spec.md`

## Summary

Implement the MM-345 Claude context-snapshot story as importable runtime contracts and deterministic context-index helpers at the managed-session schema boundary. The work builds on MM-342, MM-343, and MM-344 by adding typed `ContextSnapshot`, context segment, context event, and compaction helper models that attach to canonical Claude session identifiers, validate documented startup and on-demand context kinds, keep guidance distinct from enforcement, create immutable compaction epochs, and keep central metadata payload-light. Validation will use focused unit tests and integration-style schema boundary tests without live Claude execution.

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2, existing MoonMind schema validation helpers  
**Storage**: No new persistent storage; this story defines compact runtime contracts and deterministic outputs that can later be persisted by the managed-session store  
**Unit Testing**: pytest via `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`; focused iteration with `pytest tests/unit/schemas/test_claude_context_snapshots.py`  
**Integration Testing**: pytest integration-style boundary tests; final required hermetic integration runner is `./tools/test_integration.sh` when Docker is available  
**Target Platform**: Linux containers and local development environments supported by MoonMind  
**Project Type**: Python orchestration service schema/runtime boundary  
**Performance Goals**: Context contract construction and compaction planning are deterministic, bounded, and import-safe  
**Constraints**: Preserve MM-342 Claude session core contracts, MM-343 policy contracts, and MM-344 decision contracts; do not call live Claude providers; do not embed full transcripts, file bodies, or skill bodies in central metadata; unsupported enum values must fail validation  
**Scale/Scope**: Covers STORY-004 / MM-345 context-snapshot metadata, reinjection policy, compaction epoch, compaction work item, and normalized event contracts for fixture context sources

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The change models Claude context provenance for orchestration without replacing Claude Code behavior.
- **II. One-Click Agent Deployment**: PASS. No deployment prerequisites, credentials, external services, or live provider calls are added.
- **III. Avoid Vendor Lock-In**: PASS. Context records are normalized session-plane contracts with Claude-specific values isolated to the Claude schema surface.
- **IV. Own Your Data**: PASS. Context evidence is represented as local, inspectable metadata and pointers, with large payloads kept outside central storage by default.
- **V. Skills Are First-Class and Easy to Add**: PASS. Skill descriptions and invoked skill bodies are modeled as context segment kinds without mutating skill runtime behavior or checked-in skill folders.
- **VI. The Bittersweet Lesson**: PASS. The implementation is a compact contract/helper layer with tests, designed to be replaceable as Claude runtime capabilities evolve.
- **VII. Powerful Runtime Configurability**: PASS. Reinjection and guidance classification are explicit data, not hidden fallback behavior.
- **VIII. Modular and Extensible Architecture**: PASS. Contracts live in the existing managed-session schema boundary and do not fork core orchestration.
- **IX. Resilient by Default**: PASS. Immutable epochs and fail-fast validation prevent ambiguous context provenance after compaction.
- **X. Facilitate Continuous Improvement**: PASS. Verification evidence is captured through focused unit and integration tests.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. The plan follows the MM-345 spec and preserves Jira traceability.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Implementation artifacts remain under `specs/`; no canonical docs are rewritten as backlog.
- **XIII. Pre-release Clean Breaks**: PASS. Unsupported context kinds, load timings, policies, classifications, and events fail validation instead of receiving compatibility aliases.

## Project Structure

### Documentation (this feature)

```text
specs/186-claude-context-snapshots/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── claude-context-snapshots.md
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
│       └── test_claude_context_snapshots.py
└── integration/
    └── schemas/
        └── test_claude_context_snapshots_boundary.py
```

**Structure Decision**: Extend the existing managed-session schema module used by the Claude core, policy, and decision stories so context snapshots share validation helpers and remain adjacent to Claude session, turn, work-item, decision, and policy records.

## Complexity Tracking

No constitution violations require justification.
