# Contract: Claude Manual Enrollment UI Boundary

## Provider Profile Metadata Inputs

The Settings UI recognizes Claude manual enrollment from trusted provider profile metadata:

```json
{
  "runtime_id": "claude_code",
  "provider_id": "anthropic",
  "credential_source": "secret_ref",
  "runtime_materialization_mode": "api_key_env",
  "command_behavior": {
    "auth_strategy": "claude_manual_token",
    "auth_state": "not_connected",
    "auth_actions": ["connect"],
    "auth_status_label": "Claude token not connected",
    "auth_readiness": {
      "connected": false,
      "last_validated_at": null,
      "failure_reason": null,
      "backing_secret_exists": false,
      "launch_ready": false
    }
  }
}
```

## Manual Auth Submit Boundary

When the operator submits a non-empty returned token, the UI calls a Claude manual-auth endpoint for the selected profile.

```http
POST /api/v1/provider-profiles/{profile_id}/manual-auth/commit
Content-Type: application/json

{
  "token": "submitted secret value"
}
```

Expected response is secret-free:

```json
{
  "status": "ready",
  "status_label": "Claude token ready",
  "readiness": {
    "connected": true,
    "last_validated_at": "2026-04-22T08:30:00Z",
    "failure_reason": null,
    "backing_secret_exists": true,
    "launch_ready": true
  }
}
```

Failure responses may include a reason, but the UI redacts it before rendering:

```json
{
  "detail": {
    "message": "Validation failed for token ..."
  }
}
```

## UI Guarantees

- Claude manual enrollment must not call `/api/v1/oauth-sessions`.
- The drawer must not use terminal OAuth wording.
- Submitted token values must be cleared from local UI state on success, cancellation, or close.
- Failure text must be redacted before rendering.
- Readiness metadata is optional and each missing field is omitted.
