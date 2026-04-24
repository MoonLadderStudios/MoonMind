# Feature Specification: Codex OpenRouter Phase 3 — Generalization

**Feature Branch**: `127-codex-openrouter-phase3`
**Created**: 2026-04-03
**Status**: Draft
**Input**: Fully implement Phase 3 from `docs/ManagedAgents/CodexCliOpenRouter.md`

## Source Document Requirements

Source: `docs/ManagedAgents/CodexCliOpenRouter.md`, Section 15 — Rollout Plan, Phase 3

| ID | Source | Requirement |
|----|--------|-------------|
| DOC-REQ-001 | §15 Phase 3, line 1 | Treat OpenRouter as the reference implementation for config-bundle Codex providers |
| DOC-REQ-002 | §15 Phase 3, line 2 | Reuse the same pattern for additional OpenRouter-backed model defaults |
| DOC-REQ-003 | §15 Phase 3, line 3 | Align any remaining legacy auth-profile persistence with the provider-profile contract |

### DOC-REQ-001 — Reference Implementation

The OpenRouter provider profile implementation must be structured as a reusable reference pattern that other config-bundle Codex providers can follow. All materialization, config generation, and launch plumbing must be provider-agnostic beyond OpenRouter-specific configuration values. Concretely, no module in the materialization or launch path should contain OpenRouter-specific branching; provider identity should only appear in profile data, config values, and seed definitions.

### DOC-REQ-002 — Additional Model Defaults

The system must support creating additional OpenRouter-backed provider profiles that differ primarily by `default_model`, `profile_id`, and provider-specific labels, without requiring new code per profile. Adding a new OpenRouter model profile should be a data-only operation (profile record creation via API/UI), not a code change.

### DOC-REQ-003 — Legacy Auth-Profile Alignment

The `ManagedAgentProviderProfile` Pydantic model in `moonmind/schemas/agent_runtime_models.py` remains legacy-shaped: it uses `auth_mode` as a required field and lacks the richer provider-profile fields (`credential_source`, `runtime_materialization_mode`, `command_behavior`, `env_template`, `file_templates`, `home_path_overrides`, `clear_env_keys`, `secret_refs`). The database model (`api_service/db/models.py`) already has these fields, but the Pydantic contract has not been aligned. This must be resolved so the Pydantic schema matches the provider-profile contract defined in `docs/Security/ProviderProfiles.md`.

---

## Current State Assessment

The following Phase 1 / Phase 2 plumbing is **already implemented** in the codebase and should be verified rather than re-implemented:

| Component | File | Status |
|-----------|------|--------|
| Rich `ManagedRuntimeProfile` fields | `moonmind/schemas/agent_runtime_models.py` | **Done** — has `credential_source`, `runtime_materialization_mode`, `command_behavior`, `env_template`, `file_templates`, `home_path_overrides`, `clear_env_keys`, `secret_refs` |
| Adapter field mapping | `moonmind/workflows/adapters/managed_agent_adapter.py` | **Done** — maps all provider-profile fields into `ManagedRuntimeProfile` |
| Path-aware file materialization | `moonmind/workflows/adapters/materializer.py` | **Done** — supports `RuntimeFileTemplate[]`, TOML rendering, `runtime_support_dir` template vars, permissions, cleanup |
| Home-path application | `moonmind/workflows/adapters/materializer.py` | **Done** — `home_path_overrides` applied in `materialize()` |
| Codex strategy model-flag suppression | `moonmind/workflows/temporal/runtime/strategies/codex_cli.py` | **Done** — honors `suppress_default_model_flag` |
| OpenRouter auto-seed path | `api_service/` (seed tests exist) | **Done** — tests at `tests/unit/api_service/test_provider_profile_auto_seed.py` |
| DB model with full fields | `api_service/db/models.py` | **Done** — `ManagedAgentProviderProfile` table has all provider-profile columns |
| Launcher `home_path_overrides` | `moonmind/workflows/temporal/runtime/launcher.py` | **Done** — materializer applies `home_path_overrides` before strategy shaping |

The **remaining gaps** that Phase 3 must close are:

1. **Legacy Pydantic schema**: `ManagedAgentProviderProfile` in `agent_runtime_models.py` uses `auth_mode` (required) and is missing the rich provider-profile fields. It must be aligned with the DB model and the Provider Profiles contract.
2. **Provider-agnostic guarantee**: No provider-specific branching should exist in materializer, adapter, or launcher code. The OpenRouter profile should be verifiably data-driven.
3. **Multi-profile operational support**: The system must demonstrably support multiple OpenRouter-backed profiles with different models, selectable independently.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Create Additional OpenRouter Model Profiles (Priority: P1)

