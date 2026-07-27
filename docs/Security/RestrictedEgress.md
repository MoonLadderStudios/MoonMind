# Restricted egress

MoonMind's supported production boundary is a deployment-owned Docker gateway
network reconciled by a privileged backend. This is the canonical design for
MoonLadderStudios/MoonMind#3516. A normal Docker `bridge`, an application URL
allowlist, and proxy environment variables are non-enforcing development
alternatives and are never reported in `enforcedNetworkRefs`.

## Authority and topology

The trusted worker selects an immutable `EgressProfile`; workflows and
containers cannot select a raw network, route, firewall command, extra host,
device, namespace, capability, or bypass flag. A privileged deployment
reconciler creates an internal workload network and a gateway namespace. The
gateway is the network's only route and owns DNS plus firewall/proxy rules.
Workloads run unprivileged with all capabilities dropped, no Docker socket, no
host namespace, and no secondary network. Static and on-demand Omnigent hosts,
Container Jobs, RAG/provider/source-control access, remediation, and managed
helpers use this same attachment contract.

The gateway resolves every DNS request itself. It validates every CNAME and all
mixed answers, rejects non-global and out-of-profile answers, and continuously
revalidates names before connection. Direct IP traffic is allowed only by an
explicit CIDR entry. Redirects and CONNECT requests are re-authorized as new
destinations. `NO_PROXY` does not create a direct route. IPv6 is either fully
filtered by the gateway or disabled according to the profile; IPv4-mapped IPv6
is normalized before evaluation.

Loopback, RFC1918/ULA, link-local, multicast, unspecified, metadata, Docker and
host gateways, Unix sockets, and MoonMind control-plane addresses are denied by
default. A profile cannot add non-global CIDRs. Raw packet, route, firewall,
device, privileged, host-network, extra-host, and secondary-network authority
is absent at the final Docker launch boundary, so child/helper containers must
be launched through the same trusted backend.

## Immutable profiles and attestation

Profiles contain destinations, ports/protocols, DNS and resolution policy,
IPv6 behavior, resource ceilings, retention, owner, version, validation state,
and security-review evidence. Pydantic rejects extra fields, credentials,
non-global CIDRs, and executable firewall input. Canonical JSON produces the
profile digest.

Before readiness, the trusted worker reconciles and inspects the exact Docker
network, generated rule digest, parse-checked gateway, and gateway attachments
through its deployment-selected daemon. This observed state is the readiness
authority; static Compose labels are configuration and are never accepted as
applied-state evidence. When a deployment supplies an attestation key, the
reconciler additionally signs profile, rule, gateway, network, backend, and
validation identities into network labels for independent inspection. Missing,
stale, or mismatched observed state fails closed and the network is omitted
from `enforcedNetworkRefs`.

The reconciler owns creation, rule replacement, crash reconciliation, counters,
and deletion of only its labelled resources. Policy rotation changes the
profile digest, making old networks immediately unattested. Launch evidence
records the profile and workload attachment; bounded gateway diagnostics record
denial counters and redacted destination metadata, never payloads or secrets.
Cleanup outcome is recorded independently so it cannot overwrite workload
success.

## Operational realization

The agent-runtime worker invokes the local-Docker reconciler before it reports
any enforced network as ready. The reconciler deterministically compiles the
selected profile into Squid DNS, destination, port, method, private-address,
IPv4-mapped-IPv6, connection, byte, and idle rules. It adopts only an observed
internal Compose network, replaces only its labelled gateway container, and
attaches that gateway to the internal network and Docker's outbound bridge.
Static Compose hosts, Container Jobs, ordinary workload profiles that request
`bridge`, and managed helpers receive the internal network and the same gateway
proxy identity. The old static restricted-egress proxy is not part of this
path.

Every launch re-inspects the running dual-homed gateway and then verifies that
the created workload has exactly one network attachment. The trusted backend
persists a bounded launch artifact containing profile, backend, network,
gateway, rule, validation, workload-attachment, and redacted denial-counter
evidence. Cleanup publishes a separate owned-container removal result.
Deployment-selected remote Docker endpoints use the same Docker API
observations and inject the non-secret generated policy into an ephemeral
in-container filesystem, so they do not depend on worker-host bind paths. If
the daemon cannot create, inspect, parse-check, or attach that exact state,
reconciliation fails.

External PentestGPT targets remain disabled by default. Enabling them requires
an exact approved target scope, manual/durable approval, a reviewed profile,
and matching enforcement evidence. External targets stay gated until
enforcement exists.
