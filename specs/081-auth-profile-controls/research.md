# Research: Auth-Profile and Rate-Limit Controls (081)

**Created**: 2026-03-15
**Branch**: `081-auth-profile-controls`

## Codebase Inventory

### Already Implemented (Phase 5 Pre-existing Work from Spec 072)

| Component | Module | Status |
|-----------|--------|--------|
| `ManagedAgentAuthProfile` Pydantic model | `moonmind/schemas/agent_runtime_models.py` | ✅ Complete |
| `ManagedRuntimeProfile` Pydantic model | `moonmind/schemas/agent_runtime_models.py` | ✅ Complete |
| `AgentExecutionRequest.execution_profile_ref` field | `moonmind/schemas/agent_runtime_models.py` | ✅ Complete |
| `ManagedAgentAuthProfile` SQLAlchemy ORM model | `api_service/db/models.py` (ManagedAgentAuthMode + ManagedAgentRateLimitPolicy enums) | ✅ Complete |
| DB migration for `managed_agent_auth_profiles` | `api_service/migrations/versions/202603140002_managed_agent_auth_profiles.py` | ✅ Complete |
| Auth profiles CRUD REST API | `api_service/api/routers/auth_profiles.py` | ✅ Complete |
| `AuthProfileManager` Temporal workflow | `moonmind/workflows/temporal/workflows/auth_profile_manager.py` | ✅ Complete — includes signals (request_slot, release_slot, report_cooldown, sync_profiles), FIFO queue, continue-as-new, periodic cooldown clearing |
| `auth_profile.list` activity definition in catalog | `moonmind/workflows/temporal/activity_catalog.py` (line 440) | ✅ Registered in catalog |
| `AgentAdapter` protocol | `moonmind/workflows/adapters/agent_adapter.py` | ✅ Complete |
| Unit tests for AuthProfileManager | `tests/unit/workflows/temporal/test_auth_profile_manager.py` | ✅ Present |

### Missing — Phase 5 Gaps

| Component | Location | Gap Description |
|-----------|----------|-----------------|
| `auth_profile.list` activity **implementation** | None — only catalog entry exists | Activity handler matching `auth_profile.list` must be registered and wired to the DB |
| `ManagedAgentAdapter` class | Not present | Adapter that resolves `execution_profile_ref` → `ManagedAgentAuthProfile`, performs env shaping, and signals `AuthProfileManager` for slot lease/release |
| `EnvironmentShaper` (or inline logic in adapter) | Not present | Model/function that produces shaped env dict: clear API keys for OAuth, inject key for api_key mode |
| `slot_assigned` signal handling in `MoonMind.AgentRun` | Not present (AgentRun workflow may not exist yet) | AgentRun must wait on a `slot_assigned` signal, then release via `release_slot` on completion/error |
| Integration wiring: `ManagedAgentAdapter → AuthProfileManager` | Not present | Adapter must signal `auth-profile-manager:<runtime_id>` with request_slot before startying runtime |
| End-to-end concurrency enforcement tests | Not present | Tests validating per-profile slot limits, 429 cooldown, environment shaping (oauth/api_key modes) |

## Key Design Decisions

### Decision 1: Where does env shaping live?
- **Chosen**: Inside `ManagedAgentAdapter.start()` before passing env to the runtime launcher.
- **Rationale**: The adapter owns the runtime-launch orchestration; shaping belongs in the adapter's pre-launch preparation step.
- **Alternatives**: Separate `EnvironmentShaper` activity (rejected — over-engineering for a sync data transform; no I/O needed).

### Decision 2: Where is per-profile concurrency state tracked?
- **Chosen**: Entirely in the `AuthProfileManager` Temporal workflow's in-memory `ProfileSlotState`, backed by continue-as-new for durability.
- **Rationale**: This is already implemented and matches the DOC-REQ-011 requirement that concurrency state lives in the Temporal workflow layer, not in the DB.
- **Alternatives**: DB-backed counters (rejected — non-atomic under concurrent access without explicit locking; Temporal signals provide serialized access naturally).

### Decision 3: How does `MoonMind.AgentRun` integrate with `AuthProfileManager`?
- **Chosen**: `AgentRun` signals `request_slot` to `auth-profile-manager:<runtime_id>` and waits for a `slot_assigned` signal back with the resolved `profile_id`. On completion or failure, it signals `release_slot`.
- **Rationale**: Consistent with the AuthProfileManager signal API that already exists.

### Decision 4: What hosts `auth_profile.list` activity?
- **Chosen**: The artifacts fleet worker (`mm.activity.artifacts`), since it already has DB access.
- **Rationale**: The activity is simple DB read with no runtime secrets needed. Already registered to this queue in the catalog.
