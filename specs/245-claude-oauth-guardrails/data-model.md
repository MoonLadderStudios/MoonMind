# Data Model: Claude OAuth Authorization and Redaction Guardrails

## Entities

### Claude OAuth Session Authorization Boundary

Represents the access-control envelope around a Claude OAuth session.

Fields and state already present in repo models:
- `session_id`: durable OAuth session identifier.
- `runtime_id`: expected to be `claude_code` for this story.
- `profile_id`: usually `claude_anthropic` or another Claude OAuth profile row.
- `requested_by_user_id`: owner identity used to gate create, attach, cancel, finalize, and reconnect.
- `status`: lifecycle state such as `pending`, `starting`, `bridge_ready`, `awaiting_user`, `verifying`, `registering_profile`, `succeeded`, `failed`, `cancelled`, or `expired`.
- `expires_at`: lifetime bound used to deny expired terminal attachment.
- `metadata_json`: compact attach-token and terminal metadata.

Guardrail rules:
- Owner mismatch must fail closed before verification, profile mutation, or terminal access.
- Non-terminal reconnect/repair is only valid from expired, failed, or cancelled predecessor states.
- Operator-visible session responses may expose compact status but not raw secrets or auth-volume contents.

### OAuth Terminal Attach Token

Short-lived, single-use browser terminal credential derived from the owning session.

Stored shape:
- `terminal_attach_token_sha256`: persisted digest only.
- `terminal_attach_token_used`: boolean single-use marker.
- `terminal_attach_issued_at`: issuance timestamp.
- Query token: transient plaintext token returned once to the authorized caller.

Guardrail rules:
- Persist only the hash, never the plaintext token.
- Reject expired, missing, mismatched, or already-used tokens.
- Mark consumed on successful WebSocket attach.
- Keep token values out of API responses beyond the one-time attach response, out of metadata, and out of logs/artifacts.

### Secret-Free Observable Output

Any operator-visible data emitted by the Claude OAuth lifecycle.

Representative surfaces:
- OAuth session REST responses.
- Provider-profile REST responses and manager payloads.
- Verification results.
- Terminal bridge safe metadata.
- Launcher/activity/runtime failure summaries.
- Artifact or diagnostic metadata derived from these boundaries.

Guardrail rules:
- Redact token-like assignments, bearer strings, private keys, and auth-volume paths below the mount root.
- Preserve compact refs and non-secret runtime metadata.
- Never emit raw credential file contents, raw terminal paste input, or raw directory listings.

### Claude OAuth Credential Store Surface

The auth-volume-backed Claude home and its related metadata.

Representative fields:
- `volume_ref = claude_auth_volume`
- `volume_mount_path = /home/app/.claude`
- `MANAGED_AUTH_VOLUME_PATH`
- `CLAUDE_HOME`
- `CLAUDE_VOLUME_PATH`
- safe diagnostics such as `volumeRef` and `authMountTarget`

Guardrail rules:
- Treat the volume as credential storage only.
- Keep it separate from repo workspace roots and artifact publication roots.
- Allow non-secret mount-target references when needed for diagnostics.
- Do not expose raw auth file paths, file contents, or directory listings.

## State Transitions

### OAuth Session Lifecycle

- `pending` -> `starting` -> `bridge_ready` -> `awaiting_user`
- `awaiting_user` -> `verifying` -> `registering_profile` -> `succeeded`
- Any active state -> `cancelled`, `failed`, or `expired`
- `failed` / `cancelled` / `expired` -> reconnect/repair successor session via `POST /oauth-sessions/{id}/reconnect`

### Attach Token Lifecycle

- issued: hash stored, `used=false`
- consumed: WebSocket attach succeeds, `used=true`
- expired: session expiry denies further use
- invalid: digest mismatch or missing token state denies attach

## Validation Implications

- Route tests should assert owner mismatch returns 404/403 before side effects.
- WebSocket or bridge tests should assert single-use token consumption and replay denial.
- Provider-profile and verification tests should assert ref-only secret-free payloads.
- Launch and diagnostics tests should assert auth-volume separation from workspace/artifact roots.
