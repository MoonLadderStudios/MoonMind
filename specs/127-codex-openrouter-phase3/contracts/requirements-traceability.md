# Requirements Traceability: Phase 3 — Codex OpenRouter Generalization

**Feature**: 127-codex-openrouter-phase3

This document maps every source requirement (DOC-REQ-*) from the spec to its implementation location and verification method.

---

## 1. DOC-REQ Summary

| DOC-REQ | Implementation Surfaces | Validation Strategy |
|---------|------------------------|---------------------|
| DOC-REQ-001 | Code audit: `materializer.py`, `managed_agent_adapter.py`, `launcher.py`, `codex_cli.py` — zero provider-specific branching | Grep audit: `grep -r "openrouter" moonmind/workflows/` → 0 matches; T005 |
| DOC-REQ-002 | No code changes — data-only profile creation via REST API / DB row | Integration tests: two distinct OpenRouter profiles independently resolvable (T006) |
| DOC-REQ-003 | `agent_runtime_models.py` — Pydantic model rewrite; `managed_agent_adapter.py` — adapter cleanup; `artifacts.py` — API response projection cleanup | Unit tests: full contract acceptance, legacy rejection, new validation tests (T003, T004) |

---

## 2. Source Requirements (from `docs/ManagedAgents/CodexCliOpenRouter.md`)

### DOC-REQ-001 — OpenRouter as Reference Implementation

**Source**: §15 Phase 3, line 1
**Statement**: Treat OpenRouter as the reference implementation for config-bundle Codex providers.

| Trace | Detail |
|-------|--------|
| **Spec FR-011** | No module in materialization, adapter, or launcher path may contain provider-specific branching for OpenRouter. |
| **Implementation** | Code audit confirms zero OpenRouter-specific branching in `materializer.py`, `managed_agent_adapter.py`, `launcher.py`, `codex_cli.py`. `grep -r "openrouter" moonmind/workflows/` returns 0 matches. |
| **File** | `/mnt/d/code/MoonMind/moonmind/workflows/adapters/materializer.py` — data-driven via `file_templates` |
| **File** | `/mnt/d/code/MoonMind/moonmind/workflows/adapters/managed_agent_adapter.py` — data-driven via profile dict |
| **File** | `/mnt/d/code/MoonMind/moonmind/workflows/temporal/runtime/launcher.py` — no provider identity references |
| **Verification** | Code audit (research.md §1.5). All provider behavior is driven by `file_templates`, `env_template`, `command_behavior`, etc. |
| **Success Criterion** | SC-002: `ProviderProfileMaterializer` contains zero provider-specific branching for OpenRouter. |

### DOC-REQ-002 — Additional OpenRouter-Backed Model Defaults

**Source**: §15 Phase 3, line 2
**Statement**: Reuse the same pattern for additional OpenRouter-backed model defaults.

| Trace | Detail |
|-------|--------|
| **Spec FR-001** | System must support creating arbitrary OpenRouter-backed profiles by varying only profile data, without code changes per profile. |
| **Spec FR-014** | System must support at least two distinct OpenRouter-backed provider profiles simultaneously. |
| **Implementation** | No code changes needed. Adding a new OpenRouter profile is a data-only operation: create a DB row via REST API with `runtime_id=codex_cli`, `provider_id=openrouter`, `default_model=<model-string>`, and appropriate `file_templates`/`env_template`. |
| **File** | `/mnt/d/code/MoonMind/api_service/api/routers/provider_profiles.py` — REST API supports CRUD |
| **File** | `/mnt/d/code/MoonMind/api_service/db/models.py` — DB model supports arbitrary profiles |
| **File** | `/mnt/d/code/MoonMind/api_service/services/provider_profile_service.py` — `sync_provider_profile_manager` syncs all enabled profiles |
| **Verification** | Manual test: create second OpenRouter profile via REST API → launch Codex run → verify correct model used. |
| **Success Criterion** | SC-001: New profile usable without code changes. SC-004: Integration tests pass for two distinct profiles. |

### DOC-REQ-003 — Legacy Auth-Profile Alignment

**Source**: §15 Phase 3, line 3
**Statement**: Align any remaining legacy auth-profile persistence with the provider-profile contract.

| Trace | Detail |
|-------|--------|
| **Spec FR-007** | `ManagedAgentProviderProfile` Pydantic model must include all provider-profile fields; `auth_mode` must be removed. |
| **Spec FR-012** | Pydantic model must remain compatible with the DB model — all DB fields must be representable. |
| **Implementation** | `ManagedAgentProviderProfile` in `agent_runtime_models.py` rewritten to match provider-profile contract. `auth_mode` removed. All missing fields added: `credential_source`, `runtime_materialization_mode`, `tags`, `priority`, `clear_env_keys`, `env_template`, `file_templates`, `home_path_overrides`, `command_behavior`, `secret_refs`, `volume_mount_path`, `max_lease_duration_seconds`, `owner_user_id`. |
| **File** | `/mnt/d/code/MoonMind/moonmind/schemas/agent_runtime_models.py` — Pydantic model rewrite |
| **File** | `/mnt/d/code/MoonMind/moonmind/workflows/adapters/managed_agent_adapter.py` — adapter cleanup (replace `auth_mode` with `credential_source` in metadata) |
| **Verification** | Unit tests: instantiate with full fields → validate; instantiate with legacy `auth_mode` → reject. |
| **Success Criterion** | SC-003: Pydantic model accepts all provider-profile fields and rejects/migrates legacy `auth_mode`. |

