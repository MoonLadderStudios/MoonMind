# Requirements Traceability: Auth-Profile and Rate-Limit Controls (081)

**Created**: 2026-03-15
**Source document**: `docs/Temporal/ManagedAndExternalAgentExecutionModel.md` (Phase 5, Sections 7, 9)

| DOC-REQ | Summary | FR(s) | Implementation Surface | Validation Strategy |
|---------|---------|-------|----------------------|---------------------|
| DOC-REQ-001 | `ManagedAgentAuthProfile` struct with all required fields | FR-001 | `moonmind/schemas/agent_runtime_models.py` (already exists) | Unit test: model validator accepts valid profile; rejects missing required fields |
| DOC-REQ-002 | Auth-profile-based runtime selection via `execution_profile_ref` | FR-002 | `ManagedAgentAdapter.start()` resolves profile before launch | Unit test: adapter resolves profile ID and applies it; non-retryable error on unknown profile (FR-011) |
| DOC-REQ-003 | Per-profile concurrency limits enforced | FR-003 | `AuthProfileManager` workflow slot logic (already exists) + adapter signals it | Unit test: second request blocked/queued when profile at capacity |
| DOC-REQ-004 | Concurrency is per-profile, not per-runtime-family | FR-004 | `AuthProfileManager` `ProfileSlotState` is keyed by `profile_id` (already implemented) | Unit test: two profiles for same runtime family can each host one run concurrently |
| DOC-REQ-005 | 429 → cooldown for `cooldown_after_429` seconds | FR-005 | `AuthProfileManager.report_cooldown` signal (already exists) + adapter signals it | Unit test: 429 triggers cooldown signal; profile excluded from assignment during cooldown |
| DOC-REQ-006 | Only `profile_id` in workflow payloads; no raw credentials | FR-006 | `AgentExecutionRequest` already validates no credential keys; `ManagedAgentAdapter` must pass only `profile_id` in signals | Unit test: inspect Temporal history/signals for absence of token/key values |
| DOC-REQ-007 | OAuth mode: clear API-key env vars | FR-007 | `ManagedAgentAdapter` env shaping logic | Unit test: OAuth profile produces cleared API-key vars in `EnvironmentSpec` |
| DOC-REQ-008 | OAuth credential state in persistent volumes | FR-008 | `ManagedAgentAdapter` sets `volume_mount_path` from `volume_ref` | Unit test: OAuth profile produces correct `volume_mount_path` in `EnvironmentSpec` |
| DOC-REQ-009 | Runtime-specific env shaping for both modes | FR-009 | `ManagedAgentAdapter` env shaping (oauth + api_key branches) | Unit test: api_key profile injects key reference; oauth profile clears key vars |
| DOC-REQ-010 | Worker must support volume mounts and per-profile concurrency | FR-010 | Activity catalog `auth_profile.list` + sandbox fleet volume support | Verify activity implemented and handled; integration test may be deferred to Phase 6 |
