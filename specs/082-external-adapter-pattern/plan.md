# Implementation Plan: Generic External Agent Adapter Pattern

**Branch**: `082-external-adapter-pattern` | **Date**: 2026-03-17 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/082-external-adapter-pattern/spec.md`

## Summary

Generalize the external-agent adapter pattern described in `docs/ExternalAgents/ExternalAgentIntegrationSystem.md` into production-ready code. Significant infrastructure already exists (`BaseExternalAgentAdapter`, `ProviderCapabilityDescriptor`, both provider adapters). Remaining work focuses on: (1) making the base class capability-aware (auto-populating `poll_hint_seconds`, best-effort cancel fallback), (2) completing Codex Cloud Temporal activity integration, (3) exporting the base class as public API, (4) writing developer documentation, and (5) updating the design doc to reflect completed status.

## Technical Context

**Language/Version**: Python 3.11
**Primary Dependencies**: Pydantic, Temporal SDK (temporalio), pytest, pytest-asyncio
**Storage**: N/A (no database changes)
**Testing**: Unit tests via `./tools/test_unit.sh`
**Target Platform**: Linux server (Docker Compose)
**Project Type**: Python package (moonmind)
**Performance Goals**: N/A (adapter pattern; no performance-critical paths)
**Constraints**: Temporal determinism (no non-deterministic code in workflows)
**Scale/Scope**: ~6 files modified, ~2 new files created

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Orchestrate, Don't Recreate | **PASS** | Adapter pattern is the canonical orchestration boundary. No agent internals are being replaced. |
| II. One-Click Deployment | **PASS** | No deployment changes. |
| III. Avoid Vendor Lock-In | **PASS** | Explicitly decouples provider-specific logic behind universal adapter interface. |
| IV. Own Your Data | **PASS** | No data storage changes. |
| V. Skills First-Class | **PASS** | N/A — not a skill change. |
| VI. Bittersweet Lesson | **PASS** | Base class designed for deletion — providers override thin hooks behind thick contracts. |
| VII. Runtime Configurability | **PASS** | Provider capabilities declared statically; gating remains env-based. |
| VIII. Modular Architecture | **PASS** | New provider requires only adapter subclass + registration — no core changes. |
| IX. Resilient by Default | **PASS** | Idempotency cache, best-effort cancel, correlation metadata all support resilience. |
| X. Continuous Improvement | **PASS** | N/A — no operational flow changes. |
| XI. Spec-Driven | **PASS** | This plan follows spec 082. |

## Project Structure

### Documentation (this feature)

```text
specs/082-external-adapter-pattern/
├── spec.md              # Feature specification
├── plan.md              # This file
├── research.md          # Phase 0 output
├── tasks.md             # Phase 2 output
├── checklists/
│   └── requirements.md  # Spec quality checklist
└── contracts/
    └── requirements-traceability.md
```

### Source Code (repository root)

```text
moonmind/workflows/adapters/
├── __init__.py                       # [MODIFY] Export BaseExternalAgentAdapter
├── base_external_agent_adapter.py    # [MODIFY] Add capability-aware poll_hint + cancel fallback
├── codex_cloud_agent_adapter.py      # [EXISTING] Already extends base
├── jules_agent_adapter.py            # [EXISTING] Already extends base
└── external_adapter_registry.py      # [EXISTING] No changes needed

moonmind/workflows/temporal/activities/
├── jules_activities.py               # [EXISTING] Reference pattern
└── codex_cloud_activities.py         # [NEW] Codex Cloud Temporal activities

moonmind/workflows/temporal/
└── activity_catalog.py               # [MODIFY] Register Codex Cloud activities

docs/ExternalAgents/
├── ExternalAgentIntegrationSystem.md  # [MODIFY] Update to reflect completed status
└── AddingExternalProvider.md          # [NEW] Developer guide

tests/unit/workflows/adapters/
├── test_base_external_agent_adapter.py  # [MODIFY] Add tests for capability-aware features
└── test_codex_cloud_activities.py       # [NEW] Unit tests for Codex Cloud activities
```

**Structure Decision**: All changes follow existing package layout. No new packages or structural changes needed.

## Implementation Details

### Change 1: Capability-Aware Base Class (FR-006, FR-008)

**File**: `moonmind/workflows/adapters/base_external_agent_adapter.py`

1. **`build_handle` auto-populates `poll_hint_seconds`**: When building an `AgentRunHandle`, if no explicit `poll_hint_seconds` is provided, set it from `self.provider_capability.default_poll_hint_seconds`.

2. **`cancel` best-effort fallback**: Override `cancel()` to check `self.provider_capability.supports_cancel`. If `False`, return an `intervention_requested` status with `cancelAccepted=False, unsupported=True` without calling `do_cancel`.

### Change 2: Export Base Class (FR-010)

**File**: `moonmind/workflows/adapters/__init__.py`

Add `BaseExternalAgentAdapter` and `ProviderCapabilityDescriptor` to the package exports.

### Change 3: Codex Cloud Temporal Activities (FR-011)

**File**: `moonmind/workflows/temporal/activities/codex_cloud_activities.py` (NEW)

Follow the exact same pattern as `jules_activities.py`:
- `_build_adapter()`: Build a gated `CodexCloudAgentAdapter` using env-based configuration.
- 4 activity definitions: `integration.codex_cloud.start`, `.status`, `.fetch_result`, `.cancel`.

**File**: `moonmind/workflows/temporal/activity_catalog.py` (MODIFY)

Register the 4 Codex Cloud activities in the catalog alongside the existing Jules activities, on the `mm.activity.integrations` task queue.

### Change 4: Developer Guide (FR-012)

**File**: `docs/ExternalAgents/AddingExternalProvider.md` (NEW)

Step-by-step guide covering:
1. Configuration (settings module, runtime gate)
2. Client (HTTP transport + schemas)
3. Adapter subclass (extending `BaseExternalAgentAdapter`)
4. Registry registration (in `build_default_registry`)
5. Temporal activities (4 activity definitions)
6. Activity catalog registration
7. Testing patterns

### Change 5: Design Doc Update (DOC-REQ-013)

**File**: `docs/ExternalAgents/ExternalAgentIntegrationSystem.md` (MODIFY)

Update Phase B and Phase C sections to reflect completed implementation. Mark Phase E as proven with Codex Cloud.

## Complexity Tracking

No constitution violations. No complexity justifications needed.

## Verification Plan

### Automated Tests

**Existing tests** (must continue to pass):

```bash
./tools/test_unit.sh tests/unit/workflows/adapters/test_base_external_agent_adapter.py
./tools/test_unit.sh tests/unit/workflows/adapters/test_external_adapter_registry.py
```

**New tests**:

1. **`test_build_handle_populates_poll_hint_from_capability`**: Verify that `build_handle` returns a handle with `poll_hint_seconds` matching the capability descriptor's `defaultPollHintSeconds`.

2. **`test_cancel_returns_fallback_when_cancel_unsupported`**: Verify that calling `cancel()` on a stub adapter with `supportsCancel=False` returns `intervention_requested` without calling `do_cancel`.

3. **`test_codex_cloud_activities.py`**: Unit tests for the 4 Codex Cloud Temporal activities, mocking the client and verifying adapter invocation.

**Run all tests:**

```bash
./tools/test_unit.sh tests/unit/workflows/adapters/
```
