# Contract: Claude OAuth Verification and Profile Registration

## Finalize Claude OAuth Session

Endpoint: `POST /api/v1/oauth-sessions/{session_id}/finalize`

Preconditions:

- The session belongs to the authenticated operator.
- The session status is `awaiting_user` or `verifying`.
- The session runtime is `claude_code`.
- The session has an auth volume ref and Claude home mount path.

Success response:

```json
{
  "status": "succeeded"
}
```

Success side effects:

- Verifies the mounted Claude home before provider profile mutation.
- Registers or updates `claude_anthropic`.
- Stores `credential_source = "oauth_volume"`.
- Stores `runtime_materialization_mode = "oauth_home"`.
- Stores `volume_ref = "claude_auth_volume"`.
- Stores `volume_mount_path = "/home/app/.claude"`.
- Syncs Provider Profile Manager for `runtime_id = "claude_code"`.
- Marks the OAuth session `succeeded`.

Failure behavior:

- Failed verification returns a 400 response and marks the session `failed`.
- Unavailable verification returns a 503 response and marks the session `failed`.
- Unauthorized access returns without verification or profile mutation.
- Invalid session state returns without verification or profile mutation.

Secret-safety requirements:

- Responses, profile rows, logs, artifacts, and workflow payloads must not contain credential file contents, token values, environment dumps, raw directory listings, or raw auth-volume entries.

## Verifier Result

Verifier: `verify_volume_credentials(runtime_id="claude_code", volume_ref, volume_mount_path="/home/app/.claude")`

Accepted proof:

- `credentials.json` present under the mounted Claude home.
- `settings.json` present under the mounted Claude home with qualifying account-auth evidence documented by the Claude runtime adapter.

Result shape:

```json
{
  "verified": true,
  "status": "verified",
  "runtime_id": "claude_code",
  "reason": "ok",
  "credentials_found_count": 1,
  "credentials_missing_count": 1
}
```

Forbidden result fields:

- Raw file contents.
- Token values.
- Environment variable values.
- Raw directory listings.
- Full credential file paths.
