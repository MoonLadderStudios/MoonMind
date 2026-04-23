# Contract: Claude Manual Auth API

## Commit Claude Manual Auth

`POST /api/v1/provider-profiles/{profile_id}/manual-auth/commit`

Commits a returned Claude Anthropic token for an existing provider profile that the current caller is allowed to manage.

### Path Parameters

- `profile_id`: Provider profile id. The target profile must represent Claude Code with the Anthropic provider.

### Request Body

```json
{
  "token": "sk-ant-...",
  "account_label": "Claude Anthropic"
}
```

Fields:
- `token` is required and secret-bearing.
- `account_label` is optional and non-secret.

### Success Response

```json
{
  "status": "ready",
  "status_label": "Claude token ready",
  "profile_id": "claude_anthropic",
  "secret_ref": "db://claude-anthropic-token",
  "readiness": {
    "connected": true,
    "last_validated_at": "2026-04-22T00:00:00+00:00",
    "backing_secret_exists": true,
    "launch_ready": true,
    "failure_reason": null
  }
}
```

Contract rules:
- The response must not contain the submitted token.
- The response may contain a secret reference, because it is not token material.
- `readiness` must contain only secret-free metadata.

### Profile Mutation Contract

After a successful commit, fetching the profile must show:

```json
{
  "credential_source": "secret_ref",
  "runtime_materialization_mode": "api_key_env",
  "volume_ref": null,
  "volume_mount_path": null,
  "secret_refs": {
    "anthropic_api_key": "db://claude-anthropic-token"
  },
  "clear_env_keys": [
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "OPENAI_API_KEY"
  ],
  "env_template": {
    "ANTHROPIC_API_KEY": {
      "from_secret_ref": "anthropic_api_key"
    }
  },
  "command_behavior": {
    "auth_strategy": "claude_manual_token",
    "auth_state": "connected",
    "auth_actions": ["replace_token", "validate", "disconnect"],
    "auth_status_label": "Claude token ready",
    "auth_readiness": {
      "connected": true,
      "backing_secret_exists": true,
      "launch_ready": true
    }
  }
}
```

Contract rules:
- The fetched profile must not contain the submitted token.
- Runtime-visible profile manager payloads must use the same secret-reference shape.
- Existing volume-backed launch fields must be cleared.

### Failure Responses

Malformed, unauthorized, unsupported-profile, or upstream validation failures must return a non-success HTTP response with secret-free detail text.

Example:

```json
{
  "detail": "Claude token validation failed."
}
```

Contract rules:
- Failure responses must not include the submitted token.
- Malformed token failures must not create or update the managed secret for a ready binding.
- Unsupported profile failures must not mutate unrelated provider profiles.

## Runtime Secret Resolution

The runtime secret resolver must resolve `db://<slug>` references by Managed Secret slug so profile materialization can inject the stored Anthropic credential through the profile binding.

Contract rules:
- `db://` resolution returns plaintext only at the secret resolver boundary.
- Invalid or unsupported secret reference schemes are ignored or rejected according to existing resolver policy.
- The resolved plaintext must not be included in profile payloads or route responses.
