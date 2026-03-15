# Implementation Plan: Auth-Profile and Rate-Limit Controls (081)

**Branch**: `081-auth-profile-controls` | **Date**: 2026-03-15 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/081-auth-profile-controls/spec.md`

## Summary

Phase 5 of the Managed Agent Execution Model adds auth-profile-based runtime selection, per-profile concurrency enforcement, 429 cooldown/backoff, and safe environment shaping (OAuth + API-key modes) to the `ManagedAgentAdapter`. The `ManagedAgentAuthProfile` model, `AuthProfileManager` Temporal workflow, DB table, and CRUD API all exist (from spec 072). This phase wires them together: implements the missing `auth_profile.list` activity, creates the `ManagedAgentAdapter` that resolves profiles and performs env shaping, integrates `slot_assigned`/`release_slot` signal round-trips with `AuthProfileManager`, and adds comprehensive validation tests.

## Technical Context

**Language/Version**: Python 3.12
**Primary Dependencies**: Temporal SDK (`temporalio`), Pydantic v2, SQLAlchemy async, FastAPI
**Storage**: PostgreSQL (`managed_agent_auth_profiles` table — existing)
**Testing**: pytest + temporalio test framework
**Target Platform**: Linux Docker container (sandbox + artifacts workers)
**Project Type**: Backend service (MoonMind monorepo)
**Constraints**: No credentials in workflow payloads; env shaping must be deterministic and unit-testable without running real CLIs

## Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. One-Click Agent Deployment | ✅ PASS | No new infrastructure; uses existing DB + Temporal workers |
| II. Avoid Vendor Lock-In | ✅ PASS | Adapter interface is generic; env shaping is mode-based (oauth/api_key), not vendor-hard-coded |
| III. Own Your Data | ✅ PASS | Profile data stored in operator-managed PostgreSQL |
| IV. Skills Are First-Class | ✅ PASS | N/A to this feature |
| V. The Bittersweet Lesson | ✅ PASS | Env shaping + concurrency enforcement are thin, replaceable modules behind a clear interface |
| VI. Powerful Runtime Configurability | ✅ PASS | All profile settings are DB-driven; no hardcoded policy values |
| VII. Modular and Extensible | ✅ PASS | `ManagedAgentAdapter` is a new, isolated module; does not alter existing adapter interface |
| VIII. Self-Healing by Default | ✅ PASS | `AuthProfileManager` handles slot/cooldown recovery via continue-as-new; adapter retries are bounded |
| IX. Facilitate Continuous Improvement | ✅ PASS | N/A to this feature |
| X. Spec-Driven Development | ✅ PASS | This spec; all DOC-REQ-* are traced |
| Security Secret Hygiene | ✅ PASS | Credentials never in workflow payloads; shaped env uses references, not values; validator guards exist |

## Project Structure

### Documentation (this feature)

```text
specs/081-auth-profile-controls/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── requirements-traceability.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code Changes

```text
moonmind/
  workflows/
    adapters/
      managed_agent_adapter.py          [NEW] ManagedAgentAdapter implementation
    temporal/
      artifacts.py                      [MODIFIED] auth_profile_list method added to TemporalArtifactActivities
      workflows/
        auth_profile_manager.py         [EXISTING — no changes needed for Phase 5]

tests/
  unit/
    workflows/
      adapters/
        test_managed_agent_adapter.py   [NEW] unit tests: env shaping, profile resolution, concurrency guard, auth_profile_list activity
```

## Proposed Changes

### 1. `auth_profile.list` activity implementation

**File**: `moonmind/workflows/temporal/artifacts.py` [MODIFIED]

- Added `auth_profile_list` as a method of the existing `TemporalArtifactActivities` class
- Accepts `{"runtime_id": str}` — queries `managed_agent_auth_profiles` table via `_repository._session`
- Returns `{"profiles": [list of profile dicts]}` matching the shape `AuthProfileManager` expects
- Registered on the artifacts worker (`mm.activity.artifacts`) via existing `_ACTIVITY_HANDLER_ATTRS` mapping

### 2. `ManagedAgentAdapter`

**File**: `moonmind/workflows/adapters/managed_agent_adapter.py` [NEW]

Implements `AgentAdapter` protocol for managed CLI runtimes.

`start(request: AgentExecutionRequest) → AgentRunHandle`:
1. Resolve `execution_profile_ref` → fetch `ManagedAgentAuthProfile` from DB or registry
2. Validate profile: must be enabled, non-blank `profile_id`; fail fast (non-retryable) if not found/disabled
3. Signal `AuthProfileManager` (`request_slot`)
4. Wait for `slot_assigned` signal with assigned `profile_id`
5. Call env shaping: produce `EnvironmentSpec` based on `auth_mode`
6. Launch runtime subprocess via `ManagedRuntimeLauncher` (stub/foundation; actual launcher in Phase 4)
7. Return `AgentRunHandle` with `profile_id` in metadata (no raw credentials)

On completion / error:
- Signal `release_slot` to `AuthProfileManager`
- On 429: signal `report_cooldown` with profile's `cooldown_after_429` seconds

### 3. Env shaping logic

Inline in `ManagedAgentAdapter` (or extracted to `_shape_environment(profile)`):

**OAuth mode**:
- `cleared_vars`: list of API-key env var names for the runtime (e.g. `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`)
- `volume_mount_path`: from `profile.volume_ref`
- `env_vars`: empty or runtime-specific non-credential vars

**API-key mode**:
- `cleared_vars`: empty
- `volume_mount_path`: None
- `env_vars`: `{"<RUNTIME>_API_KEY_REF": profile.profile_id}` — a reference, not the value

### 4. Tests

- `test_managed_agent_adapter.py`: unit tests for env shaping (both modes), fail-fast on unknown/disabled profile, slot request/release round-trips (mock AuthProfileManager signals), 429 cooldown signal, and `auth_profile_list` activity against in-memory SQLite DB

## Complexity Tracking

No constitution violations. No cross-cutting changes beyond new module additions.
