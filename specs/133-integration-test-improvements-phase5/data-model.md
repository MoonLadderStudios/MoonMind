# Data Model: Integration Test Improvements — Phase 5

No new data model changes. This feature concerns documentation, test markers, and tool scripts only.

## Existing Marker Definitions (from Phase 1, `pyproject.toml`)

| Marker | Purpose | Test Scope |
|--------|---------|------------|
| `integration` | Marks integration tests (broad) | All tests under `tests/integration/` |
| `integration_ci` | Marks hermetic integration tests safe for required CI | Compose-backed, no external credentials |
| `provider_verification` | Marks live external-provider verification tests | Real third-party providers |
| `jules` | Marks Jules-provider-specific tests | `tests/provider/jules/` |
| `requires_credentials` | Marks tests needing real credentials | Any provider test |

## Test File Taxonomy (Post-Phase-5)

| Category | Location | Markers | CI Required? |
|----------|----------|---------|-------------|
| Hermetic integration | `tests/integration/temporal/` | `integration`, `integration_ci` | Yes |
| Hermetic integration | `tests/integration/workflows/temporal/` | `integration`, `integration_ci` | Yes |
| Hermetic integration | `tests/integration/services/temporal/` | `integration`, `integration_ci` | Yes |
| Hermetic integration | `tests/integration/test_profile_*.py`, `test_projection_sync.py`, `test_startup_*.py` | `integration`, `integration_ci` | Yes |
| Hermetic integration | `tests/integration/api/test_live_logs.py` | `integration`, `integration_ci` | Yes |
| Provider verification | `tests/provider/jules/` | `provider_verification`, `jules`, `requires_credentials` | No (separate workflow) |
| External API (not CI-safe) | `tests/integration/test_*_embeddings.py`, `test_multi_profile_openrouter.py` | `integration` only | No |
