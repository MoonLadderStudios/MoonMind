# Tasks: Codex CLI OpenRouter Phase 2

**Feature**: 126-codex-openrouter-phase2
**Mode**: runtime
**Branch**: `126-codex-openrouter-phase2`

## DOC-REQ Traceability

| DOC-REQ | Implementation Tasks | Validation Tasks |
|---------|---------------------|------------------|
| DOC-REQ-006: Mission Control creation/editing | T001, T002, T003 | T004 |
| DOC-REQ-007: profile_selector.provider_id routing | (plumbing exists) | T005, T006 |
| DOC-REQ-008: suppress_default_model_flag | (already done) | (already done) |
| DOC-REQ-009: Integration cooldown/slot coverage | T007, T008 | T009 |

---

## T001 — Extend ProviderProfile and ProviderProfileFormState interfaces with advanced fields

**Type**: Implementation
**Artifact**: `frontend/src/components/settings/ProviderProfilesManager.tsx`

1. Update the `ProviderProfile` interface to include optional fields:
   - `command_behavior?: Record<string, any> | null`
   - `tags?: string[] | null`
   - `priority?: number | null`
   - `clear_env_keys?: string[] | null`
   - `account_label?: string | null`

2. Add new fields to `ProviderProfileFormState`:
   - `commandBehavior: string` (JSON string, empty object default)
   - `tagsText: string` (comma-separated tags)
   - `priority: string` (nullable number as string)
   - `clearEnvKeysText: string` (newline-separated env keys)
   - `accountLabel: string` (nullable text)

3. Update `defaultFormState()` to include defaults for new fields.
4. Update `toFormState()` to serialize new fields from profile response.

**Dependencies**: None

---

## T002 — Add advanced field inputs to the provider profile form

**Type**: Implementation
**Artifact**: `frontend/src/components/settings/ProviderProfilesManager.tsx`

Add form inputs for the new fields after the existing rate limit policy section:
- **Command Behavior**: textarea with JSON format hint (rows=3, monospace font)
- **Tags**: text input with placeholder "tag1, tag2, tag3"
- **Priority**: number input (nullable, min=0)
- **Clear Env Keys**: textarea with placeholder "OPENAI_API_KEY\nOPENAI_BASE_URL" (rows=4)
- **Account Label**: text input

Wire each input to update the form state.

**Dependencies**: T001

---

## T003 — Update save payload to include advanced fields

**Type**: Implementation
**Artifact**: `frontend/src/components/settings/ProviderProfilesManager.tsx`

Update the `saveMutation` payload builder to:
- Parse `commandBehavior` JSON string into `command_behavior` dict (or null if empty/invalid)
- Split `tagsText` by comma into `tags` array (filter out empty strings)
- Parse `priority` as number (or null if blank)
- Split `clearEnvKeysText` by newline into `clear_env_keys` array
- Include `account_label` from `accountLabel` (or null if blank)

Update `toFormState()` to deserialize these fields from the profile response for edit mode.

**Dependencies**: T001, T002

---

## T004 — Unit test form state conversions with advanced fields

**Type**: Validation
**Artifact**: `frontend/src/components/settings/ProviderProfilesManager.test.tsx` (CREATE NEW)

Test:
- `defaultFormState()` includes all new fields with correct defaults
- `toFormState()` correctly serializes a profile with all advanced fields
- `toFormState()` handles null/missing advanced fields gracefully
- Payload builder correctly parses commandBehavior JSON, tags text, priority, clearEnvKeysText
- Invalid commandBehavior JSON is handled gracefully (falls back to null)

Note: No existing test file found. Create new test file following patterns from nearby components.

**Dependencies**: T003

---

## T005 — Integration test: dynamic routing via profile_selector.provider_id = "openrouter"

**Type**: Validation
**Artifact**: `tests/integration/workflows/temporal/workflows/test_run_agent_dispatch.py` (create or extend)

Test:
- Create a test profile with `provider_id: "openrouter"`, `runtime_id: "codex_cli"`, `priority: 100`
- Submit a managed run request with `{"profileSelector": {"providerId": "openrouter"}}`
- Assert the correct openrouter profile is resolved
- Verify the launch payload includes `provider_id=openrouter` and `runtime_materialization_mode=composite`

**Dependencies**: None (can run in parallel with T001-T004)

---

## T006 — Integration test: multi-profile priority-based selection

**Type**: Validation
**Artifact**: `tests/integration/workflows/temporal/workflows/test_run_agent_dispatch.py`

Test:
- Create two profiles with `provider_id: "openrouter"` but different priorities (50 and 150)
- Submit a request with `profileSelector.providerId = "openrouter"`
- Assert the higher priority profile (150) is selected
- Disable the higher priority profile
- Assert the lower priority profile (50) is selected instead

**Dependencies**: T005

---

## T007 — Integration test: cooldown attaches to openrouter profile specifically

**Type**: Validation
**Artifact**: `tests/integration/services/temporal/workflows/test_agent_run.py` (extend existing)

Test:
- Create an openrouter profile with `cooldown_after_429_seconds: 300`
- Start a managed run targeting the profile
- Simulate a 429 completion signal
- Assert the cooldown report references the openrouter profile specifically (not all codex_cli runs)
- Assert cooldown expiry is calculated as 300 seconds

**Dependencies**: None (can run in parallel)

---

## T008 — Integration test: slot leasing against openrouter profile

**Type**: Validation
**Artifact**: `tests/integration/services/temporal/workflows/test_agent_run.py` (extend existing)

Test:
- Create an openrouter profile with `max_parallel_runs: 2`
- Start a managed run targeting the profile
- Assert slot is leased against the openrouter profile
- Complete the run
- Assert slot is released

**Dependencies**: None (can run in parallel)

---

## T009 — Integration test: cancellation releases openrouter profile slot

**Type**: Validation
**Artifact**: `tests/integration/services/temporal/workflows/test_agent_run.py` (extend existing)

Test:
- Create an openrouter profile with `max_parallel_runs: 2`
- Start a managed run and wait for slot lease
- Cancel the workflow
- Assert the slot lease is released

**Dependencies**: T008

---

## Runtime Scope Validation

- [X] T001: Frontend type/interface extension — no runtime file changes
- [X] T002: Frontend form inputs — no runtime file changes
- [X] T003: Frontend payload builder — no runtime file changes
- [X] T004: Frontend unit tests — validation task
- [X] T005: Integration test for dynamic routing — validation task
- [X] T006: Integration test for multi-profile routing — validation task
- [X] T007: Integration test for cooldown — validation task
- [X] T008: Integration test for slot leasing — validation task
- [X] T009: Integration test for cancellation slot release — validation task

**Runtime mode**: All implementation tasks are frontend-only (T001-T003). All validation tasks are integration tests (T004-T009). No backend Python changes are required because the backend already supports all advanced fields from Phase 1 work.
