# Restricted egress

**Document Class:** Canonical declarative
**Viewpoint:** Cross-Cutting Concept
**Last updated:** 2026-09-16

MoonLadderStudios/MoonMind#3516 defines the production restricted-egress
boundary shared by Container Jobs, managed helper workloads, and static and
on-demand Omnigent hosts.

## Architecture and authority

The supported design uses deployment-owned Docker networks with `internal:
true` and IPv6 disabled. A workload is attached only to the network selected by
its immutable profile. Its sole internet-capable peer is one trusted Squid
gateway with exactly one external attachment. Provider workloads and Omnigent
hosts use separate internal networks and separate proxy listeners. The
Omnigent listener is bound only to an alias on the Omnigent network, so generic
Container Jobs and helpers have no route to the narrow control-plane exception.
The trusted agent-runtime worker selects the immutable egress profile, attests
the live network and gateway, and launches the workload. Public API, workflow,
browser, and host-process contracts cannot select a Docker network, gateway,
route, firewall command, extra host, device, namespace, or capability.

An internal Docker network has no host-provided external route. Proxy
environment variables provide compatibility, but are not the security
boundary: deleting or overriding them removes connectivity rather than
restoring direct egress. Workloads run without privileges or capabilities,
with `no-new-privileges`, without the Docker socket, and cannot attach a
secondary network or launch a helper outside the trusted backend. Static
Compose and on-demand hosts use the same Omnigent profile, internal network,
isolated listener, and gateway. Deployment-owned runner profiles select an
explicit network policy: `restricted_egress` uses the provider profile,
`docker_proxy` reaches only the trusted Docker proxy. Plain Docker `bridge`, the
Compose-managed `control-plane-network`, and local application URL
allowlists are non-enforcing development mechanisms and must never be reported
in `enforcedNetworkRefs`.

The current implementation supports the deployment-selected daemon reached
through MoonMind's restricted Docker proxy. Arbitrary remote daemon endpoints
are not profile authority: the backend must find and attest the exact
deployment-owned network and gateway on its selected daemon or startup fails.

The ordinary application control plane uses the Compose key
`control-plane-network`. Compose creates and owns it with the stable Docker-level
name resolved from `MOONMIND_CONTROL_PLANE_NETWORK` (default
`moonmind_control-plane-network`). Trusted workers pass that same resolved name
to standalone managed sessions and gateway attestation. It remains a routable
development/application network, not an enforced egress boundary.

## Profile and request controls

`moonmind.security.egress.EgressProfile` is frozen, versioned, digestible, and
rejects extra fields. It contains only destinations, TCP ports, DNS/resolution
policy, IPv6 policy, bounds, ownership, workload classes, validation state, and
a security-review reference. It cannot contain credentials or executable
commands. Names are exact/suffix proxy destinations; direct IP literals are
not accepted by name-only rules. CIDRs overlapping unspecified, private,
loopback, carrier NAT, link-local, multicast, reserved, IPv4-mapped IPv6, or
unique-local ranges are rejected.

The Omnigent-only extra provider destinations come from one deployment-owned
data file, `docker/sandbox-egress-proxy/omnigent-provider-domains.txt`, shared
by the backend and the proxy. It lists one exact lowercase public DNS name per
line; IP literals, wildcards, single-label or reserved-suffix names, ports,
URLs, and whitespace variants are rejected, and each entry is approved only as
`dnsName` on TCP/443. Adding a future public provider host is an edit to that
file alone: no Python change and no digest recalculation, because the backend
derives the loaded destinations and the combined config digest from the file
and the proxy reads the same bytes at startup through `policy.sh`, which
validates, snapshots, and pins the policy before Squid parses it. Approving a
changed set still requires running the conformance suite in
`tests/integration/security/test_restricted_egress_live.py` before the new
destinations take effect for real workloads.

