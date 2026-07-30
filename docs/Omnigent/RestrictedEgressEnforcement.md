# Restricted Egress Enforcement

Canonical design for MoonMind's policy-selected restricted-egress substrate
(MoonLadderStudios/MoonMind#3516). It limits outbound traffic from Omnigent
hosts and generic workloads to explicitly approved destinations, resists common
bypass paths, publishes durable attestation evidence, and **fails closed** when
enforcement cannot be proven.

> A declared or reflected Docker network is **not** proof of restricted egress.
> Docker `bridge` alone is not an enforced egress boundary. Only a validated,
> immutable egress profile whose compiled rule set passes conformance and whose
> enforcing network state the trusted backend confirms is attestable.

## Trust and privilege boundary

- **Trusted backend** (the worker / container-job backend) is the only component
  that talks to the Docker/host endpoint, creates enforcing network state,
  applies rules, and signs attestations. It owns every side effect.
- **Untrusted** components — the browser, the workflow payload, and the host
  agent process — carry *references only* (an `egressProfileRef` and a policy
  ref). They never receive authority to select raw Docker networks, firewall
  commands, host routes, capabilities, or bypass flags. `validate_launch_constraints`
  (`moonmind/omnigent/egress_enforcement.py`) rejects any launch spec that asks
  for `network_mode=host/bridge/none/container`, `privileged`, `NET_ADMIN`/
  `NET_RAW`/`SYS_ADMIN` capabilities, secondary networks, `extra_hosts`, added
  routes, `sysctls`, or device passthrough.

## Enforcement architecture (production)

The supported production design is an **egress gateway + firewall chains** that
force all container traffic through a policy-aware proxy:

1. Workloads attach only to a dedicated, internal Docker network with no default
   route to the internet.
2. A trusted egress gateway (proxy + `iptables`/`nftables` chains, owned by the
   backend, not reachable for reconfiguration from the workload) is the only
   path off that network. It applies the compiled rule set: default-deny, then
   the profile's allow rules, with implicit denials for loopback, link-local
   (including `169.254.169.254` metadata), private/RFC1918, Docker
   bridge/host-gateway, multicast, and MoonMind control-plane ranges.
3. IPv6 is disabled on the network unless the profile's `ipv6Policy` is
   `allow_listed`; IPv4-mapped IPv6 destinations are normalized to their
   embedded IPv4 before evaluation so a `::ffff:169.254.169.254` cannot slip
   through.
4. DNS resolution goes through the profile's declared resolvers only
   (`resolverPolicy=profile_dns_only`); answers are re-validated against the
   allow-list on every connection (`resolution=revalidate`) to defeat DNS
   rebinding and time-of-check/time-of-use changes. `pin` mode uses an
   out-of-band pinned IP set.
5. Redirects and `CONNECT`/proxy methods do not bypass the check: the
   *effective* destination is always re-evaluated, and only approved
   `proxyEndpoints` may be used.

### Local-development alternative (explicitly non-enforcing)

Plain Docker `bridge` and any backend that reports `enforcing=False` are
**classified as non-enforcing**. They never yield an attested network ref;
`enforcedNetworkRefs` stays empty and external targets stay gated. This keeps
local-first deployment simple without pretending bridge is a boundary.

### Remote Docker daemons

A remote Docker daemon is supported only when the backend can create and own the
enforcing network/gateway on that daemon. If it cannot, the backend declares the
profile non-enforcing and fails closed rather than attaching to an unenforced
network.

## Versioned egress profiles

`moonmind/omnigent/egress_profiles.py` defines the immutable `EgressProfile`:
allowed DNS names / IP-CIDRs, ports/protocols, resolution mode, approved proxy
endpoints, IPv6 policy, resolver policy, connection/rate/byte/idle limits,
logging & retention bounds, owner/version/digest, validation state, security
review evidence, permitted workload classes, and policy references.

Profiles are content-addressed (`digest`) and immutable: any change requires a
version bump, so stale backend state keyed on an old digest never satisfies a
new profile version. Profiles carry **no credentials and no executable firewall
commands** — a validator rejects shell/firewall metacharacters and secret-like
keys. Private/loopback/link-local/metadata/Docker/control-plane ranges are
denied implicitly and cannot be opened from the allow-list unless a profile
narrowly opts in via `allowInternalRanges` with a recorded justification (and
even then loopback and metadata stay blackholed).

Built-in catalog: `egress-omnigent-baseline@1` (provider APIs + source control
over TLS) and `egress-deny-all@1`.

## Realization and attestation at the trusted backend

At worker startup (`worker_runtime.py`) the backend, for each enforced policy:

1. resolves the referenced immutable egress profile and validates it;
2. compiles it into a deterministic default-deny rule set with an
   `appliedRuleDigest`;
3. runs the negative conformance probe (`run_conformance`) — allowed
   destinations pass, representative bypass/forbidden destinations fail at the
   rule layer;
4. confirms the enforcing network is live (`network_ready`);
5. emits an `EgressAttestation` evidence record.

Only an **attested** result contributes its network ref to `enforcedNetworkRefs`
(evidence-backed readiness, replacing the previous declarative-only flag). Every
launch persists a bounded, redacted evidence record: profile id/version/digest,
backend + enforcer version, network identity, applied-rule digest, validation
result and time, attachment identity, denied-connection counters, cleanup state,
and security-review ref. Full traffic payloads, credentials, and unbounded
packet logs are never persisted.

Reconciliation removes only backend-owned network/gateway resources on cleanup;
partial setup is reconciled before readiness.

## Bypass classes covered by the conformance suite

DNS rebinding / TOCTOU, CNAME/mixed answers, direct-IP use when only hostnames
are allowed, IPv6 and IPv4-mapped IPv6, redirects from approved to unapproved
destinations, HTTP `CONNECT`/proxy methods and env proxy bypass, loopback /
host-gateway / Docker-gateway / metadata / internal-service ranges,
container-added routes / extra hosts / privilege escalation / secondary network
attachment, child/helper containers (they inherit the same enforced network),
and reuse of stale network/gateway state after a policy version change (defeated
by digest binding). See `tests/unit/omnigent/test_egress_enforcement.py`.

## Integration

The same generic capability backs static and on-demand Omnigent hosts, Container
Jobs and managed helper containers, RAG-gateway access, provider/source-control
endpoints, and remediation/diagnostics helpers via the shared `workloadClasses`
on each profile. Any retained PentestGPT external-target work stays disabled
(`PentestSettings.allow_external_targets` defaults to `False`) until a reviewed
profile and attested enforcement exist.
