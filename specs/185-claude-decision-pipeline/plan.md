# Implementation Plan: Claude Decision Pipeline

**Branch**: `185-claude-decision-pipeline` | **Date**: 2026-04-16 | **Spec**: [spec.md](./spec.md)  
**Input**: Single-story feature specification from `specs/185-claude-decision-pipeline/spec.md`

## Summary

Define MM-344 Claude decision and hook provenance as runtime-validated schema contracts in MoonMind's managed-session schema surface. The implementation adds normalized DecisionPoint and HookAudit records that attach to canonical Claude session identifiers, validate documented decision stages and event names, distinguish policy, hook, sandbox, classifier, user, runtime, protected-path, and headless outcomes, and provide focused unit plus integration-style boundary tests.

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2, existing MoonMind schema validation helpers  
**Storage**: No new persistent storage; this story defines importable runtime contracts and validation behavior only  
**Unit Testing**: pytest via `./tools/test_unit.sh`; focused iteration with `pytest tests/unit/schemas/test_claude_managed_session_models.py`  
**Integration Testing**: pytest integration-style boundary tests; final required hermetic integration runner is `./tools/test_integration.sh` when Docker is available  
**Target Platform**: Linux containers and local development environments supported by MoonMind  
**Project Type**: Python orchestration service schema/runtime boundary  
**Performance Goals**: Contract construction is deterministic, bounded, and import-safe  
**Constraints**: Preserve Codex managed-session behavior; preserve existing Claude core session behavior; do not introduce compatibility translation layers; keep large tool payloads and transport envelopes out of decision metadata and hook audit data  
**Scale/Scope**: Covers STORY-003 decision and hook provenance contracts for MM-344

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The change models Claude decision provenance for orchestration without replacing Claude Code behavior.
- **II. One-Click Agent Deployment**: PASS. No deployment prerequisites or external services are added.
- **III. Avoid Vendor Lock-In**: PASS. Decision records are normalized session-plane contracts with Claude-specific source values isolated to the Claude schema surface.
- **IV. Own Your Data**: PASS. No external storage or SaaS dependency is introduced.
- **V. Skills Are First-Class and Easy to Add**: PASS. This change does not mutate agent skill runtime behavior.
- **VI. The Bittersweet Lesson**: PASS. The schema is a compact, replaceable contract surface anchored by tests.
- **VII. Powerful Runtime Configurability**: PASS. No hidden runtime configuration, model fallback, or policy fallback behavior is introduced.
- **VIII. Modular and Extensible Architecture**: PASS. Contracts live in the existing schema boundary and do not fork core orchestration.
- **IX. Resilient by Default**: PASS. Explicit decision provenance prevents ambiguous safety outcomes.
- **X. Facilitate Continuous Improvement**: PASS. Verification evidence is captured through focused tests and Moon Spec artifacts.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. The plan follows the MM-344 spec and preserves the Jira brief.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. No canonical docs are converted into implementation checklists.
- **XIII. Pre-release Clean Breaks**: PASS. Unsupported decision stages, events, scopes, and outcomes fail validation instead of receiving compatibility transforms.

## Project Structure

### Documentation (this feature)

```text
specs/185-claude-decision-pipeline/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── claude-decision-pipeline.md
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
│       └── test_claude_managed_session_models.py
└── integration/
    └── schemas/
        └── test_claude_decision_pipeline_boundary.py
```

**Structure Decision**: Add Claude decision and hook contracts to the existing managed-session schema module so they share validation helpers and stay adjacent to Claude session, turn, and work-item records.

## Complexity Tracking

No constitution violations require justification.