A MoonMind operator or Mission Control user can create new provider profiles for different OpenRouter models (e.g., `qwen/qwen-max`, `anthropic/claude-sonnet-4-20250514`) by specifying only the profile identity, model string, and credential binding. No code changes are required per profile.

**Why this priority**: Phase 3's generalization goal is blocked if new OpenRouter models still require custom code instead of profile data.

**Independent Test**: Seed a second OpenRouter provider profile with a different model string, launch a Codex run against it, and verify the correct model is used.

**Acceptance Scenarios**:

1. **Given** an existing OpenRouter provider profile, **When** a new profile is created with a different `default_model`, **Then** Codex runs use the new model without any code changes.
2. **Given** a new OpenRouter profile, **When** launched via exact `execution_profile_ref`, **Then** the per-run config bundle selects the correct model and provider.
3. **Given** two OpenRouter profiles with different priorities, **When** a request uses `profile_selector.provider_id = "openrouter"`, **Then** the highest-priority available profile is selected.

---

### User Story 2 — Config-Bundle Materialization is Provider-Agnostic (Priority: P1)

The file materialization system (`ProviderProfileMaterializer`) handles any config-bundle provider (OpenRouter, future providers) using the same path-aware rendering pipeline. Provider-specific logic is limited to configuration values, not materialization behavior.

**Why this priority**: This is the core of Phase 3 — ensuring the OpenRouter pattern generalizes to other providers.

**Independent Test**: Inspect the materializer, adapter, and launcher code to confirm zero provider-specific branching for OpenRouter; verify that config rendering is driven entirely by `file_templates` data.

**Acceptance Scenarios**:

1. **Given** a provider profile with `runtime_materialization_mode = composite` and `file_templates`, **When** materialization runs, **Then** files are rendered to the specified paths regardless of provider identity.
2. **Given** two different provider profiles with different `file_templates`, **When** both are materialized, **Then** each produces correct config at its specified paths.
3. **Given** a `file_templates` entry with `format = "toml"`, **When** rendered, **Then** the output is valid TOML matching the `content_template` structure.

---

### User Story 3 — Legacy Auth-Profile Schema Alignment (Priority: P1)

The `ManagedAgentProviderProfile` Pydantic model is updated to match the provider-profile contract, eliminating the dual-schema gap between the DB model (already complete) and the Pydantic model (still legacy-shaped).

**Why this priority**: Without this, the API and validation layer cannot express the full provider-profile contract, and new fields added via the API would fail validation.

**Independent Test**: Instantiate a `ManagedAgentProviderProfile` with all provider-profile fields (including `credential_source`, `runtime_materialization_mode`, `file_templates`, `home_path_overrides`, `command_behavior`, `clear_env_keys`, `secret_refs`) and verify it passes validation.

**Acceptance Scenarios**:

1. **Given** the updated `ManagedAgentProviderProfile` schema, **When** a profile dict is constructed with `credential_source = "secret_ref"` and `runtime_materialization_mode = "composite"`, **Then** the model validates successfully.
2. **Given** a legacy profile dict using `auth_mode = "api_key"`, **When** the system processes it, **Then** it is either migrated or rejected with a clear error (no silent acceptance of legacy-only fields).
3. **Given** a profile with `file_templates` containing a TOML config entry, **When** serialized via `model_dump(by_alias=True)`, **Then** the output round-trips correctly through the API layer.

---

### User Story 4 — Operator Creates OpenRouter Profiles via Mission Control / REST (Priority: P2)

An operator can create, view, edit, and delete OpenRouter-backed Codex provider profiles through Mission Control and the REST API without touching code or seed scripts.

**Why this priority**: Enables self-service profile management for production deployments.

**Independent Test**: Use the REST API to create a new OpenRouter profile, then launch a Codex run targeting it.

**Acceptance Scenarios**:

1. **Given** the REST API, **When** a POST creates a profile with `runtime_id = "codex_cli"`, `provider_id = "openrouter"`, `default_model = "qwen/qwen-max"`, **Then** the profile is persisted and returned on subsequent GET.
2. **Given** an existing profile, **When** PATCH updates its `default_model` or `priority`, **Then** the update is reflected in subsequent profile resolution.
3. **Given** a profile that has active leases, **When** DELETE is attempted, **Then** the system either rejects or warns about active usage.

---

### Edge Cases

