# Research Notes: Cursor CLI Phase 3

## R1: AuthProfileManager is Runtime-ID Agnostic

After analyzing the `AuthProfileManager` startup path:

1. `AgentRun._ensure_manager_and_signal()` in `agent_run.py` calls `auth_profile.ensure_manager` activity with `runtime_id` param
2. `auth_profile_ensure_manager()` in `artifacts.py` uses `f"auth-profile-manager:{runtime_id}"` — works for any runtime_id
3. `_load_profiles_from_db()` in `auth_profile_manager.py` calls `auth_profile.list` with `runtime_id`
4. `auth_profile_list()` queries `managed_agent_auth_profiles WHERE runtime_id = :runtime_id`

**Conclusion**: No code changes needed. The system auto-starts `AuthProfileManager` for `cursor_cli` when the first cursor_cli agent run occurs, as long as there's a matching profile row in the database.

## R2: cursor_auth_volume Already Done

Phase 1 (spec 088) already:
- Added `cursor_auth_volume` Docker volume
- Created `cursor-auth-init` init container
- Added volume mounts to both worker services
- Added `MOONMIND_CURSOR_CLI_AUTH_MODE` and `CURSOR_API_KEY` env vars

## R3: Alembic Migration Chain

Current migration chain:
```
594fc88de6eb (initial) → 59830c78b458 → b3e7a91c2d4f → c1d2e3f4a5b6
```

The new migration should depend on `c1d2e3f4a5b6` (latest). Use `ON CONFLICT DO NOTHING` for idempotency.

## R4: Default Profile Attributes

Based on the `managed_agent_auth_profiles` table schema:
- `profile_id`: `cursor-cli-default` (follows `<runtime>-default` convention)
- `runtime_id`: `cursor_cli`
- `auth_mode`: `api_key` (matches Phase 1's default `MOONMIND_CURSOR_CLI_AUTH_MODE`)
- `volume_ref`: NULL initially (volume is configured but not linked until OAuth mode)
- `volume_mount_path`: NULL initially
- `max_parallel_runs`: 1 (conservative default)
- `cooldown_after_429_seconds`: 300 (5 min standard)
- `rate_limit_policy`: `backoff` (standard)
- `enabled`: true
