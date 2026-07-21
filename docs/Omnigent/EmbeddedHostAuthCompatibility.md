# Embedded host authentication compatibility

MoonMind embedded compatibility mode delegates runner authentication to the
Omnigent submodule pinned at commit
`7da32637a5eeba1c47431fe21fca948ced9b779e`. The supported profile identifier is
`omnigent.runner_tunnel.7da32637`.

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
of 15 minutes from `rotatedAt`. Existing tunnels may finish until disconnected;
new and reconnecting tunnels reauthenticate against the current bounded set.
Expired generations are stale and rejected. Revocation disables every
generation immediately for new requests and reconnects; operators drain or
close existing tunnels before re-enabling a newly validated generation. Invalid
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

The pinned upstream allow-list rejects missing, empty, and unauthorized tokens.
The token-derived runner identifier prevents one credential from claiming a
runner bound to another credential. Reconnect with the same generation produces
the same identity. Revoking a token from the server-side allow-list prevents new
connections; existing websocket behavior remains owned by the upstream tunnel
implementation. Upstream HTTP rejection is 403 before websocket acceptance for
an invalid binding and protocol failures close the accepted socket according to
the upstream tunnel route.

Embedded mode remains experimental and must not be presented as production
ready until the configured proxy-conformance evidence for issue #3368 and live
host-auth conformance evidence are both present. Proxy mode remains the supported
production topology.