| ID | Scenario | Expected Behavior |
|----|----------|-------------------|
| EC-001 | `file_templates` references a path that already exists | Uses `merge_strategy` to determine behavior; `replace` overwrites, `deep_merge` merges |
| EC-002 | Provider profile has an invalid or unsupported `runtime_materialization_mode` | Fails fast with clear error at profile validation time, not at launch time |
| EC-003 | Secret referenced by `secret_refs` is missing at launch time | Aborts launch with actionable error naming the missing secret role and profile |
| EC-004 | `file_templates[].path` resolves outside `runtime_support_dir` | Rejected with path-traversal error during materialization |
| EC-005 | Two OpenRouter profiles have identical `priority` and `default_model` | Tie-broken by `available_slots` (most free slots wins); both are usable but one is preferred |
| EC-006 | `env_template` value references a `secret_ref` key not defined in `secret_refs` | Fails at materialization time with clear error naming the missing ref |
| EC-007 | Provider profile with `credential_source = "none"` and `runtime_materialization_mode = "config_bundle"` | Valid: a config-only provider that needs no credentials (e.g., local mock endpoint) |
| EC-008 | Legacy `auth_mode` value (`"oauth"`, `"api_key"`) submitted to the aligned schema | Either auto-migrated (`"oauth"` → `"oauth_volume"`, `"api_key"` → `"secret_ref"`) or rejected with migration guidance |
| EC-009 | `clear_env_keys` removes a key that is also required by the base environment (e.g., `PATH`) | `clear_env_keys` only removes provider-specific keys; base environment variables like `PATH`, `HOME` are protected by the layering rule (§11.2 of ProviderProfiles) |
| EC-010 | Generated config file permissions are not `0600` | Default is `0600`; explicit `permissions` in `RuntimeFileTemplate` overrides; validated on write |

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST support creating arbitrary OpenRouter-backed provider profiles by varying only `profile_id`, `default_model`, `provider_label`, and `secret_refs`, without code changes per profile.
- **FR-002**: `ProviderProfileMaterializer` MUST render `file_templates` to arbitrary paths specified in the profile, with support for TOML, JSON, and plain text formats.
- **FR-003**: System MUST apply `home_path_overrides` from the provider profile into the subprocess env before runtime strategy shaping.
- **FR-004**: `CodexCliStrategy` MUST honor `command_behavior.suppress_default_model_flag` and omit `-m` when the config bundle already sets the default model.
- **FR-005**: System MUST clear `clear_env_keys` before injecting profile-resolved env vars to prevent ambient credential bleed.
- **FR-006**: `ManagedAgentAdapter` MUST map all provider-profile fields (`credential_source`, `runtime_materialization_mode`, `command_behavior`, `env_template`, `file_templates`, `home_path_overrides`) into `ManagedRuntimeProfile`.
- **FR-007**: The `ManagedAgentProviderProfile` Pydantic model MUST be aligned with the provider-profile contract: it MUST include `credential_source`, `runtime_materialization_mode`, `clear_env_keys`, `env_template`, `file_templates`, `home_path_overrides`, `command_behavior`, and `secret_refs`; and `auth_mode` MUST be removed from the model (incoming validation rejects it via `extra="forbid"`). Outgoing API responses MUST NOT emit a derived `auth_mode` field alongside `credential_source`; `credential_source` is the sole canonical field. Any existing code path that projects `auth_mode` into API response dicts (e.g., `build_canonical_start_handle` in `artifacts.py`) MUST be updated to emit only `credential_source`.
- **FR-008**: Generated config files MUST be written with mode `0600` by default and cleaned up on run completion.
- **FR-009**: System MUST support dynamic provider selection via `profile_selector.provider_id` in addition to exact `execution_profile_ref`.
- **FR-010**: Provider profile creation, update, and deletion MUST be available through the REST API. Mission Control UI for profile management is deferred to a follow-up feature.
- **FR-011**: No module in the materialization, adapter, or launcher path MAY contain provider-specific branching for OpenRouter. All provider behavior MUST be driven by profile data (`file_templates`, `env_template`, `command_behavior`, etc.).
- **FR-012**: The `ManagedAgentProviderProfile` Pydantic model MUST remain compatible with the DB model in `api_service/db/models.py` — all fields present in the DB row MUST be representable in the Pydantic schema.
- **FR-013**: When a provider profile is resolved and materialized, the resulting `ManagedRuntimeProfile` MUST contain all fields needed for launch without requiring fallback to the raw profile dict.
- **FR-014**: The system MUST support at least two distinct OpenRouter-backed provider profiles simultaneously (e.g., `codex_openrouter_qwen36_plus` and `codex_openrouter_qwen_max`), each independently selectable and launchable.

