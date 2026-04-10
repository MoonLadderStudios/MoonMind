# Implementation Plan: Codex CLI OpenRouter Phase 2

**Branch**: `126-codex-openrouter-phase2` | **Date**: 2026-04-03 | **Spec**: [`spec.md`](./spec.md)

## Context

Phase 1 (spec 125) implemented the core plumbing: provider-profile field passthrough, path-aware file materialization, `CODEX_HOME` support, auto-seeding of the `codex_openrouter_qwen36_plus` profile, and `suppress_default_model_flag` in the Codex strategy. Phase 1 is marked **Implemented**.

Phase 2 focuses on: Mission Control UI exposure of advanced profile fields, dynamic routing verification, and integration test coverage for openrouter-specific cooldown and slot behavior. The `suppress_default_model_flag` strategy support is already complete from Phase 1 but is included here as a Phase 2 milestone in the source doc.

## Current State Assessment

| Component | Phase 1 Status | Phase 2 Gap |
|-----------|---------------|-------------|
| Adapter field plumbing | ✅ Done | None |
| Materializer file support | ✅ Done | None |
| Launcher home-path overrides | ✅ Done | None |
| `suppress_default_model_flag` in strategy | ✅ Done | None (Phase 2 milestone already met) |
| Auto-seeded openrouter profile | ✅ Done | None |
| Mission Control UI for profile CRUD | ✅ Generic CRUD exists | Advanced fields (`command_behavior`, `file_templates`, `env_template`, `clear_env_keys`, `home_path_overrides`, `tags`, `priority`) not exposed in form |
| Dynamic routing by `provider_id` | ✅ Plumbing exists | No integration test coverage for openrouter value |
| Cooldown/slot integration tests | ✅ Generic pattern exists | Zero openrouter-specific tests |

## Scope

### In Scope

1. **Mission Control UI**: Expose advanced provider-profile fields in the `ProviderProfilesManager` form:
   - `command_behavior` (JSON editor for `suppress_default_model_flag` and future flags)
   - `tags` (multi-select or comma-separated input)
   - `priority` (numeric input)
   - `clear_env_keys` (multi-line text input, one key per line)
   - `account_label` (text input)

2. **Integration tests**: Add openrouter-specific integration tests:
   - Test dynamic routing with `profile_selector.provider_id = "openrouter"`
   - Test cooldown attaches to openrouter profile specifically
   - Test slot leasing against openrouter profile specifically

3. **Validation tests**: Verify the existing `suppress_default_model_flag` implementation works end-to-end through the managed runtime launch path (unit tests already exist; this adds the integration boundary coverage).

### Out of Scope

- Adding additional OpenRouter-backed model profiles beyond `qwen/qwen3.6-plus` (Phase 3)
- Provider-specific UI presets or validation rules (e.g., openrouter-specific form hints) — generic JSON editors suffice
- Changes to the auto-seeding logic or credential mechanisms
- Legacy auth-profile migration (already complete)
- OpenRouter-specific optional headers or attribution metadata

## Technical Approach

### 1. Frontend: Expose Advanced Profile Fields

**File**: `frontend/src/components/settings/ProviderProfilesManager.tsx`

- Extend `ProviderProfileFormState` interface with new fields:
  - `command_behavior: Record<string, any> | null`
  - `tags: string[]`
  - `priority: number | null`
  - `clear_env_keys: string[]`
  - `account_label: string | null`
- Update `defaultFormState()` and `toFormState()` to include new fields
- Add form sections for these fields in the create/edit dialog:
  - **Command Behavior**: JSON editor for `command_behavior` (pre-populated with `{}` when empty)
  - **Tags**: Comma-separated text input, split on save
  - **Priority**: Numeric input (nullable)
  - **Clear Env Keys**: Multi-line textarea, one key per line
  - **Account Label**: Simple text input
- Update the `saveProfile()` payload builder to include new fields
- Add read-only display of `tags` and `priority` in the profile table rows

