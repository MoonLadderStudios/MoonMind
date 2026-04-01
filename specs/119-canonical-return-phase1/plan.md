# Implementation Plan: canonical-return-phase1

**Branch**: `119-canonical-return-phase1` | **Date**: 2026-03-31 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/119-canonical-return-phase1/spec.md`

## Summary

This work completes Phase 1 of Canonical Return execution by establishing shared contract validation helpers for `AgentRunHandle`, `AgentRunStatus`, and `AgentRunResult` at the generic activity boundary. It ensures payloads are strictly modeled in Canonical Pydantic shapes preventing arbitrary provider dictionaries from polluting the main workflow payloads.

## Technical Context

**Language/Version**: Python 3.10+ (MoonMind's standard backend language).
**Primary Dependencies**: Pydantic v2 (for canonical modeling), Temporal Python SDK (for activity/workflow contexts).
**Testing**: test_unit.sh / pytest.
**Project Type**: Python backend application (`moonmind` module).
**Constraints**: Zero regression on running workflow compatibility. Invalid shapes must explicitly raise exception at boundary.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The orchestrator continues treating activities opaquely but now guarantees standard signature shapes for them.
- **IX. Resilient by Default**: PASS. Strongly typing the activity return boundaries limits "poison pill" runtime dict schema drift from providers.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. The old compatibility shapes (if applicable) inside the workflow aren't removed in Phase 1 (to be done in Phase 4), but the boundary schemas are strictly applied going forward. 

## Project Structure

### Documentation (this feature)

```text
specs/119-canonical-return-phase1/
├── spec.md              
├── plan.md              # This file
├── contracts/
│   └── requirements-traceability.md
└── tasks.md             # (to be generated)
```

### Source Code

```text
moonmind/
└── schemas/
    └── agent_runtime_models.py   # Helpers implemented here
tests/
└── unit/
    └── schemas/
        └── test_agent_runtime_models.py  # Tests added here
```

**Structure Decision**: Add canonical validation methods directly to `moonmind/schemas/agent_runtime_models.py` since that is where `AgentRunHandle`, `AgentRunStatus`, and `AgentRunResult` exist. Add unit tests for those methods to `tests/unit/schemas/test_agent_runtime_models.py`.