### Non-Functional Requirements

- **NFR-001**: Profile resolution MUST complete within 500ms for a typical deployment with ≤ 50 profiles per runtime.
- **NFR-002**: Materialization of `file_templates` (including TOML rendering) MUST complete within 200ms for a typical Codex config bundle.
- **NFR-003**: No raw credential values MAY appear in Temporal workflow payloads, run metadata, artifact files, or log output at any point in the launch pipeline.
- **NFR-004**: All provider-specific configuration values in profile records MUST be inspectable by operators through Mission Control without exposing secret values.

### Key Entities

- **Provider Profile** (`ManagedAgentProviderProfile`): A configuration record binding a `runtime_id` + `provider_id` to credential sources, materialization templates, policy limits, and command behavior. Persisted in `managed_agent_provider_profiles` DB table and represented by the aligned Pydantic model.
- **Runtime File Template** (`RuntimeFileTemplate`): A structured descriptor (`path`, `format`, `merge_strategy`, `content_template`, `permissions`) for path-aware file materialization.
- **Per-Run Support Directory**: A MoonMind-owned workspace subdirectory (`.moonmind/`) containing generated config bundles for the current run. Created during materialization, cleaned up on run completion.
- **Managed Runtime Profile** (`ManagedRuntimeProfile`): The launch-time payload shaped from a Provider Profile. Carries all fields needed for subprocess launch without requiring further profile lookups.

---

## Implementation Scope

### What is in scope for Phase 3

1. **Schema alignment**: Update `ManagedAgentProviderProfile` in `moonmind/schemas/agent_runtime_models.py` to match the full provider-profile contract. Remove or deprecate `auth_mode` as a required field. Add all missing fields: `credential_source`, `runtime_materialization_mode`, `clear_env_keys`, `env_template`, `file_templates`, `home_path_overrides`, `command_behavior`, `secret_refs`.
2. **Provider-agnostic verification**: Audit `materializer.py`, `managed_agent_adapter.py`, `launcher.py`, and `codex_cli.py` for any provider-specific branching. Remove or refactor any OpenRouter-specific conditionals that should be data-driven.
3. **Multi-profile support**: Ensure the auto-seed mechanism can produce multiple OpenRouter profiles, and that the REST API / Mission Control supports CRUD for them.
4. **Migration path**: Provide a data migration or compatibility layer for any existing profile records that use the legacy `auth_mode` field instead of `credential_source`.
5. **`rate_limit_policy` type**: The DB model stores `rate_limit_policy` as `ManagedAgentRateLimitPolicy` (enum), while the current Pydantic model uses `dict[str, Any]`. The Pydantic model retains `dict[str, Any]` for this feature — the enum-to-dict serialization is handled at the API/DB boundary. Aligning the Pydantic type with the DB enum is deferred to a follow-up change, as it would require changes to the API serialization layer and is not required to close the `auth_mode` / provider-profile gap (DOC-REQ-003).

### What is out of scope

- New provider implementations beyond OpenRouter (the infrastructure should be ready, but actual new providers are separate features).
- Changes to the Temporal workflow orchestration model or ProviderProfileManager signals.
- Changes to the Secrets System backend or secret storage model.
- OpenRouter-specific optional headers or attribution metadata (deferred per §16.3 of the design doc).
- Changes to the `codex_cli` strategy beyond honoring `command_behavior` (existing behavior is sufficient).
- `OAuthProviderSpec` in `moonmind/workflows/temporal/runtime/providers/base.py` and its registry entries: the `auth_mode` field in this TypedDict describes OAuth session type for terminal/bootstrap flows, not credential sourcing. It is semantically unrelated to `ManagedAgentProviderProfile.auth_mode` and is not affected by this feature.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

| ID | Criterion | Verification Method |
|----|-----------|-------------------|
| SC-001 | A new OpenRouter provider profile can be created and used for a Codex run by adding only profile data (no code changes). | Manual test: create profile via REST API → launch Codex run → verify correct model used. |
| SC-002 | `ProviderProfileMaterializer` contains zero provider-specific branching for OpenRouter; all behavior is driven by `file_templates` data. | Code audit: grep for `openrouter` in `materializer.py`, `launcher.py`, `managed_agent_adapter.py`. Zero matches expected. |
| SC-003 | The `ManagedAgentProviderProfile` Pydantic model accepts all provider-profile fields and rejects or migrates legacy `auth_mode`-only records. | Unit tests: instantiate with full fields; instantiate with legacy `auth_mode`; verify behavior. |
| SC-004 | Integration tests pass for at least two distinct OpenRouter-backed provider profiles with different model strings. | Run `test_provider_profile_auto_seed.py`-style integration test with two profiles. |
| SC-005 | `speckit-analyze` reports no CRITICAL or HIGH consistency gaps across spec, plan, and tasks artifacts. | Run `/speckit-analyze` after plan and tasks are created. |
| SC-006 | All existing unit and integration tests pass without regression. | Run `./tools/test_unit.sh` and integration test suite. |
| SC-007 | Generated Codex config files are written with mode `0600` and cleaned up after run completion. | Unit test on materializer; integration test verifying cleanup. |