**Testing**: Unit test the form state conversion functions with the new fields. Manual E2E test through Mission Control UI.

### 2. Integration Tests: OpenRouter Dynamic Routing

**File**: `tests/integration/workflows/temporal/workflows/test_run_agent_dispatch.py` (new test file or extend existing)

- Create a test profile with `provider_id: "openrouter"`, `runtime_id: "codex_cli"`, and `priority: 100`
- Submit a managed run request with `{"profileSelector": {"providerId": "openrouter"}}`
- Assert the correct openrouter profile is resolved
- Verify the launch payload includes openrouter-specific materialization fields

**File**: `tests/integration/workflows/temporal/workflows/test_provider_profile_routing.py` (new file)

- Test multi-profile routing: create two profiles with different `provider_id` values
- Assert `profile_selector.providerId` correctly filters to the matching profile
- Assert priority-based selection when multiple profiles match

### 3. Integration Tests: OpenRouter Cooldown and Slot Behavior

**File**: `tests/integration/services/temporal/workflows/test_agent_run.py` (extend existing)

- Add a test `test_openrouter_profile_cooldown_attaches_to_profile`:
  - Seed or create an openrouter provider profile with `cooldown_after_429_seconds: 300`
  - Simulate a 429 completion signal
  - Assert the cooldown report references the openrouter profile specifically
  - Assert cooldown expiry is calculated correctly

- Add a test `test_openrouter_profile_slot_leasing`:
  - Create an openrouter profile with `max_parallel_runs: 2`
  - Start a managed run targeting the openrouter profile
  - Assert slot is leased against the openrouter profile
  - Assert slot is released on completion/cancellation

**Pattern**: Follow the existing `MockProviderProfileManager` pattern from `test_agent_run.py`. Use the existing signal/query infrastructure. The key difference is using an openrouter-shaped profile with `provider_id: "openrouter"` and asserting profile-specific behavior.

## Implementation Order

**Frontend chain** (linear dependency):
1. **T001**: Extend ProviderProfile and ProviderProfileFormState interfaces
2. **T002**: Add advanced field inputs to the form
3. **T003**: Update save payload to include advanced fields
4. **T004**: Unit test form state conversions

**Integration tests** (can run in parallel with frontend chain):
5. **T005**: Dynamic routing test for `profile_selector.provider_id = "openrouter"`
6. **T006**: Multi-profile routing test (depends on T005)
7. **T007**: Cooldown attaches to openrouter profile test (independent)
8. **T008**: Slot leasing against openrouter profile test (independent)
9. **T009**: Cancellation releases slot test (depends on T008)

**Parallel execution**: T005-T009 can be implemented simultaneously with T001-T004 since they are independent test files with no shared code changes.

## Testing Strategy

### Unit Tests
- Frontend form state conversion with new fields
- JSON parsing/serialization for `command_behavior`

### Integration Tests
- Dynamic routing via `profile_selector.provider_id`
- Cooldown attachment to openrouter profile
- Slot leasing against openrouter profile
- Multi-profile priority-based selection

### Validation
- Run `./tools/test_unit.sh` for unit test verification
- Run integration tests via `docker compose -f docker-compose.test.yaml run --rm pytest bash -lc "pytest tests/integration -q --tb=short"`

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Frontend form complexity for JSON fields | Medium | Use simple JSON textareas with basic validation; avoid complex nested editors |
| Integration test flakiness with Temporal workflows | Medium | Use deterministic mock managers; avoid real network calls |
| Profile creation in tests conflicts with auto-seeded profile | Low | Use unique `profile_id` prefixes in tests (e.g., `test_openrouter_...`) |

## Rollout

1. Merge frontend changes and verify Mission Control can create/edit profiles with advanced fields
2. Merge integration tests and verify green on CI
3. No database migrations or breaking changes required
4. Existing auto-seeded openrouter profile continues to work unchanged
