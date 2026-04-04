# Quickstart: Phase 3 — Codex OpenRouter Generalization

**Feature**: 127-codex-openrouter-phase3

---

## Overview

Phase 3 has one primary change: rewrite the `ManagedAgentProviderProfile` Pydantic model to match the provider-profile contract, plus adapter cleanup. The DB model, API routes, materializer, adapter, and launcher are already provider-agnostic.

## Prerequisites

- Python 3.12+ with the MoonMind virtual environment activated
- Access to the MoonMind repository at `/mnt/d/code/MoonMind`

## Running Existing Tests (Before Making Changes)

```bash
# Run unit tests for the affected schema module
./tools/test_unit.sh --py-args tests/unit/schemas/test_agent_runtime_models.py -q

# Run provider profile tests
./tools/test_unit.sh --py-args tests/unit/api_service/api/routers/test_provider_profiles.py -q

# Run provider profile manager workflow tests
./tools/test_unit.sh --py-args tests/unit/workflows/temporal/test_provider_profile_manager.py -q
```

## Implementation Steps

### Step 1: Rewrite `ManagedAgentProviderProfile`

**File**: `/mnt/d/code/MoonMind/moonmind/schemas/agent_runtime_models.py`

1. Remove the `auth_mode` field (line ~298)
2. Remove `auth_mode` validation in `_validate_policy` (line ~316)
3. Add the 13 missing provider-profile fields:
   - `credential_source`, `runtime_materialization_mode`, `tags`, `priority`
   - `clear_env_keys`, `env_template`, `file_templates`, `home_path_overrides`
   - `command_behavior`, `secret_refs`, `volume_mount_path`
   - `max_lease_duration_seconds`, `owner_user_id`

See `plan.md` for the exact field definitions and types.

### Step 2: Update Adapter

**File**: `/mnt/d/code/MoonMind/moonmind/workflows/adapters/managed_agent_adapter.py`

1. Line 212: Replace `profile.get("auth_mode", default_auth)` with `profile.get("credential_source", ...)`
2. Line 338: Remove `auth_mode=auth_mode` from `ManagedRuntimeProfile` constructor
3. Line 403: Replace `"auth_mode": auth_mode` with `"credential_source": credential_source` in metadata

### Step 3: Update Tests

**File**: `/mnt/d/code/MoonMind/tests/unit/schemas/test_agent_runtime_models.py`

- Replace `authMode="oauth"` with `credentialSource="oauth_volume"`
- Replace `authMode="api_key"` with `credentialSource="secret_ref"`
- Add tests for full provider-profile field acceptance
- Add tests for legacy `auth_mode` rejection

### Step 4: Verify

```bash
# Full unit test suite
./tools/test_unit.sh

# Provider-agnostic guarantee audit
grep -r "openrouter" moonmind/workflows/adapters/ moonmind/workflows/temporal/runtime/launcher.py
# Expected: 0 matches
```

## Verification Checklist

- [ ] `./tools/test_unit.sh` passes without regression
- [ ] `grep -r "openrouter" moonmind/workflows/` returns 0 matches
- [ ] `ManagedAgentProviderProfile` accepts all provider-profile fields
- [ ] `ManagedAgentProviderProfile` rejects `authMode` (extra="forbid")
- [ ] Adapter no longer references `auth_mode` in profile construction or metadata

## Key Files

| File | Purpose |
|------|---------|
| `/mnt/d/code/MoonMind/moonmind/schemas/agent_runtime_models.py` | Pydantic models (primary change target) |
| `/mnt/d/code/MoonMind/moonmind/workflows/adapters/managed_agent_adapter.py` | Adapter (auth_mode cleanup) |
| `/mnt/d/code/MoonMind/tests/unit/schemas/test_agent_runtime_models.py` | Unit tests |
| `/mnt/d/code/MoonMind/api_service/db/models.py` | DB model (reference, no changes needed) |
| `/mnt/d/code/MoonMind/api_service/api/routers/provider_profiles.py` | API routes (reference, no changes needed) |
