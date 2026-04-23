# Quickstart: Claude OAuth Session Backend

1. Run focused provider registry tests:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/auth/test_oauth_provider_registry.py
```

2. Run focused OAuth session activity and terminal runner tests:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/auth/test_oauth_session_activities.py tests/unit/services/temporal/runtime/test_terminal_bridge.py
```

3. Run focused API/seed tests:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api_service/api/routers/test_oauth_sessions.py tests/unit/api_service/test_provider_profile_auto_seed.py
```

4. Verify behavior:

- `claude_code` registry defaults resolve `moonmind_pty_ws`, `claude_auth_volume`, `/home/app/.claude`, Anthropic provider metadata, `claude login`, and `claude_config_exists`.
- `POST /api/v1/oauth-sessions` for `claude_anthropic` stores `session_transport = moonmind_pty_ws`.
- Claude auth-runner startup includes `HOME`, `CLAUDE_HOME`, `CLAUDE_VOLUME_PATH`, empty `ANTHROPIC_API_KEY`, empty `CLAUDE_API_KEY`, and the Claude auth volume mount.
- Existing Codex OAuth runner tests still pass.

5. Run final required unit verification:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```
