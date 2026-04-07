# Feature: Integration Test Improvements — Phase 5 (Repo Conventions & Specs)

## Overview

Phase 5 of the Integration Test Improvements effort updates repository conventions and documentation so the new test taxonomy (hermetic integration vs. provider verification) is explicit and sustainable. Phases 1–4 are already implemented: test markers exist in `pyproject.toml`, `tests/provider/jules/` exists, both GitHub Actions workflows (`pytest-integration-ci.yml` and `provider-verification.yml`) are in place, and the integration test runner scripts already use the `integration_ci` marker.

Phase 5 closes the loop by making the policy visible in developer-facing docs, adding the missing PowerShell provider-verification script, and ensuring every integration test that should be CI-safe is properly marked.

## Goals

1. **Update AGENTS.md** to explicitly distinguish hermetic integration from provider verification, so agents and humans alike understand that "integration tests" no longer implies all of `tests/integration` is one default bucket.
2. **Add `tools/test-provider.ps1`** (or `tools/test-jules.ps1`) as the PowerShell counterpart to `tools/test_jules_provider.sh`.
3. **Ensure all CI-safe integration tests carry the `integration_ci` marker** so the required CI workflow actually runs them. Several integration test files are missing this marker.
4. **Ensure all live Jules/provider tests carry `provider_verification` + `jules` + `requires_credentials` markers** and are excluded from CI-safe runs.
5. **Update relevant spec/task docs** so Jules is no longer described as required compose-backed integration, while artifact validation remains explicitly required.

## Non-Goals

- No changes to test logic, assertions, or test implementation code.
- No changes to GitHub Actions workflow YAMLs (already correct from Phases 3 & 4).
- No changes to `pyproject.toml` marker definitions (already correct from Phase 1).
- No moving of test files between directories (Phase 2 already created `tests/provider/jules/`).

## In-Scope Test Files

### Must receive `integration_ci` marker (currently unmarked)

These files exercise hermetic, compose-backed, no-external-credentials scenarios and belong in required CI:

- `tests/integration/temporal/test_compose_foundation.py`
- `tests/integration/temporal/test_execution_rescheduling.py`
- `tests/integration/temporal/test_integrations_monitoring.py`
- `tests/integration/temporal/test_interventions_temporal.py`
- `tests/integration/temporal/test_live_logs_performance.py`
- `tests/integration/temporal/test_managed_runtime_live_logs.py`
- `tests/integration/temporal/test_manifest_ingest_runtime.py`
- `tests/integration/temporal/test_namespace_retention.py`
- `tests/integration/temporal/test_oauth_session.py`
- `tests/integration/temporal/test_upgrade_rehearsal.py`
- `tests/integration/services/temporal/workflows/test_agent_run.py`
- `tests/integration/workflows/temporal/test_schedule_timezone_handling.py`
- `tests/integration/workflows/temporal/test_task_5_14.py`
- `tests/integration/workflows/temporal/workflows/test_run_agent_dispatch.py`
- `tests/integration/workflows/temporal/workflows/test_run.py`
- Top-level `tests/integration/*.py` files that are compose-backed but do not need external credentials (each evaluated individually)

### Already correctly marked

- `tests/integration/temporal/test_temporal_artifact_local_dev.py` — `integration_ci` ✅
- `tests/integration/temporal/test_temporal_artifact_auth_preview.py` — `integration_ci` ✅
- `tests/integration/temporal/test_temporal_artifact_lifecycle.py` — `integration_ci` ✅
- `tests/integration/temporal/test_activity_worker_topology.py` — `integration_ci` ✅
- `tests/provider/jules/test_jules_integration.py` — `provider_verification`, `jules`, `requires_credentials` ✅

### Top-level integration tests to evaluate

These files in `tests/integration/` (not under `temporal/`) need individual assessment. Some may require external API keys (OpenRouter, OpenAI, etc.) and should be excluded from `integration_ci`:

- `test_gemini_embeddings.py`
- `test_interventions.py`
- `test_multi_profile_openrouter.py` — likely needs OpenRouter key → NOT CI-safe
- `test_ollama_embeddings.py`
- `test_openai_embeddings.py`
- `test_profile_chat_flow.py`
- `test_profile_creation_on_register.py`
- `test_projection_sync.py`
- `test_startup_profile_seeding.py`
- `test_startup_secret_env_seeding.py`
- `test_startup_task_template_seeding.py`
- `api/test_live_logs.py`

## Requirements

### R1: AGENTS.md Testing Instructions update

The "Testing Instructions" section must be updated to:
- Explicitly define **hermetic integration** as compose-backed, local-dependencies-only, no external credentials.
- Explicitly define **provider verification** as real third-party provider checks using real credentials, not required for merge.
- Reference both `./tools/test_integration.sh` (hermetic) and `./tools/test_jules_provider.sh` / `./tools/test-provider.ps1` (provider verification).
- State that Jules unit tests remain in the required unit suite.

### R2: PowerShell provider verification script

Create `tools/test-provider.ps1` that:
- Runs `pytest tests/provider/jules -m 'provider_verification and jules' -q --tb=short`
- Requires `JULES_API_KEY` environment variable (fail fast if missing)
- Mirrors the behavior of `tools/test_jules_provider.sh`

### R3: Marker coverage for integration tests

Every test file in `tests/integration/` that is hermetic (compose-backed, no external credentials) must carry the `integration_ci` marker. Every test file that requires external credentials must NOT carry `integration_ci` and should carry `provider_verification` or equivalent exclusion markers.

### R4: Spec/doc references updated

Any spec or task document that describes Jules as "required compose-backed integration" must be updated to reflect the new taxonomy. The activity-topology spec (060) and integrations-monitoring spec (061) are the most likely candidates.

## Success Criteria

1. `./tools/test_integration.sh` runs only CI-safe hermetic tests (verified by marker).
2. `./tools/test_jules_provider.sh` and `./tools/test-provider.ps1` run only Jules provider verification.
3. AGENTS.md explicitly documents the test taxonomy with clear definitions.
4. No integration test file that requires external credentials carries the `integration_ci` marker.
5. No spec or task document describes Jules live tests as required PR CI coverage.
