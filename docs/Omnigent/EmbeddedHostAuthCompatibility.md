# Retained embedded transport history

**Document Class:** Canonical declarative

The experimental `embedded_omnigent_compatible_server` host transport has been
removed. New work uses `upstream_omnigent_server_proxy`, whose stock Omnigent
Server owns host and runner authentication and tunnels. The unrelated Workflow
Chat `embedded=1` presentation option remains supported.

## Recorded URLs and configuration

Enabling the removed transport fails configuration validation and names the
proxy alternative. Disabled historical configuration and recorded
`hostProtocolMode` values remain decodable; they cannot select a launcher or
create a host, credential consumer or session.

The old host registration, heartbeat, event-ingest and host-auth management URLs
return `410 Gone` with `omnigent_embedded_transport_retired` and the supported
proxy alternative. Old host and runner tunnel handshakes close with `4404` and
the same diagnostic. A retained create key cannot reattach or redispatch a runner.
No request is redirected to the current proxy using an old session identity.

## Historical reads

Shared session, first-message, event, idempotency and artifact records are
retained. The admitted operator can read bound session metadata without a live
transport. Event streams read the committed journal with durable sequence
cursors, retention-gap reporting and a final drain confirmation before terminal
closure. Workflow Chat serves captured terminal transcripts, metadata and its
scoped catalog through the existing binding authorization and identity
virtualization. Authorized diagnostic-only captures keep metadata and catalog
reads available; transcript reads report unavailable evidence. Missing, denied
or corrupt captures remain errors, without falling back to the current proxy.

Live runner resources and mutations are unavailable after transport removal.
Previously captured resource projections and artifacts remain with the existing
artifact authorization and download owners. Profile credentials, stored
host-auth metadata, host-lease records and migrations are not deleted by removal
of the executable authentication adapter.

## Drain diagnostics

`GET /embedded-transport-drain` remains a read-only diagnostic over durable
active-session, active-lease and pending-cleanup counts. It reports unknown,
failed or truncated observations explicitly. A zero count does not authorize
deleting credentials or saved data. Historical transport identifiers remain
stable for decoding even though the executable retirement-inventory row is gone.
