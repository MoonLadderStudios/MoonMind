# Quickstart: Register OAuth-backed Codex Provider Profiles

1. Start a Codex OAuth session through `/api/v1/oauth-sessions` with `runtime_id=codex_cli`, a `profile_id`, and an `account_label`.
2. If volume details are omitted, verify the row uses `codex_auth_volume` and `/home/app/.codex`.
3. Complete credential enrollment into the durable auth volume.
4. Finalize the session.
5. Verify the resulting Provider Profile has:
   - `runtime_id=codex_cli`
   - `provider_id=openai`
   - `credential_source=oauth_volume`
   - `runtime_materialization_mode=oauth_home`
   - `volume_ref=codex_auth_volume`
   - `volume_mount_path=/home/app/.codex`

Validation:

```bash
python -m pytest tests/unit/auth/test_volume_verifiers.py tests/unit/auth/test_oauth_session_activities.py tests/unit/api_service/api/routers/test_oauth_sessions.py -q
./tools/test_unit.sh
```

