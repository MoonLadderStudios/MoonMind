# Quickstart: Integration Test Improvements — Phase 5

## What This Changes

Phase 5 updates documentation and conventions for the test taxonomy introduced in Phases 1–4. No application code changes.

## How to Verify

### 1. Check marker coverage
```bash
# Run only CI-safe hermetic integration tests
./tools/test_integration.sh

# Should run ~15-20 test files (all marked integration_ci)
```

### 2. Check provider verification script
```bash
# PowerShell (Windows)
.\tools\test-provider.ps1

# Bash (Linux/macOS) — existing script
./tools/test_jules_provider.sh
```
Both should fail fast if `JULES_API_KEY` is not set.

### 3. Check AGENTS.md
Open `AGENTS.md` → "Testing Instructions" section should now explicitly define:
- **Hermetic integration tests** (compose-backed, no credentials, `integration_ci` marker)
- **Provider verification tests** (real providers, credentials required, `provider_verification` marker)

### 4. Check doc references
`docs/Temporal/ActivityCatalogAndWorkerTopology.md` and `docs/Temporal/IntegrationsMonitoringDesign.md` should no longer describe Jules as a "required compose-backed integration" — it is an optional external provider.

## What Not to Test

- No test logic changed — existing test assertions are untouched.
- No CI workflow YAMLs changed — Phases 3–4 already got those right.
- No `pyproject.toml` marker definitions changed — Phase 1 already registered them.
