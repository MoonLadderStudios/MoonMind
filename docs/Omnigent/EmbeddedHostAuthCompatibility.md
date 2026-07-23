# Embedded host authentication compatibility

MoonMind embedded compatibility mode delegates runner authentication to the
Omnigent submodule pinned at commit
`538494ff735a93f13e6914f264abb7feca037e57`. The supported profile identifier is
`omnigent.runner_tunnel.538494ff`.

The authoritative transport is the upstream websocket runner tunnel at
`/v1/runners/{runner_id}/tunnel`. A stock runner supplies exactly one
`X-Omnigent-Runner-Tunnel-Token` handshake header and the non-browser origin
`omnigent://internal`. The server verifier is
`omnigent.server.routes.runner_tunnel._expected_runner_id_from_headers`; the
verified identity is produced by `omnigent.runner.identity.token_bound_runner_id`.
MoonMind invokes these pinned entrypoints through `OmnigentHostAuthAdapter` and
fails preflight when they cannot be imported.

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
ready until the configured proxy-conformance evidence for issue #3368 and live
host-auth conformance evidence are both present. Proxy mode remains the supported
production topology.
