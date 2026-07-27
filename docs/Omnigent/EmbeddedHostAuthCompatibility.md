# Embedded host authentication compatibility

MoonMind embedded compatibility mode delegates runner authentication to the
Omnigent submodule pinned at commit
`983c93c6ec00fd0ec75a6eb3f12f3e3fc7d4b315`. The supported profile identifier is
`omnigent.runner_tunnel.983c93c6`; the supported upstream host frame protocol
major is `1`. Both values are exact allow-list entries. A different profile,
commit, or frame major is incompatible until a new compatibility profile has
passed conformance and been explicitly selected.

The authoritative transport is the upstream websocket runner tunnel at
`/v1/runners/{runner_id}/tunnel`. A stock runner supplies exactly one
`X-Omnigent-Runner-Tunnel-Token` handshake header and the non-browser origin
`omnigent://internal`. The server verifier is
`omnigent.server.routes.runner_tunnel._expected_runner_id_from_headers`; the
verified identity is produced by `omnigent.runner.identity.token_bound_runner_id`.
MoonMind invokes these pinned entrypoints through `OmnigentHostAuthAdapter` and
fails preflight when they cannot be imported.

## Implemented compatibility surface

Embedded mode is an Omnigent-compatible adapter boundary, not a MoonMind host
protocol. The selected profile implements only these stock-host routes:

| Purpose | Route | Authority and failure behavior |
| --- | --- | --- |
| Register host and advertise inventory | `POST /v1/hosts/register` | The token-bound host identity, profile, generation, Provider Profile authorization, and lease must agree. |
| Exchange host control frames | `WS /v1/hosts/{host_id}/tunnel` | Accepts upstream frame protocol major `1`; wrong direction, unknown frame semantics, oversized frames, and other majors close with `4400`. |
| Exchange runner frames | `WS /v1/runners/{runner_id}/tunnel` | Requires the active session-bound runner identity and derived binding token; an unbound or misbound runner closes with `4401`. |
| Renew the host lease | `POST /v1/hosts/{host_id}/heartbeat` | The authenticated identity must equal the path identity and retain active profile and lease authority. |
| Ingest session events | `POST /v1/hosts/{host_id}/sessions/{session_id}/events` | Host, session, profile, credential generation, and Provider Profile binding are revalidated before ingestion. |

The MoonMind-facing session, event, control, and resource routes are the
versioned `omnigent.server.v1` facade declared in
[Omnigent Bridge](OmnigentBridge.md#41-public-session-api-surface). They retain
workflow-owner authorization and first-message idempotency even when the
embedded host channel supplies the runtime events.

Supported host command/result frames are the pinned upstream launch, stop,
stat, directory, worktree, directory-creation, harness-installation, readiness,
and runner-exit shapes loaded by `OmnigentHostProtocolAdapter`. MoonMind does
not locally reinterpret unknown fields or invent extension frames. Unsupported
approval or elicitation operations, optional resource operations, reset/epoch
semantics, and controls that the selected profile does not declare fail
explicitly with `omnigent_embedded_control_unsupported` or
`omnigent_bridge_capability_unavailable`; they are never treated as successful
no-ops.

Registration creates or renews the durable host lease for the exact
token-derived identity. Heartbeats renew only that lease. Session dispatch
binds one runner identity to the authorized host, Provider Profile generation,
bridge session, and host-auth generation. Disconnect, credential expiry,
revocation, terminal session cleanup, or policy withdrawal drains that
authority; reconnect succeeds only after the complete binding is validated
again. Static and on-demand deployment modes do not share host leases or
credential authority.

## Managed credential lifecycle

Embedded deployments configure a stable host-auth profile ID, a current
generation, and an `env://` or `db://` SecretRef. Secret bodies are resolved
only immediately before the HTTP or WebSocket handshake verifier runs. The
legacy `OMNIGENT_HOST_RUNNER_TOKEN` is retained solely as an explicit
local/bootstrap fallback and readiness reports that fallback state; it is not
the managed production contract.

Rotation is an atomic settings change. A new SecretRef and strictly increasing
generation become current together. One immediately preceding generation may
remain valid for reconnects until its explicit expiry, with a maximum overlap
of 15 minutes from `rotatedAt`. New and reconnecting tunnels authenticate
against the current bounded set, and connected tunnels revalidate that set
before every accepted frame. Expiry drains a tunnel authenticated with the old
generation; revocation drains every connected tunnel and rejects every new or
reconnecting tunnel immediately. Operators reconnect with a newly validated
generation after rotation or revocation. Invalid
profile, verifier, SecretRef, or overlap configuration fails readiness without
replacing the last valid settings, which supplies rollback through the settings
transaction rather than silent credential fallback.

The verified token-bound identity and selected host-auth generation must match
an active durable host lease. That lease is revalidated against its exact host
binding, Provider Profile credential generation, and assigned bridge session
on each accepted operation. Readiness and errors expose only safe profile,
generation, pinned-commit, and failure-code metadata.

The binding token is a runner control-plane credential, distinct from Omnigent
user authentication and MoonMind user/operator authentication. Authorization
Bearer values, cookies, query/path values, execution-principal headers, and
workflow payload values are not runner credentials. A successful verification
returns only a token-derived runner identifier and the profile version; raw
headers and credential values are not retained in the auth context.

The pinned upstream allow-list rejects missing, empty, duplicate, and unauthorized tokens.
The token-derived runner identifier prevents one credential from claiming a
runner bound to another credential. Reconnect with the same generation produces
the same identity. MoonMind's lifecycle layer additionally revalidates profile
and generation metadata for connected tunnels so rotation expiry and revocation
have deterministic drain semantics. Upstream HTTP rejection is 403 before
websocket acceptance for an invalid binding; MoonMind maps handshake rejection
to close code 4401, disabled/revoked or stale connected authority to 4403,
transient verifier/configuration failure to 1013, and accepted-frame protocol
failure to 4400.

Embedded mode remains experimental and must not be presented as production
ready until the configured proxy-conformance evidence for issue #3368 and the
complete issue #3519 live stock-host matrix are both present, fresh,
schema-valid, independently resolvable, secret-scanned, and bound to the exact
server image, host image, architecture, protocol profile, auth profile, and
credential generation selected for new runs. Proxy mode remains the supported
production topology and default.

Promotion never changes the bridge mode of an existing session. Rollback
selects proxy mode only for new runs; historical sessions retain their actual
mode and compatibility identity. An upstream upgrade requires a new profile
and passing matrix while the previous passing immutable compatibility identity
remains available and tested as the rollback target. Missing, stale,
incompatible, or unresolved evidence fails readiness and does not trigger a
silent proxy/embedded substitution.
