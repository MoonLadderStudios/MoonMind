# Embedded host transport retirement record

**Document Class:** Canonical declarative

**Status:** Retired by MoonLadderStudios/MoonMind#3955. The experimental
embedded host/runner transport (`hostProtocolMode:
embedded_omnigent_compatible_server`) no longer admits new hosts, sessions, or
credential consumers. The only supported transport is
`upstream_omnigent_server_proxy` (compatibility profile `omnigent.server.v1`).

**Compatibility declaration (historical):** `omnigent.server.v1` with embedded
runner authentication profile `omnigent.runner_tunnel.983c93c6`, verified
against upstream Omnigent source commit
`f04b0354fb5344c1ea8b92795ceb6760a9ad7595`. This declaration describes retained
history only; it must not be used to admit new embedded work or to manufacture
a passing embedded conformance row.

## What was removed

The embedded host-facing execution path is gone: the bridge configuration
rejects `embedded_omnigent_compatible_server` and the removed
`hostConnection.embedded` settings block with actionable errors, the
transport-specific HTTP/WebSocket routes (`POST /v1/hosts/register`, `WS
/v1/hosts/{host_id}/tunnel`, `WS /v1/runners/{runner_id}/tunnel`, host
heartbeat, host/session event ingestion) answer `410 Gone` with the supported
proxy alternative instead of creating host, session, or credential consumers,
and the runner launch, channel, evidence-gate, and facade modules were deleted.
API/worker startup fails fast on a surviving embedded declaration through the
normal bridge-configuration resolution path rather than silently ignoring it or
substituting proxy mode. Errors never silently change transport.

## What was preserved

- **Native Workflow Chat presentation.** The `embedded=1` query parameter of
  the binding-scoped native chat surface (`WorkflowChatNative.tsx`) is an
  unrelated presentation option for the upstream UI inside MoonMind's
  authorized facade. It survives this retirement unchanged, as does the
  binding-scoped facade for full-page access.
- **Shared bridge/session/event/credential contracts.** First-message
  idempotency, browser-safe bindings, event persistence, and the durable bridge
  session rows are untouched. Retained sessions keep their recorded mode,
  endpoint, and cleanup owner until drained; their history remains readable
  through the existing resolve/events/artifact projections, which decode the
  recorded `embedded_omnigent_compatible_server` value without importing the
  removed launch modules.
- **Host-auth credential lifecycle.** The host-auth profile
  selection/rotation/revocation routes and their existing lifecycle remain the
  mechanism that retires credential references. Transport consumers were
  disconnected first; no shared secret or enrollment-owned volume was deleted
  as a side effect of removing a route. The legacy
  `OMNIGENT_HOST_RUNNER_TOKEN` fallback reader is inert: nothing consumes it.
- **Drain ownership.** Retained sessions and leases drain through the existing
  janitor-owned terminal-cleanup probes (`active_host_protocol_modes`,
  `embedded_reconciliation_host_lease_refs`,
  `cleanup_required_host_lease_refs`, `record_terminal_cleanup`). A configured
  mode change is still blocked with `409` while active sessions belong to
  another mode, so an in-flight session is never redirected between modes.
- **Proxy qualification.** Validators still used by proxy qualification
  (`conformance`, `exact_artifact_conformance`, `live_verification_health`)
  are unchanged. Support and readiness surfaces advertise only the surviving
  proxy topology.

## Historical reference

The retired adapter implemented stock-host registration and heartbeat at `POST
/v1/hosts/register` and `POST /v1/hosts/{host_id}/heartbeat`, the host tunnel
at `WS /v1/hosts/{host_id}/tunnel`, and the runner tunnel at `WS
/v1/runners/{runner_id}/tunnel`. Registration never created authority: the
profile/host coordinator assigned an active host lease first, and registration
bound the verified token identity, host ID, Provider Profile and OAuth
generation, host-auth profile and generation, endpoint mode, capability
inventory, and lease expiry. Heartbeats refreshed that exact lease; disconnect
marked it unavailable; rotation expiry or revocation drained it; cleanup and
reconciliation released the durable binding. A host could never claim another
profile, lease, runner, or session.

Secret bodies were resolved only immediately before a handshake verifier ran.
Rotation was an atomic settings change with at most one overlapping preceding
generation (maximum 15 minutes); revocation drained every connected tunnel and
rejected every new or reconnecting tunnel immediately. The binding token was a
runner control-plane credential, distinct from Omnigent user authentication and
MoonMind user/operator authentication. Readiness and errors exposed only safe
profile, generation, pinned-commit, and failure-code metadata.

Static Compose and on-demand Docker were separate support rows, each required
to prove the same contract before advertisement. Rollback selected
`upstream_omnigent_server_proxy` for new sessions only; in-flight sessions
stayed with their recorded endpoint and bridge mode, and historical records
retained their actual compatibility profile and evidence references.
