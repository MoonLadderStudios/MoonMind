# Quickstart: Auth-Profile and Rate-Limit Controls (081)

## Local Validation Commands

```bash
# Run unit tests for this feature
./tools/test_unit.sh tests/unit/workflows/adapters/test_managed_agent_adapter.py
./tools/test_unit.sh tests/unit/workflows/temporal/test_auth_profile_activity.py

# Run all unit tests (ensure no regressions)
./tools/test_unit.sh
```

## Manual Smoke Test

1. Start the local stack: `docker compose up -d`
2. Create an auth profile via the API:
   ```bash
   curl -X POST http://localhost:8888/api/auth-profiles \
     -H "Content-Type: application/json" \
     -d '{"profile_id": "test-gemini-oauth", "runtime_id": "gemini_cli", "auth_mode": "oauth", "volume_ref": "/mnt/gemini-oauth", "max_parallel_runs": 1}'
   ```
3. Submit a managed agent task and confirm:
   - Temporal shows signal round-trip with `AuthProfileManager`
   - No credential values appear in Temporal workflow history
   - OAuth-mode run has API-key vars cleared in process env
