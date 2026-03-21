# Implementation Plan: Cursor CLI Phase 3 — Auth Profile Support

## Technical Context

Phase 3 seeds a default `cursor_cli` auth profile in the database via Alembic migration. The existing `AuthProfileManager` workflow, `auth_profile.ensure_manager` activity, and `AgentRun._ensure_manager_and_signal()` are all runtime_id agnostic — they work for any runtime including `cursor_cli` without code changes.

## Constitution Check

- ✅ No credentials committed (profile references env var, no actual key)
- ✅ No compatibility transforms
- ✅ Fail-fast for unsupported values

## Scope Analysis

The CursorCli.md Phase 3 items map as follows:

| Doc Item | Status | Rationale |
|----------|--------|-----------|
| Seed default cursor_cli auth profile in migration | **In scope** | New migration file |
| Start AuthProfileManager for cursor_cli on startup | **No code changes needed** | `auth_profile.ensure_manager` is runtime_id agnostic |
| Add cursor_auth_volume to docker-compose | **Already done in Phase 1** | T012-T014 in spec 088 |

## Proposed Changes

### Alembic Migration

#### [NEW] [seed_cursor_cli_auth_profile.py](file:///Users/nsticco/MoonMind/api_service/migrations/versions/seed_cursor_cli_auth_profile.py)

Data-only migration that INSERTs a default `cursor_cli` auth profile row:

```python
op.execute("""
INSERT INTO managed_agent_auth_profiles (
    profile_id, runtime_id, auth_mode, max_parallel_runs,
    cooldown_after_429_seconds, rate_limit_policy, enabled
) VALUES (
    'cursor-cli-default', 'cursor_cli', 'api_key', 1,
    300, 'backoff', true
) ON CONFLICT (profile_id) DO NOTHING
""")
```

Downgrade removes the row:
```python
op.execute("DELETE FROM managed_agent_auth_profiles WHERE profile_id = 'cursor-cli-default'")
```

### Speckit Artifacts

#### [NEW] specs/090-cursor-cli-phase3/ — spec.md, plan.md, research.md, tasks.md, checklists/, contracts/

---

## Verification Plan

### Automated Tests

```bash
./tools/test_unit.sh
```

All existing tests should continue to pass. No new unit tests needed for a data-only migration (the migration itself is validated by structure, and the seeded data is consumed by the existing `auth_profile.list` activity which already has test coverage).

### Manual Verification

None required — this is a straightforward data migration.
