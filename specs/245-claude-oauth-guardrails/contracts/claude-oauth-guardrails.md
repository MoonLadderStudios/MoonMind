# Contract: Claude OAuth Authorization and Redaction Guardrails

## Covered Surfaces

Boundary surfaces in scope for MM-482:
- `POST /api/v1/oauth-sessions`
- `POST /api/v1/oauth-sessions/{session_id}/cancel`
- `POST /api/v1/oauth-sessions/{session_id}/terminal/attach`
- `GET /api/v1/oauth-sessions/{session_id}`
- `POST /api/v1/oauth-sessions/{session_id}/finalize`
- `POST /api/v1/oauth-sessions/{session_id}/reconnect`
- `POST /api/v1/provider-profiles/{profile_id}/oauth/validate`
- `POST /api/v1/provider-profiles/{profile_id}/oauth/disconnect`
- OAuth terminal WebSocket attach and frame handling
- Claude runtime launch/session diagnostics for OAuth-home profiles

## Authorization Contract

Required behavior:
- Only the owning authorized operator may create or use a Claude OAuth session tied to a non-shared profile.
- Unauthorized operators cannot attach, cancel, finalize, or reconnect a Claude OAuth session.
- Unauthorized access must fail before verification, provider-profile mutation, or terminal bridging occurs.

Minimum proof points:
- Owner mismatch on finalize does not call the verifier or create/update a profile.
- Owner mismatch on reconnect does not create a successor session.
- Owner mismatch on terminal attach does not issue an attach token.

## Attach Token Contract

Required behavior:
- Attach response returns a one-time plaintext token only to the authorized caller.
- Session metadata stores only `terminal_attach_token_sha256`, issuance timestamp, and usage state.
- WebSocket attach accepts only a non-expired matching token whose `used` marker is still false.
- Successful WebSocket attach flips `terminal_attach_token_used` to true.
- Replay of the same token is denied.

Forbidden behavior:
- Persisting plaintext token values.
- Returning the stored hash to the caller.
- Reusing the same token across multiple WebSocket attachments.

## Redaction Contract

Allowed operator-visible data:
- compact session state
- compact provider profile summary
- non-secret diagnostics such as `profileRef`, `runtimeId`, `volumeRef`, and `authMountTarget`
- bounded terminal metadata counts from `safe_metadata()`

Forbidden operator-visible data:
- raw token or authorization-code values
- raw credential file contents
- bearer token strings
- raw auth-volume file paths below the mount root
- raw auth-volume directory listings
- durable storage of pasted terminal credentials

## Credential-Store Boundary Contract

Required behavior:
- `claude_auth_volume` / `/home/app/.claude` is treated as credential storage only.
- Launch, validation, and diagnostics may reference the mount target safely, but not as workspace or artifact content roots.
- Workspace cwd and artifact publication roots remain distinct from `MANAGED_AUTH_VOLUME_PATH`.

## Unit-Test Contract

Focused MM-482 unit coverage must prove:
- owner-scoped authorization for Claude OAuth routes, including reconnect-as-repair
- one-time hashed attach-token issuance and replay denial
- secret-free OAuth session failure reasons and provider-profile payloads
- secret-free Claude verification / profile / diagnostic payloads
- auth-volume separation from workspace and artifact-backed paths

## Integration-Test Contingency

Run hermetic integration tests only if implementation changes:
- artifact publication behavior,
- worker topology or WebSocket/session wiring,
- or another compose-backed seam that unit tests cannot fully prove.
