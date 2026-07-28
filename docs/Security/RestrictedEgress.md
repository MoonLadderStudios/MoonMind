# Restricted egress

MoonLadderStudios/MoonMind#3516 defines the production restricted-egress
boundary shared by Container Jobs, managed helper workloads, and static and
on-demand Omnigent hosts.

## Architecture and authority

The supported design is one dedicated Docker network with `internal: true` and
IPv6 disabled. A workload is attached only to that network. Its sole
internet-capable peer is a trusted, dual-homed Squid gateway. The trusted
agent-runtime worker selects the immutable egress profile, attests the live
network and gateway, and launches the workload. Public API, workflow, browser,
and host-process contracts cannot select a Docker network, gateway, route,
firewall command, extra host, device, namespace, or capability.

An internal Docker network has no host-provided external route. Proxy
environment variables provide compatibility, but are not the security
boundary: deleting or overriding them removes connectivity rather than
restoring direct egress. Workloads run without privileges or capabilities,
with `no-new-privileges`, without the Docker socket, and cannot attach a
secondary network or launch a helper outside the trusted backend. Static
Compose and on-demand hosts use the same internal network and gateway;
Container Jobs and deployment-owned helper profiles resolve `bridge` policy to
that same enforcing state. Plain Docker `bridge`, `local-network`, and local
application URL allowlists are non-enforcing development mechanisms and must
never be reported in `enforcedNetworkRefs`.

The current implementation supports the deployment-selected daemon reached
through MoonMind's restricted Docker proxy. Arbitrary remote daemon endpoints
are not profile authority: the backend must find and attest the exact
deployment-owned network and gateway on its selected daemon or startup fails.

## Profile and request controls

`moonmind.security.egress.EgressProfile` is frozen, versioned, digestible, and
rejects extra fields. It contains only destinations, TCP ports, DNS/resolution
policy, IPv6 policy, bounds, ownership, workload classes, validation state, and
a security-review reference. It cannot contain credentials or executable
commands. Names are exact/suffix proxy destinations; direct IP literals are
not accepted by name-only rules. CIDRs overlapping unspecified, private,
loopback, carrier NAT, link-local, multicast, reserved, IPv4-mapped IPv6, or
unique-local ranges are rejected.

The proxy permits only HTTPS `CONNECT` to port 443 for approved provider,
source-control, artifact, and retrieval domains. All other methods, ports, IP
literals, redirects to unapproved names, and alternate CONNECT targets fail.
Squid resolves the requested name for each connection and applies the
prohibited-address ACL after resolution, so CNAME chains, mixed answers,
rebinding to a private address, metadata services, Docker/host gateways, and
internal service DNS are denied. IPv6 is disabled on the workload network and
IPv6 destination ranges are denied at the proxy. The single narrow exception
is HTTP access from Omnigent hosts through the gateway to the `omnigent:8000`
control endpoint; no other control-plane destination is allowed.

## Lifecycle and evidence

Before readiness or a networked launch, the worker verifies:

1. the exact network exists, is internal, and has IPv6 disabled;
2. the exact gateway exists, has the expected profile and enforcer labels, is
   attached to the internal network, and has exactly one external attachment;
3. the live profile version matches the immutable profile selected by policy.

A passing attestation records the profile ref and digest, backend and enforcer
implementation, network and gateway identities, applied-rule digest,
validation result and time, bounded denial counters, and bounded diagnostics.
Only a passing attestation contributes to `enforcedNetworkRefs`. Workload
labels bind the attachment to the profile/rule digest. Stale profile versions,
partial setup, missing labels, extra gateway networks, missing cleanup state,
or an unavailable backend fail closed before workload start. Reconciliation
and cleanup remain label/ownership scoped; they never remove an unowned
network, gateway, container, or volume. Diagnostics contain bounded redacted
destination metadata, never credentials, request bodies, or packet payloads.

External PentestGPT targets remain gated. Enabling one additionally requires an
exact reviewed egress profile, approved target scope, and durable approval
evidence; the general provider profile in this document is not authority for
external penetration-test targets.
