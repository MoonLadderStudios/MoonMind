# Data Model: Finalize OAuth from Provider Terminal

## OAuth Session

Represents one provider credential enrollment or repair session.

Relevant fields:

- `session_id`: stable session identifier used by Settings and the provider terminal page.
- `runtime_id`: runtime selected when the session was created.
- `profile_id`: Provider Profile identity selected when the session was created.
- `status`: lifecycle state: `pending`, `starting`, `bridge_ready`, `awaiting_user`, `verifying`, `registering_profile`, `succeeded`, `failed`, `cancelled`, or `expired`.
- `session_transport`: transport mode such as `moonmind_pty_ws` or `none`.
- `terminal_session_id`: terminal runner session ref, present only when bridge-backed terminal attachment is available.
- `terminal_bridge_id`: terminal bridge ref, present only when bridge-backed terminal attachment is available.
- `volume_ref`: durable auth-volume reference selected when the session was created.
- `volume_mount_path`: enrollment/verification mount path selected when the session was created.
- `account_label`: safe operator-readable account label.
- `requested_by_user_id`: owner/actor identity used for authorization.
- `expires_at`: session expiry timestamp.
- `failure_reason`: sanitized failure summary.
- `metadata_json`: safe session metadata such as provider label, provider id, slot policy, and attach-token metadata.

Validation rules:

- Finalization can only operate on the session row selected by `session_id` and the authenticated actor.
- Finalization must use session-owned `profile_id`, `runtime_id`, `volume_ref`, `volume_mount_path`, provider identity, and policy metadata.
- Terminal requests must not override session-owned identity or credential-reference fields.
- Browser-visible failure summaries must be sanitized and bounded.

State transitions for this story:

```text
awaiting_user -> verifying -> registering_profile -> succeeded
awaiting_user -> verifying -> failed
verifying/registering_profile/succeeded -> idempotent finalize response
pending/starting/bridge_ready -> not eligible for finalize
cancelled/expired/failed -> safe failure or recovery action, no profile mutation
failed/cancelled/expired -> reconnect creates a new pending OAuth Session
older same-actor/profile session superseded by newer active or completed session -> safe failure, no profile mutation
```

Superseded-session rule:

- Superseded is a derived validation condition, not a stored `OAuthSessionStatus`.
- A session is superseded when it is no longer the current owner of the finalization path for the same actor and Provider Profile because a newer active or completed OAuth Session has taken precedence.
- Superseded sessions must fail finalization safely without registering or mutating a Provider Profile.

## Provider Profile

Represents the durable managed runtime credential profile registered or updated after OAuth verification.

Relevant fields:

- `profile_id`: profile identity selected by the OAuth Session.
- `owner_user_id`: optional profile owner; when present, must match the authenticated actor for mutation.
- `runtime_id`: runtime supported by the profile.
- `provider_id`: provider identifier from session metadata or runtime default.
- `provider_label`: safe display label.
- `credential_source`: expected `oauth_volume` for OAuth-backed profiles.
- `runtime_materialization_mode`: expected `oauth_home` for OAuth-backed runtime homes.
- `volume_ref`: durable auth-volume reference from the OAuth Session.
- `volume_mount_path`: enrollment/verification path from the OAuth Session.
- `account_label`: safe operator-readable account label.
- `max_parallel_runs`, `cooldown_after_429_seconds`, `rate_limit_policy`: profile slot and backoff policy.
- `enabled`: profile is enabled after successful OAuth finalization.

Validation rules:

- Profile registration or update happens only after durable auth verification succeeds.
- Duplicate finalize requests for the same OAuth Session must not create a second Provider Profile or mutate a different `profile_id`.
- The safe registered-profile summary may include identifiers, labels, credential source, materialization mode, enabled/default state, and rate-limit policy, but not credential contents.

## Finalization Result

Observable result returned to Settings and the terminal page after a finalize request or session refresh.

Fields:

- `session_id`
- `runtime_id`
- `profile_id`
- `status`
- `expires_at`
- `session_transport`
- `terminal_session_id`
- `terminal_bridge_id`
- `failure_reason`
- `profile_summary`

Validation rules:

- `profile_summary` appears only when the actor is allowed to see the selected profile summary.
- `failure_reason` is sanitized before display.
- Secret-like credential contents, auth-volume listings, environment dumps, and token values are never included.

## Terminal Completion View State

Derived frontend state for the provider terminal page.

Fields:

- `session`: latest safe OAuth Session projection.
- `terminalStatus`: terminal attach/connect status.
- `finalizePending`: whether a finalize request is in flight.
- `actionError`: sanitized UI error message.
- `queryRefreshSent`: whether provider-profile views have been invalidated or notified after success.

Allowed actions:

- Attach terminal: only when status and terminal refs are attachable.
- Finalize Provider Profile: only when the session is eligible for verification/finalization.
- Cancel: only when the current session state permits cancellation.
- Retry/Reconnect: only for failed, cancelled, or expired sessions when reconnect is supported.
- Return to Settings or manage profile: convenience only after success.