The gateway uses the managed MoonMind image, which bundles Squid and the
executable policy under `/opt/moonmind-egress`; checkout mounts cannot obscure
those files. Only provider data is read from the deployment mount at
`/app/docker/sandbox-egress-proxy` (or `MOONMIND_EGRESS_POLICY_DIRECTORY` when
configured consistently for the backend and gateway). An absent provider file
means no extra destinations, exactly like an empty file, preserving existing
installations during image-only updates. It does not enable the image's
OpenRouter example implicitly. The main Squid policy is always image-owned.
The updater does not rewrite the provider file, so explicit future destinations
survive image changes and restarts.

During the v1-to-v2 transition, candidate startup accepts a healthy v1 gateway
only with its reviewed profile-set label, config label, and live main-config
hash intact, and only when every loaded extra destination is already covered
by the v1 suffix allowlist. The attestation records the actual v1 enforcer and
config digest, not v2 evidence. OpenRouter and other new destinations require
v2: first promote the image with the installed destinations unchanged, then
edit the provider file and restart the gateway and its consumers. Remove this
transition once the minimum supported upgrade source ships v2 and no retained
v1 gateway remains; until then it permits qualification before the release
controller's normal service-recreation phase without weakening integrity.

The proxy permits only HTTPS `CONNECT` to port 443 for approved provider,
source-control, artifact, and retrieval domains. All other methods, ports, IP
literals, redirects to unapproved names, and alternate CONNECT targets fail.
Squid resolves through Docker's embedded DNS for each connection and applies
the prohibited-address ACL after resolution, so Compose service discovery works
without weakening CNAME, mixed-answer, rebinding, metadata, Docker/host gateway,
or internal-service protections. IPv6 is disabled on workload networks and the
gateway denies the entire IPv6 destination space on its external hop. The
reviewed 300-second peer read timeout bounds idle CONNECT tunnels and is part of
the attested profile/config digests. Both policy files are attestable together
through one combined digest; a tampered mounted or startup-pinned copy fails
closed even when the main config still matches. The Omnigent-only listener permits narrow
HTTP control-plane exceptions to `omnigent:8000` and to `api:8000` for the
container Tool call, execution creation, and child execution description paths.
Execution routes require both the fan-out version marker and a bearer header;
the API then verifies the workflow-scoped capability and child relationship.
No other workload network can reach this listener, and all other MoonMind API
paths, methods, and control-plane destinations are denied. The transport route
is always available to compatible hosts but grants no authority without the
operation-specific credential selected by normalized `requiredCapabilities`.

## Lifecycle and evidence

Before readiness or a networked launch, the worker verifies:

1. the exact network exists, is internal, and has IPv6 disabled;
2. the exact gateway exists, carries the enforcer implementation label, is
   attached to every declared internal network, and has exactly one
   external attachment;
3. the live mounted and startup-pinned proxy policy files (main config and the
   deployment-owned provider list) match the backend's combined config digest;
4. the live profile version matches the immutable profile selected by policy.

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
Terminal Container Job diagnostics correlate the workload attachment with the
gateway access log and persist only a hashed attachment address, denial count,
and at most 20 normalized denied authorities. After removal, a separate
terminal lifecycle artifact observes that no job-owned attachment remains,
records successful cleanup and reconciliation, and correlates itself to the
runtime diagnostics ref. Terminal workflow and projection cleanup evidence
points at this post-removal artifact; pre-cleanup diagnostics do not claim a
cleanup outcome. The production-shaped local
conformance suite is `tests/integration/security/test_restricted_egress_live.py`;
operators run it against the supported Compose topology with
`MOONMIND_RUN_EGRESS_CONFORMANCE=1` before approving an external-target profile.
The opt-in `tools/test_control_plane_network_lifecycle.sh` check starts an
isolated `moonmind-test-*` project with no pre-existing control-plane network,
proves standalone managed-session access to `http://api:8000`, observes worker
egress attestations, verifies normal `compose down`, and exercises restart and
cleanup while an interrupted owned container temporarily keeps the network
attached. Run it only with the normal deployment stopped and
`MOONMIND_RUN_CONTROL_PLANE_NETWORK_CONFORMANCE=1`.
