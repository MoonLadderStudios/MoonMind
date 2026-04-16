# Implementation Plan: Claude Surfaces Handoff

**Branch**: `190-claude-surfaces-handoff` | **Date**: 2026-04-16 | **Spec**: [spec.md](./spec.md)  
**Input**: Single-story feature specification from `specs/190-claude-surfaces-handoff/spec.md`

## Summary

Implement the MM-348 Claude multi-surface projection and handoff story as runtime-validated managed-session schema contracts and deterministic fixture helpers. The work extends existing Claude managed-session core contracts with richer surface bindings, surface lifecycle events, handoff seed lineage, resume semantics, and execution security classification. Validation uses focused unit tests plus an integration-style schema boundary test without live Claude provider execution.

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2, existing MoonMind schema validation helpers  
**Storage**: No new persistent storage; this story defines compact runtime contracts and deterministic outputs that can later be persisted by the managed-session store  
**Unit Testing**: pytest via `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`; focused iteration with `pytest tests/unit/schemas/test_claude_surfaces_handoff.py`  
**Integration Testing**: pytest integration-style boundary tests with focused iteration via `pytest tests/integration/schemas/test_claude_surfaces_handoff_boundary.py -q`; final required hermetic integration runner is `./tools/test_integration.sh` when Docker is available  
**Target Platform**: Linux containers and local development environments supported by MoonMind  
**Project Type**: Python orchestration service schema/runtime boundary  
**Performance Goals**: Surface and handoff contract construction is deterministic, bounded, and import-safe  
**Constraints**: Preserve existing MM-342 and MM-347 Claude schema contracts; do not call live Claude providers; do not infer execution location from UI surface; keep handoff payloads as compact refs; unsupported values fail validation  
**Scale/Scope**: Covers STORY-007 / MM-348 surface binding, projection, disconnect/reconnect, resume, cloud handoff lineage, lifecycle event, and security classification semantics for controlled fixtures

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The change models Claude surface semantics at the orchestration boundary without reimplementing Claude transport.
- **II. One-Click Agent Deployment**: PASS. No deployment prerequisites, credentials, external services, or live provider calls are added.
- **III. Avoid Vendor Lock-In**: PASS. The normalized execution classification and surface contracts stay in the managed-session schema boundary, with Claude-specific values isolated.
- **IV. Own Your Data**: PASS. Handoff summaries are represented as operator-owned artifact references rather than embedded external payloads.
- **V. Skills Are First-Class and Easy to Add**: PASS. The feature does not mutate skills or checked-in skill folders.
- **VI. The Bittersweet Lesson**: PASS. The implementation is a compact tested contract layer that can be replaced by provider-native surface telemetry later.
- **VII. Powerful Runtime Configurability**: PASS. Runtime mode is explicit; unsupported projection and event values fail fast.
- **VIII. Modular and Extensible Architecture**: PASS. Contracts extend the existing managed-session schema module and do not fork orchestration logic.
- **IX. Resilient by Default**: PASS. Disconnect/reconnect semantics prevent presentation loss from being misclassified as runtime failure.
- **X. Facilitate Continuous Improvement**: PASS. Verification evidence is captured through focused unit and integration-style tests.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. The plan follows the MM-348 spec and preserves Jira traceability.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Implementation artifacts remain under `specs/`; no canonical docs are rewritten as backlog.
- **XIII. Pre-release Clean Breaks**: PASS. Unsupported internal values fail validation instead of compatibility aliases or silent fallback.

## Project Structure

### Documentation (this feature)

```text
specs/190-claude-surfaces-handoff/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── claude-surfaces-handoff.md
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
│       └── test_claude_surfaces_handoff.py
└── integration/
    └── schemas/
        └── test_claude_surfaces_handoff_boundary.py
```

**Structure Decision**: Extend the existing managed-session schema module used by the Claude core and child-work stories so surface and handoff semantics share validation helpers and remain adjacent to canonical Claude session records.

## Complexity Tracking

No constitution violations require justification.
