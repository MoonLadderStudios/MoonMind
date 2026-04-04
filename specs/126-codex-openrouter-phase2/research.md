# Research: Codex CLI OpenRouter Phase 2

## Findings

### 1. Mission Control Provider Profile UI

**Current State**: The `ProviderProfilesManager.tsx` component provides a complete generic CRUD interface for provider profiles. It supports all basic fields (`profileId`, `runtimeId`, `providerId`, `defaultModel`, `credentialSource`, `cooldownAfter429Seconds`, etc.) but does NOT expose advanced fields:
- `command_behavior`
- `file_templates`
- `env_template`
- `clear_env_keys`
- `home_path_overrides`
- `tags`
- `priority`
- `account_label`

**Decision**: Expose a subset of advanced fields that are most relevant for operator configuration:
- `command_behavior` (JSON textarea) — needed for `suppress_default_model_flag`
- `tags` (comma-separated input) — useful for profile organization
- `priority` (number input) — needed for dynamic routing control
- `clear_env_keys` (multiline textarea) — needed for env isolation
- `account_label` (text input) — useful for multi-account setups

Fields like `file_templates`, `env_template`, and `home_path_overrides` are complex structured data better managed through YAML seeds or API calls. They are NOT included in the Phase 2 UI.

### 2. Dynamic Routing Infrastructure

**Current State**: The `_resolve_profile()` method in `ManagedAgentAdapter` already supports `profile_selector.providerId` filtering. The logic is a simple exact-match string comparison. Multiple profiles are sorted by `(priority, available_slots)` descending.

**Decision**: No backend changes needed. Integration tests will verify the existing plumbing works for the openrouter value.

### 3. `suppress_default_model_flag`

**Current State**: Fully implemented in `CodexCliStrategy.build_command()` with unit tests. The auto-seeded openrouter profile sets `suppress_default_model_flag: true`.

**Decision**: No code changes needed. Phase 2 requirement is already satisfied by Phase 1 work.

### 4. Integration Test Patterns

**Current State**: `test_agent_run.py` uses a `MockProviderProfileManager` workflow that handles slot requests/releases, cooldown reporting, and state queries. Tests use inline profile definitions with legacy-shaped fields.

**Decision**: New tests will:
- Use the same `MockProviderProfileManager` pattern
- Define openrouter-shaped profiles with `provider_id: "openrouter"` and modern field names
- Assert profile-specific cooldown and slot behavior
- Use unique `profile_id` prefixes to avoid conflicts with auto-seeded profiles

### 5. Provider Profile Backend Validation

**Current State**: The `provider_profiles.py` router validates basic enum patterns and secret refs. No validation exists for `command_behavior` structure or `file_templates` path validity.

**Decision**: Phase 2 adds minimal validation for `command_behavior` (ensure it's a dict/object when provided). Deeper validation is out of scope.

## References

- `frontend/src/components/settings/ProviderProfilesManager.tsx` — existing UI component
- `moonmind/workflows/adapters/managed_agent_adapter.py` — profile resolution logic
- `moonmind/workflows/temporal/runtime/strategies/codex_cli.py` — suppress_default_model_flag implementation
- `api_service/api/routers/provider_profiles.py` — REST CRUD router
- `tests/integration/services/temporal/workflows/test_agent_run.py` — integration test patterns
- `docs/ManagedAgents/CodexCliOpenRouter.md` — source design document §15 Phase 2
