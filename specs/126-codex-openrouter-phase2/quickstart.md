# Quickstart: Codex CLI OpenRouter Phase 2

## Prerequisites

- MoonMind development environment with API service, Temporal, and frontend running
- `OPENROUTER_API_KEY` set in environment (for auto-seeded profile)
- Python test dependencies installed

## Setup

No additional setup is required. Phase 2 builds on Phase 1's already-seeded `codex_openrouter_qwen36_plus` profile.

## Running Tests

### Unit Tests (frontend form state)

```bash
cd frontend && npm run test -- --testPathPattern=ProviderProfilesManager
```

### Integration Tests

```bash
docker compose -f docker-compose.test.yaml run --rm pytest bash -lc "pytest tests/integration -q --tb=short -k openrouter"
```

### Full Test Suite

```bash
./tools/test_unit.sh
```

## Manual Verification

1. Open Mission Control → Settings → Provider Profiles
2. Click "Create Profile"
3. Fill in:
   - Profile ID: `test_openrouter_manual`
   - Runtime ID: `codex_cli`
   - Provider ID: `openrouter`
   - Command Behavior: `{"suppress_default_model_flag": true}`
   - Tags: `openrouter, qwen, test`
   - Priority: `50`
   - Clear Env Keys: (one per line) `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENROUTER_API_KEY`
4. Save and verify the profile appears in the table
5. Edit the profile and change priority to `75` — verify it persists

## Files Changed

| File | Change |
|------|--------|
| `frontend/src/components/settings/ProviderProfilesManager.tsx` | Extend form state, add advanced field inputs |
| `tests/integration/workflows/temporal/workflows/test_run_agent_dispatch.py` | Add openrouter dynamic routing tests |
| `tests/integration/services/temporal/workflows/test_agent_run.py` | Add openrouter cooldown/slot tests |