---

## Open Questions

| ID | Question | Recommendation |
|----|----------|----------------|
| OQ-001 | Should `auth_mode` be removed entirely from `ManagedAgentProviderProfile` or kept as an optional aliased field with auto-migration? | Remove entirely (Constitution Principle XIII: pre-release, delete don't deprecate). Provide a one-time data migration mapping `oauth` → `oauth_volume`, `api_key` → `secret_ref`. |
| OQ-002 | Should the auto-seed mechanism create multiple default OpenRouter profiles, or only one (with operators adding more via UI)? | Seed only one (`codex_openrouter_qwen36_plus`). Additional profiles are operator-created. Auto-seeding too many profiles creates clutter. |
| OQ-003 | Does the current `ManagedRuntimeProfile` already carry all fields needed for launch, or are there gaps between what the adapter populates and what the launcher/materializer consume? | Current assessment: `ManagedRuntimeProfile` is complete. The gap is in `ManagedAgentProviderProfile` (the Pydantic model used for API/validation), not `ManagedRuntimeProfile`. |
| OQ-004 | Are there any other legacy-shaped Pydantic models or API contracts that also need alignment beyond `ManagedAgentProviderProfile`? | **Resolved: Out of scope.** The `OAuthProviderSpec` TypedDict in `moonmind/workflows/temporal/runtime/providers/base.py` contains an `auth_mode: str` field, and registry entries in `providers/registry.py` populate it with `auth_mode="oauth"`. This `auth_mode` is semantically distinct from `ManagedAgentProviderProfile.auth_mode`: it describes the *auth session type* for the OAuth terminal/bootstrap flow (always `"oauth"`), not the credential sourcing strategy. `OAuthProviderSpec` governs session lifecycle (session transport, bootstrap commands, success checks), while `ManagedAgentProviderProfile` governed profile-level credential configuration. The two `auth_mode` uses are unrelated concepts sharing a historical name. No migration of `OAuthProviderSpec` is needed for this feature. |

---

## Constitution Check

| Principle | Assessment |
|-----------|------------|
| I. Orchestrate, Don't Recreate | **PASS** — OpenRouter support goes through the existing Codex CLI runtime via provider profiles. No new runtime is created. |
| II. One-Click Deployment | **PASS** — Auto-seed creates a working OpenRouter profile when `OPENROUTER_API_KEY` is present at startup. |
| III. Avoid Vendor Lock-In | **PASS** — All provider-specific behavior is data-driven via `file_templates`, `env_template`, and `command_behavior`. No OpenRouter-specific code paths in materialization or launch. |
| IV. Own Your Data | **PASS** — Generated config files live in MoonMind-owned per-run support directories, not in repos or user-global state. |
| V. Skills Are First-Class | **N/A** — This feature does not introduce new skills. |
| VI. Bittersweet Lesson | **PASS** — The provider-profile contract is a thick, stable interface. Volatility (provider IDs, model strings, config shapes) is isolated in data, not code. |
| VII. Powerful Runtime Configurability | **PASS** — Profiles are created/edited at runtime via REST API / Mission Control. No code edits or rebuilds needed. |
| VIII. Modular and Extensible Architecture | **PASS** — Adding a new provider requires only a new profile record, not changes to core orchestration. |
| IX. Resilient by Default | **PASS** — Missing secrets fail fast with actionable errors. Generated files are cleaned up on completion. |
| X. Facilitate Continuous Improvement | **N/A** — No direct improvement-signal capture in this feature. |
| XI. Spec-Driven Development | **PASS** — This spec provides requirements, user stories, edge cases, and success criteria before implementation. |
| XII. Canonical Documentation | **PASS** — Migration/implementation tracking belongs in `local-only handoffs`, not canonical docs. |
| XIII. Pre-Release: Delete, Don't Deprecate | **PASS** — `auth_mode` will be removed, not aliased. Legacy records will be migrated, not dual-maintained. |
