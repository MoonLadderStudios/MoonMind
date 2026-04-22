# Contract: Provider Profile Credential Actions

## Surface

The Settings Provider Profiles table exposes row actions derived from each provider profile.

## Claude Anthropic Row Contract

For `profile_id = "claude_anthropic"`, `runtime_id = "claude_code"`, and `provider_id = "anthropic"`:

- `Connect with Claude OAuth` starts `POST /api/v1/oauth-sessions` with the selected profile payload.
- `Use Anthropic API key` opens the Managed Secrets-backed API-key enrollment flow and submits through the existing provider-profile manual-auth commit path.
- `Validate OAuth` is rendered only when trusted metadata includes `validate_oauth`.
- `Disconnect OAuth` is rendered only when trusted metadata includes `disconnect_oauth`.

Supported action metadata:

```json
{
  "command_behavior": {
    "auth_strategy": "claude_credential_methods",
    "auth_actions": [
      "connect_oauth",
      "use_api_key",
      "validate_oauth",
      "disconnect_oauth"
    ]
  }
}
```

The canonical OAuth profile shape may also imply `connect_oauth` and `use_api_key` for `claude_anthropic`:

```json
{
  "profile_id": "claude_anthropic",
  "runtime_id": "claude_code",
  "provider_id": "anthropic",
  "credential_source": "oauth_volume",
  "runtime_materialization_mode": "oauth_home",
  "volume_ref": "claude_auth_volume"
}
```

## Request Expectations

### OAuth Method

`Connect with Claude OAuth` calls:

```http
POST /api/v1/oauth-sessions
Content-Type: application/json
```

The JSON body includes:

- `runtime_id: "claude_code"`
- `profile_id: "claude_anthropic"`
- `volume_ref` when present
- `volume_mount_path` when present
- provider and rate-limit metadata already used by existing OAuth session creation

### API-Key Method

`Use Anthropic API key` does not call `/api/v1/oauth-sessions`. The existing API-key enrollment drawer submits:

```http
POST /api/v1/provider-profiles/{profile_id}/manual-auth/commit
Content-Type: application/json
```

The backend stores the Anthropic key in Managed Secrets and updates profile materialization for `ANTHROPIC_API_KEY`.

## Guardrails

- Claude OAuth labels must not reuse Codex-specific `Auth`.
- API-key enrollment must not be described as terminal OAuth.
- Unsupported rows must not show Claude credential-method actions.
- Raw secrets must not be rendered in action labels, status, notices, or errors.
