# Contract: Auth Operator Diagnostics

## OAuth Session Response

`GET /api/v1/oauth-sessions/{session_id}` returns the existing session fields plus optional `profile_summary`:

```json
{
  "session_id": "oas_abc123",
  "runtime_id": "codex_cli",
  "profile_id": "codex-oauth",
  "status": "failed",
  "expires_at": "2026-04-16T12:00:00Z",
  "terminal_session_id": "term_oas_abc123",
  "terminal_bridge_id": "br_oas_abc123",
  "session_transport": "moonmind_pty_ws",
  "failure_reason": "token=[REDACTED] in [REDACTED_AUTH_PATH]",
  "created_at": "2026-04-16T11:00:00Z",
  "profile_summary": {
    "profile_id": "codex-oauth",
    "runtime_id": "codex_cli",
    "provider_id": "openai",
    "provider_label": "OpenAI",
    "credential_source": "oauth_volume",
    "runtime_materialization_mode": "oauth_home",
    "account_label": "work account",
    "enabled": true,
    "is_default": false,
    "rate_limit_policy": "backoff"
  }
}
```

Forbidden response content:
- credential file contents
- token values
- raw auth-volume listings
- runtime-home directory contents
- environment dumps

## Managed Session Launch Metadata

`agent_runtime.launch_session` returns a `CodexManagedSessionHandle` whose metadata may include:

```json
{
  "authDiagnostics": {
    "component": "managed_session_controller",
    "readiness": "ready",
    "profileRef": "codex-oauth",
    "runtimeId": "codex_cli",
    "credentialSource": "oauth_volume",
    "runtimeMaterializationMode": "oauth_home",
    "volumeRef": "codex_auth_volume",
    "authMountTarget": "/home/app/.codex-auth",
    "codexHomePath": "/work/agent_jobs/run-1/.moonmind/codex-home"
  }
}
```

On failure, the activity error message includes a sanitized reason and owner classification:

```text
agent_runtime.launch_session failed: component=managed_session_controller reason=token=[REDACTED] in [REDACTED_AUTH_PATH]
```

Forbidden metadata content:
- credential file contents
- token values
- raw environment dumps
- raw auth-volume listings
- runtime-home directory contents
- OAuth terminal scrollback
