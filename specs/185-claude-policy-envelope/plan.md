# Implementation Plan: Claude Policy Envelope

**Branch**: `185-claude-policy-envelope` | **Date**: 2026-04-16 | **Spec**: [spec.md](./spec.md)  
**Input**: Single-story feature specification from `specs/185-claude-policy-envelope/spec.md`

## Summary

Implement the MM-343 Claude policy-envelope story as importable runtime contracts and deterministic policy-resolution helpers at the managed-session schema boundary. The work builds on the MM-342 Claude session core by adding versioned `PolicyEnvelope`, `PolicyHandshake`, `PolicyEvent`, and fixture-friendly source models that encode server-managed versus endpoint-managed precedence, fail-closed behavior, security-dialog requirements, provider mode, policy trust level, and BootstrapPreferences-as-template semantics. Validation will use focused unit tests and integration-style boundary tests without requiring live Claude execution or external providers.

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2, existing MoonMind schema validation helpers  
**Storage**: No new persistent storage; this story defines compact runtime contracts and deterministic outputs that can later be persisted by the managed-session store  
**Unit Testing**: pytest via `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`; focused iteration with `pytest tests/unit/schemas/test_claude_policy_envelope.py`  
**Integration Testing**: pytest integration-style boundary tests; final required hermetic integration runner is `./tools/test_integration.sh` when Docker is available  
**Target Platform**: Linux containers and local development environments supported by MoonMind  
**Project Type**: Python orchestration service schema/runtime boundary  
**Performance Goals**: Policy compilation is deterministic, bounded by compact input size, and does not perform network or filesystem I/O  
**Constraints**: Preserve MM-342 Claude session core contracts; do not call live Claude providers; do not embed large policy source payloads in workflow history; fail closed when configured; keep BootstrapPreferences distinct from managed defaults  
**Scale/Scope**: Covers STORY-002 / MM-343 policy-envelope compilation and handshake state for fixture policy sources across server-managed, endpoint-managed, cache-hit, fetch-failed, fail-closed, security-dialog, and bootstrap-template scenarios

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The change models Claude policy state for orchestration without replacing Claude Code or implementing Claude provider internals.
- **II. One-Click Agent Deployment**: PASS. No deployment prerequisites, live provider credentials, or external services are added.
- **III. Avoid Vendor Lock-In**: PASS. Provider mode and trust level are explicit metadata; policy compilation remains behind shared session-plane contracts.
- **IV. Own Your Data**: PASS. Policy evidence is represented as local, compact, inspectable records.
- **V. Skills Are First-Class and Easy to Add**: PASS. This story does not mutate skill runtime behavior or checked-in skill folders.
- **VI. The Bittersweet Lesson**: PASS. The implementation is a thin contract/helper layer with tests, designed to be replaceable as Claude runtime capabilities evolve.
- **VII. Powerful Runtime Configurability**: PASS. Fail-closed and visibility behavior are explicit inputs, not hidden defaults.
- **VIII. Modular and Extensible Architecture**: PASS. Policy types live at the existing schema boundary and can be consumed by future adapters.
- **IX. Resilient by Default**: PASS. Unsupported or failed policy states are explicit and fail closed when configured.
- **X. Facilitate Continuous Improvement**: PASS. Verification evidence is captured through focused unit and integration tests.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. The plan follows the MM-343 spec and preserves Jira traceability.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Implementation artifacts remain under `specs/`; no canonical docs are rewritten as backlog.
- **XIII. Pre-release Clean Breaks**: PASS. Unsupported values fail validation instead of receiving compatibility aliases or semantic translation layers.

## Project Structure

### Documentation (this feature)

```text
specs/185-claude-policy-envelope/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── claude-policy-envelope.md
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
│       └── test_claude_policy_envelope.py
└── integration/
    └── schemas/
        └── test_claude_policy_envelope_boundary.py
```

**Structure Decision**: Extend the existing managed-session schema module used by MM-342 so Claude policy envelopes remain adjacent to Claude session records and future workflow/activity boundaries can carry compact typed payloads.

## Complexity Tracking

No constitution violations require justification.
