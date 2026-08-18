# Embedded host authentication compatibility

**Compatibility declaration:** `omnigent.server.v1` with embedded runner
authentication profile `omnigent.runner_tunnel.983c93c6`, verified against
upstream Omnigent source commit
`8aaf72c91a53ed4791766716b43c9bdfcfadae99`.

This is an Omnigent-compatible adapter boundary, not a MoonMind host protocol.
Only an unchanged stock host speaking the declared profiles may connect.
Unknown protocol or authentication profiles fail closed.

MoonMind embedded compatibility mode delegates runner authentication to the
Omnigent submodule pinned at commit
`8aaf72c91a53ed4791766716b43c9bdfcfadae99`. The supported protocol profile
identifier remains `omnigent.runner_tunnel.983c93c6`; the source-pin boundary
test proves that this profile's verifier entrypoints remain available at the
new revision.

The authoritative transport is the upstream websocket runner tunnel at
`/v1/runners/{runner_id}/tunnel`. A stock runner supplies exactly one
`X-Omnigent-Runner-Tunnel-Token` handshake header and the non-browser origin
`omnigent://internal`. The server verifier is
`omnigent.server.routes.runner_tunnel._expected_runner_id_from_headers`; the
verified identity is produced by `omnigent.runner.identity.token_bound_runner_id`.
MoonMind invokes these pinned entrypoints through `OmnigentHostAuthAdapter` and
fails preflight when they cannot be imported.

## Implemented surface and lifecycle

The embedded adapter implements stock-host registration and heartbeat at
`POST /v1/hosts/register` and `POST /v1/hosts/{host_id}/heartbeat`, the host
tunnel at `WS /v1/hosts/{host_id}/tunnel`, and the runner tunnel at
`WS /v1/runners/{runner_id}/tunnel`. Its session boundary implements
`POST /v1/sessions`, `GET /v1/sessions/{session_id}`,
`POST /v1/sessions/{session_id}/attach`,
`DELETE /v1/sessions/{session_id}`,
`POST /v1/sessions/{session_id}/events`,
`GET /v1/sessions/{session_id}/stream`, and the configured resource routes for
changes, files, diffs, session files, and child sessions. Supported controls
are stop, interrupt, elicitation resolution, and terminal harvest through
those declared session routes.

Registration does not create authority. The profile/host coordinator must
first assign an active host lease. Registration binds the verified token
identity, host ID, Provider Profile and OAuth generation, host-auth profile and
generation, endpoint mode, capability inventory, and lease expiry. Heartbeats
refresh that exact lease. Disconnect marks it unavailable; rotation expiry or
revocation drains it; cleanup and reconciliation release the durable binding.
A host cannot claim another profile, lease, runner, or session.

The adapter preserves raw upstream event evidence for diagnosis and publishes
normalized MoonMind projections separately. First-message state is durable and
idempotent. Stream reconnect uses the recorded event cursor and epoch. Unknown
execution-critical events, authority-bearing fields, protocol versions, or
control semantics fail closed. Optional unknown resource events may be retained
as diagnostic evidence without granting authority.

Unsupported upstream capabilities include arbitrary host-defined authority,
unlisted control verbs, alternate authentication mechanisms, browser/user
credential forwarding, and implicit proxy/embedded substitution. Requests
receive an explicit unsupported, authentication, stale-generation, or
conformance-gated error; they are never silently accepted.

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

Issuance and storage are owned by MoonMind's host-auth profile settings and
SecretRef services. Validation is owned by the pinned upstream verifier
adapter. Rotation, revocation, expiry, denial, and stale-generation use emit
bounded audit metadata containing profile, generation, outcome code, and pinned
version only. Secret bodies must never enter Temporal payloads, bridge rows,
artifacts, checkpoints, workspaces, or logs.

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
ready until all three configured evidence claims (proxy conformance, live
stock-host smoke, and host-auth conformance) resolve independently, pass schema
and secret-scan validation, match the active MoonMind build/configuration and
immutable image identities, and remain unexpired. Proxy mode remains the
production default and supported topology until the full #3519 matrix passes.

## Launch modes, rollout, and rollback

Static Compose and on-demand Docker are separate support rows. Each must prove
the same registration, authentication, session, reconnect, resource, terminal,
and cleanup contract before it can be advertised for embedded mode. An
unproven row is unavailable with an actionable readiness reason; success in one
mode does not imply support for the other.

Selecting embedded mode is explicit and versioned. Missing, stale, revoked, or
incompatible evidence gates readiness before a new session starts. Rollback
selects `upstream_omnigent_server_proxy` for new sessions only. An in-flight
session stays with its recorded endpoint and bridge mode, and historical
records retain their actual compatibility profile and evidence references.
MoonMind never redirects an in-flight session between modes.

## Evidence, upgrade, and support diagnostics

The credentialed conformance run must use digest-pinned stock server and host
images, record architecture, upstream commit, protocol/auth profiles, MoonMind
build and configuration digest, host mode, capability inventory, auth
generation, per-case outcome, timestamps, and independently resolvable
secret-scanned artifact references. Semantic fakes and caller-supplied expected
event lists are test aids, not support evidence.

Every upstream upgrade requires a new conformance pass for every advertised
host mode and architecture. Promotion pins the new verified identities and
retains the preceding verified image/profile as the rollback target. Fixture
coverage for older supported event and auth shapes remains until that support
row is removed. Unverified upgrades fail readiness with the mismatched identity
and a proxy/previous-version rollback recommendation.

Readiness, the Workflow Detail projection, host lifecycle diagnostics, the
support matrix, and release metadata expose the actual bridge mode,
compatibility and auth profiles, immutable image digests and architecture,
host-auth generation, evidence freshness/reference status, lifecycle state,
capability summary, bounded failure reason, and rollback recommendation. These
surfaces contain no credential bodies.
