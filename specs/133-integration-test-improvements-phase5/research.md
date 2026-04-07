# Research: Integration Test Improvements — Phase 5

## Decisions & Rationale

### Decision 1: Which integration test files receive `integration_ci` marker

**Rationale**: The spec lists specific files to evaluate. Each file was assessed for whether it requires external credentials (OpenRouter, OpenAI, Gemini, Ollama cloud, etc.) or is purely hermetic (compose-backed, local services only).

**Findings**:
- Files under `tests/integration/temporal/` are hermetic — they exercise Temporal workflows with local compose services. All 10 unmarked temporal files should receive `integration_ci`.
- Files under `tests/integration/workflows/temporal/` are also hermetic Temporal workflow tests. All 4 unmarked files should receive `integration_ci`.
- `tests/integration/services/temporal/workflows/test_agent_run.py` is hermetic — should receive `integration_ci`.
- Top-level integration tests (`test_gemini_embeddings.py`, `test_ollama_embeddings.py`, `test_openai_embeddings.py`, `test_multi_profile_openrouter.py`) require external API keys → **NOT CI-safe** → should NOT receive `integration_ci`.
- `test_profile_chat_flow.py`, `test_profile_creation_on_register.py`, `test_projection_sync.py`, `test_startup_*.py` — these exercise local DB/API flows with compose services and no external credentials → **CI-safe** → should receive `integration_ci`.
- `test_interventions.py` — needs assessment (may hit external provider APIs depending on configuration).
- `api/test_live_logs.py` — hermetic API test against compose services → **CI-safe** → should receive `integration_ci`.

### Decision 2: PowerShell provider script structure

**Rationale**: `tools/test-provider.ps1` should be a faithful PowerShell mirror of `tools/test_jules_provider.sh` to provide parity for Windows developers. The bash script:
1. Checks for `JULES_API_KEY` (fail fast)
2. Ensures Docker Compose is available
3. Creates `.env` from template if missing
4. Creates Docker network if missing
5. Builds the pytest compose service
6. Runs `pytest tests/provider/jules -m 'provider_verification and jules' -q --tb=short -s`

The PowerShell script follows the same steps with PowerShell-native equivalents (`Write-Error`, `exit`, `Test-Path`, `docker compose`).

### Decision 3: AGENTS.md Testing Instructions update

**Rationale**: The current "Testing Instructions" section mentions integration tests and `test_jules_provider.sh` but does not explicitly define the taxonomy. The update adds:
- Definition of **hermetic integration tests** (compose-backed, no external credentials, marked with `integration_ci`)
- Definition of **provider verification tests** (real third-party providers, require credentials, marked with `provider_verification`)
- Reference to both `./tools/test_integration.sh` / `tools/test-integration.ps1` (hermetic) and `./tools/test_jules_provider.sh` / `tools/test-provider.ps1` (provider verification)
- Note that Jules unit tests remain in the required unit suite

### Decision 4: Doc updates for Jules references

**Rationale**: `ActivityCatalogAndWorkerTopology.md` and `IntegrationsMonitoringDesign.md` both reference Jules as if it were a required part of compose-backed integration testing. These should be updated to clarify that Jules is an **optional external provider** — the Temporal integration monitoring design is provider-neutral, and Jules is merely the default example because it has an existing adapter.

### Decision 5: Top-level integration test individual assessment

| File | CI-safe? | Marker |
|------|----------|--------|
| `test_gemini_embeddings.py` | NO (needs Gemini API key) | None new |
| `test_interventions.py` | Needs assessment — check if it hits external APIs | Evaluate |
| `test_multi_profile_openrouter.py` | NO (needs OpenRouter key) | None new |
| `test_ollama_embeddings.py` | NO (needs Ollama, may be local but external service) | Evaluate |
| `test_openai_embeddings.py` | NO (needs OpenAI key) | None new |
| `test_profile_chat_flow.py` | YES (local compose services) | `integration_ci` |
| `test_profile_creation_on_register.py` | YES (local compose services) | `integration_ci` |
| `test_projection_sync.py` | YES (local compose services) | `integration_ci` |
| `test_startup_profile_seeding.py` | YES (startup, local) | `integration_ci` |
| `test_startup_secret_env_seeding.py` | YES (startup, local) | `integration_ci` |
| `test_startup_task_template_seeding.py` | YES (startup, local) | `integration_ci` |
| `api/test_live_logs.py` | YES (local compose services) | `integration_ci` |

## Alternatives Considered

### Alternative: Move test files into subdirectories
Rejected — the spec explicitly says "No moving of test files between directories (Phase 2 already created `tests/provider/jules/`)."

### Alternative: Create a new marker instead of `integration_ci`
Rejected — the marker is already defined in `pyproject.toml` from Phase 1 and used in CI workflows from Phase 3. Consistency is better than renaming.

### Alternative: Embed marker in pytest.ini_options `filterwarnings` style auto-marking
Rejected — explicit `@pytest.mark.integration_ci` decorators on each file are clearer and easier to audit.
