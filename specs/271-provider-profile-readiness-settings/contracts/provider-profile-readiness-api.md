# Contract: Provider Profile Readiness API

## Existing Endpoints

Readiness is included in existing provider profile responses:

- `GET /api/v1/provider-profiles`
- `GET /api/v1/provider-profiles/{profile_id}`
- `POST /api/v1/provider-profiles`
- `PATCH /api/v1/provider-profiles/{profile_id}`

## Response Addition

Each provider profile response includes:

```json
{
  "profile_id": "codex_default",
  "runtime_id": "codex_cli",
  "provider_id": "openai",
  "readiness": {
    "status": "ready",
    "launch_ready": true,
    "summary": "Provider profile is ready for launch.",
    "checks": [
      {
        "id": "enabled",
        "label": "Enabled state",
        "status": "pass",
        "message": "Profile is enabled."
      }
    ]
  }
}
```

## Status Semantics

- `ready`: no error or warning checks; launch-ready according to Settings-visible metadata.
- `warning`: no error checks, but at least one warning check.
- `blocked`: at least one error check; affected launches should not silently fall back.

## Security Requirements

- `message` and `summary` are sanitized.
- Readiness never includes raw credentials, API keys, OAuth state blobs, decrypted files, generated credential config, auth headers, or plaintext secret values.
- SecretRef strings may be displayed as security-relevant metadata only when the caller is authorized to view the profile.

## Non-Goals

- This response does not reserve, release, or inspect live Temporal slot leases.
- This response does not construct commands, environment variables, generated files, process launch arguments, or runtime capability checks.
