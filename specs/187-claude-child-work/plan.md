# Implementation Plan: Claude Child Work

**Branch**: `187-claude-child-work` | **Date**: 2026-04-16 | **Spec**: [spec.md](./spec.md)  
**Input**: Single-story feature specification from `specs/187-claude-child-work/spec.md`

## Summary

Implement the MM-347 Claude child-work story as importable runtime contracts and deterministic validation helpers at the managed-session schema boundary. The work builds on the existing Claude session, policy, decision, and context contracts by adding distinct child-context, session-group, team-member, team-message, child-work usage, event, and fixture-flow models. Validation will use focused unit tests and integration-style schema boundary tests without live Claude provider execution.

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2, existing MoonMind schema validation helpers  
**Storage**: No new persistent storage; this story defines compact runtime contracts and deterministic outputs that can later be persisted by the managed-session store  
**Unit Testing**: pytest via `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`; focused iteration with `pytest tests/unit/schemas/test_claude_child_work.py`  
**Integration Testing**: pytest integration-style boundary tests with focused iteration via `pytest tests/integration/schemas/test_claude_child_work_boundary.py -q`; final required hermetic integration runner is `./tools/test_integration.sh` when Docker is available  
**Target Platform**: Linux containers and local development environments supported by MoonMind  
**Project Type**: Python orchestration service schema/runtime boundary  
**Performance Goals**: Child-work contract construction and fixture flow generation are deterministic, bounded, and import-safe  
**Constraints**: Preserve MM-342 Claude session core contracts and previous Claude story contracts; do not call live Claude providers; do not collapse subagents and agent teams into one generic session abstraction; unsupported enum values must fail validation; no automatic background subagent promotion  
**Scale/Scope**: Covers STORY-006 / MM-347 child-context, session-group, team-member, peer-message, usage-rollup, lifecycle-event, and teardown semantics for controlled fixtures

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The change models Claude child-work semantics for orchestration without replacing Claude Code behavior.
- **II. One-Click Agent Deployment**: PASS. No deployment prerequisites, credentials, external services, or live provider calls are added.
- **III. Avoid Vendor Lock-In**: PASS. Child-work records are normalized session-plane contracts with Claude-specific semantics isolated to the Claude schema surface.
- **IV. Own Your Data**: PASS. Child-work evidence is represented as local, inspectable metadata and bounded events.
- **V. Skills Are First-Class and Easy to Add**: PASS. The feature does not mutate skills or checked-in skill folders.
- **VI. The Bittersweet Lesson**: PASS. The implementation is a compact contract/helper layer with tests, designed to be replaced or extended as Claude runtime capabilities evolve.
- **VII. Powerful Runtime Configurability**: PASS. Child-work kinds and promotion behavior are explicit data and fail-fast validation, not hidden fallback behavior.
- **VIII. Modular and Extensible Architecture**: PASS. Contracts live in the existing managed-session schema boundary and do not fork core orchestration.
- **IX. Resilient by Default**: PASS. Strict identity, lineage, and event validation prevent ambiguous child-work state.
- **X. Facilitate Continuous Improvement**: PASS. Verification evidence is captured through focused unit and integration tests.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. The plan follows the MM-347 spec and preserves Jira traceability.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Implementation artifacts remain under `specs/`; no canonical docs are rewritten as backlog.
- **XIII. Pre-release Clean Breaks**: PASS. Unsupported child-work kinds, roles, event names, and cross-group messages fail validation instead of receiving compatibility aliases.

## Project Structure

### Documentation (this feature)

```text
specs/187-claude-child-work/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── claude-child-work.md
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
│       └── test_claude_child_work.py
└── integration/
    └── schemas/
        └── test_claude_child_work_boundary.py
```

**Structure Decision**: Extend the existing managed-session schema module used by the Claude core, policy, decision, and context stories so child-work contracts share validation helpers and remain adjacent to Claude session, turn, work-item, decision, policy, and context records.

## Complexity Tracking

No constitution violations require justification.
