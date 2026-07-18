# Embedded host authentication compatibility

> **Traceability:** GitHub issue `MoonLadderStudios/MoonMind#3369`.

MoonMind embedded compatibility mode delegates runner authentication to the
Omnigent submodule pinned at commit
`b95e41eca8b52723b0c154cfda6f06e681eba447`. The supported profile identifier is
`omnigent.runner_tunnel.b95e41ec`.

The authoritative transport is the upstream websocket runner tunnel at
`/v1/runners/{runner_id}/tunnel`. A stock runner supplies exactly one
`X-Omnigent-Runner-Tunnel-Token` handshake header and the non-browser origin
`omnigent://internal`. The server verifier is
`omnigent.server.routes.runner_tunnel._expected_runner_id_from_headers`; the
verified identity is produced by `omnigent.runner.identity.token_bound_runner_id`.
MoonMind invokes these pinned entrypoints through `OmnigentHostAuthAdapter` and
fails preflight when they cannot be imported.

The binding token is a runner control-plane credential, distinct from Omnigent
user authentication and MoonMind user/operator authentication. Authorization
Bearer values, cookies, query/path values, execution-principal headers, and
workflow payload values are not runner credentials. A successful verification
returns only a token-derived runner identifier and the profile version; raw
headers and credential values are not retained in the auth context.

The pinned upstream allow-list rejects missing, empty, and unauthorized tokens.
The token-derived runner identifier prevents one credential from claiming a
runner bound to another credential. The adapter accepts ephemeral credential
generations resolved at a service boundary; auth contexts retain the runner id,
profile, and matched positive generation, never the ref or value. The current API
service resolves generation 1 from `env://OMNIGENT_HOST_RUNNER_TOKEN`; managed
database secret-ref selection remains unavailable and therefore embedded mode
must remain experimental. Reconnect with the same generation produces the same
identity. A future service resolver may temporarily present the upstream
allow-list with the current and immediately previous resolved generations.
Removing or marking a generation revoked prevents new connections with it.
Existing accepted websocket connections are not re-authenticated by this adapter
and must be disconnected by the connection owner when deterministic immediate
revocation is required.

Missing configuration and protocol drift require operator remediation
(`host_auth_not_configured` and `host_auth_protocol_drift`). Missing,
duplicate, invalid, expired, revoked, and stale credentials are permanent request
failures (`host_credential_malformed` or `host_credential_rejected`) and must not
reconnect forever. Upstream HTTP rejection is 403 before websocket acceptance for
an invalid binding and protocol failures close the accepted socket according to
the upstream tunnel route. The MoonMind HTTP facade maps credential failures to
401 and unavailable verifier/configuration failures to 503 without reflecting
headers, cookies, secret refs, credential values, or decoded verifier payloads.

Embedded mode remains experimental and must not be presented as production
ready until the configured proxy-conformance evidence for issue #3368 and live
host-auth conformance evidence are both present. Proxy mode remains the supported
production topology.