---

## 2. Functional Requirements Traceability

| FR-ID | Requirement | Plan Section | Status |
|-------|-------------|-------------|--------|
| FR-001 | Arbitrary OpenRouter profiles via data only | Plan §3.1, §3.2 | Addressed — no code needed per profile |
| FR-002 | Materializer renders file_templates to arbitrary paths | Plan §3.1 | Already satisfied — materializer is data-driven |
| FR-003 | home_path_overrides applied before launch | Plan §3.1 | Already satisfied — adapter maps field |
| FR-004 | CodexCliStrategy honors suppress_default_model_flag | Plan §3.3 | Already satisfied — codex_cli.py implements this |
| FR-005 | clear_env_keys cleared before env injection | Plan §3.1 | Already satisfied — materializer implements this |
| FR-006 | Adapter maps all provider-profile fields | Plan §3.2 | Already satisfied — adapter maps all fields |
| **FR-007** | Pydantic model aligned with provider-profile contract | **Plan §3.1** | **Primary change — model rewrite** |
| FR-008 | Generated files written with mode 0600 | Plan §3.1 | Already satisfied — materializer defaults to 0600 |
| FR-009 | Dynamic provider selection via profile_selector | Plan §3.2 | Already satisfied — adapter implements routing |
| FR-010 | Profile CRUD via Mission Control / REST API | Plan §3.1 | Already satisfied — API routes complete |
| **FR-011** | No provider-specific branching in materialization/adapter/launcher | **Plan §3.1, §3.2** | **Already satisfied — verified by grep audit** |
| **FR-012** | Pydantic model compatible with DB model | **Plan §3.1** | **Primary change — field alignment** |
| FR-013 | ManagedRuntimeProfile carries all launch fields | Plan §3.1 | Already satisfied — runtime model complete |
| **FR-014** | Support at least two distinct OpenRouter profiles | **Plan §3.1, §4** | **No code needed — data-only operation** |

---

## 3. Non-Functional Requirements Traceability

| NFR-ID | Requirement | Verification | Status |
|--------|-------------|--------------|--------|
| NFR-001 | Profile resolution < 500ms | Benchmark with 50 profiles | Already satisfied — in-memory dict lookup |
| NFR-002 | Materialization < 200ms | Benchmark TOML rendering | Already satisfied — simple string rendering |
| NFR-003 | No raw credentials in Temporal payloads | Code audit of launch pipeline | Already satisfied — only profile_id persisted |
| NFR-004 | Profile values inspectable via Mission Control | Manual UI verification | Already satisfied — REST API exposes all fields |

---

## 4. Success Criteria Traceability

| SC-ID | Criterion | Verification Method | Plan Section |
|-------|-----------|---------------------|--------------|
| SC-001 | New OpenRouter profile usable without code changes | Manual test: create profile → launch → verify model | Plan §6 (Integration Tests) |
| SC-002 | Zero provider-specific branching in materializer | Code audit: grep for `openrouter` in workflow code | Plan §3.1, Research §1.5 |
| SC-003 | Pydantic model accepts full contract, rejects legacy auth_mode | Unit tests | Plan §3.4, §6 |
| SC-004 | Integration tests pass for two distinct OpenRouter profiles | Run integration test suite with two profiles | Plan §6.3 |
| SC-005 | speckit-analyze reports no CRITICAL/HIGH gaps | Run `/speckit-analyze` after plan and tasks | Post-plan gate |
| SC-006 | All existing tests pass without regression | Run `./tools/test_unit.sh` and integration suite | Plan §5 |
| SC-007 | Generated files written with 0600 and cleaned up | Unit test on materializer; integration test | Plan §6.1 |

---

## 5. Edge Case Coverage

| EC-ID | Scenario | Coverage |
|-------|----------|----------|
| EC-001 | file_templates path already exists | Already handled by materializer `_materialize_file_template` |
| EC-002 | Invalid runtime_materialization_mode | Pydantic validation rejects unknown values (fail-fast) |
| EC-003 | Missing secret at launch time | SecretResolverBoundary raises actionable error |
| EC-004 | Path traversal in file_templates | Already handled by `is_relative_to` check in materializer |
| EC-005 | Two profiles with identical priority | Tie-broken by available_slots (adapter `_resolve_profile`) |
| EC-006 | env_template references undefined secret_ref | Materializer raises `Unknown template variable` error |
| EC-007 | credential_source=none with config_bundle mode | Valid — Pydantic model allows this combination |
| **EC-008** | Legacy auth_mode value submitted to aligned schema | **Pydantic extra="forbid" rejects it** |
| EC-009 | clear_env_keys removes base env variable | Layering rule (§11.2 of ProviderProfiles) protects PATH, HOME |
| EC-010 | File permissions not 0600 | Materializer defaults to 0600; explicit permissions override |
