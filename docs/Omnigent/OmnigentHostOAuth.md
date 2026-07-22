# Omnigent Host OAuth

**Document Class:** Canonical declarative  
**Status:** Current  
**Owners:** MoonMind Platform  
**Last updated:** 2026-07-18  
**Authority:** Provider Profile, credential-mount, host-binding, host-lease, readiness, and cleanup contract for OAuth-backed Omnigent hosts

Implementation progress belongs in the roadmap, issues, and pull requests. This document defines the durable desired state and the safety invariants that all launch modes must enforce.

## Related documents

- [`docs/Omnigent/CodexCreateToHostContract.md`](./CodexCreateToHostContract.md)
- [`docs/Security/ProviderProfiles.md`](../Security/ProviderProfiles.md)
- [`docs/Security/SecretsSystem.md`](../Security/SecretsSystem.md)
- [`docs/ManagedAgents/OAuthTerminal.md`](../ManagedAgents/OAuthTerminal.md)
- [`docs/Omnigent/OmnigentAdapter.md`](./OmnigentAdapter.md)
- [`docs/Omnigent/OmnigentBridge.md`](./OmnigentBridge.md)
- [`docs/Omnigent/CombinedStackValidationAndRollback.md`](./CombinedStackValidationAndRollback.md)
- [`docs/Omnigent/ConformanceAndLiveSmoke.md`](./ConformanceAndLiveSmoke.md)
- [`docs/Temporal/ManagedAndExternalAgentExecutionModel.md`](../Temporal/ManagedAndExternalAgentExecutionModel.md)
- [`docs/Workflows/WorkspaceLocators.md`](../Workflows/WorkspaceLocators.md)
- [`docs/Workflows/CheckpointBranchSystem.md`](../Workflows/CheckpointBranchSystem.md)

---

## 1. Purpose

MoonMind Settings enrolls first-party CLI OAuth credentials into durable, provider-specific Docker auth volumes and registers connected Provider Profiles. A profile-bound Omnigent host reuses that verified credential home without a second login ceremony and without extracting access or refresh tokens.

The Codex-first operator journey is:

```text
Settings -> Connect Codex OAuth
  -> verified Codex OAuth volume
  -> connected OpenAI / codex_cli Provider Profile
  -> executionProfileRef selected for an Omnigent run
  -> one global Provider Profile lease
  -> one durable Omnigent host lease
  -> static Compose or deterministic on-demand Codex host
  -> exact codex-native host registration and preflight
  -> one Omnigent session / runner
  -> evidence harvest and host cleanup
  -> Provider Profile lease release last
```

The current critical path is Codex. The canonical Compose file also contains a supported static `omnigent-host-claude` profile, but dynamic Claude routing, on-demand launch, bridge parity, and productized recovery remain deferred to the late Claude milestone. No Codex milestone may silently claim Claude parity.

---

## 2. Scope

This document governs:

- MoonMind Settings enrollment and repair of CLI OAuth profiles;
- the global one-consumer invariant for mutable OAuth homes;
- safe `AuthVolumeRef` and `CredentialMountRef` contracts;
- profile-to-host binding and durable host leases;
- the canonical static Compose bootstrap path;
- deterministic on-demand Codex host materialization;
- exact host registration, harness, and credential readiness;
- workspace, skill, tool, artifact, cache, temporary-storage, and network boundaries;
- retry, reconnect, generation drain, janitor, and cleanup ordering;
- secret-safe lifecycle evidence and checkpoint references.

It does not define a token broker, raw-token export, arbitrary workflow-authored Docker mounts, generic multi-profile hosts, or a custom fork of `omnigent-host`.

API-key profiles may eventually use the same binding and host-launch framework, but their secret materialization contract remains owned by Provider Profiles and the Secrets System.

---

## 3. Governing decisions

1. **MoonMind Settings is the enrollment authority.** OAuth connection, validation, repair, reconnect, and disconnect use the existing OAuth Session and Provider Profile systems.
2. **The Provider Profile is the selection identity.** Workflows use `executionProfileRef`; they never select credential files or Docker volume names.
3. **The OAuth home is mutable credential state.** The selected CLI may refresh or rewrite it, so ownership must be exclusive and authorized writes must persist.
4. **OAuth capacity is one globally.** Direct Codex, Omnigent Codex, OAuth maintenance, validation, repair, reconnect, and disconnect share the same purpose-aware Provider Profile capacity ledger.
5. **One profile maps to at most one active host and one active session.** Host-local counters do not replace Provider Profile authority.
6. **Host registration credentials are separate from provider OAuth.** The host authenticates to Omnigent independently from Codex authenticating to OpenAI.
7. **Only safe references cross durable boundaries.** Temporal history, bridge rows, checkpoints, diagnostics, and artifacts never contain OAuth credential bodies.
8. **Static and on-demand modes use one contract.** Both resolve the same profile, mount path, binding, generation, readiness, bridge authorization, artifact, and cleanup semantics.
9. **Stock host compatibility is preserved.** MoonMind configures and controls an unchanged published `omnigent-host` image.
10. **Policy fails closed.** Missing capacity, incompatible profile, stale generation, unresolved workspace, unsupported network posture, or failed preflight never falls back to a different credential or broader launch mode.

---

## 4. Why OAuth concurrency is fixed at one

CLI OAuth homes can contain access tokens, refresh tokens, account metadata, locks, caches, and format-version state that the CLI updates over time. Two processes can race while refreshing, replacing, or migrating the same files even when the upstream provider accepts multiple active access tokens.

The invariant is credential-wide:

```text
active direct Codex consumers
+ active Omnigent Codex host consumers
+ active credential-maintenance consumers
<= 1
```

Required enforcement points are:

| Boundary | Required behavior |
| --- | --- |
| OAuth Session start/finalize | Acquire credential-maintenance authority and preserve `max_parallel_runs = 1` |
| Provider Profile API | Reject an OAuth profile configured above one |
| Settings UI | Display OAuth capacity as fixed rather than editable |
| Provider Profile Manager | Share one purpose-aware ledger across every consumer type |
| Omnigent host binding | Permit at most one active host for the profile |
| Omnigent session launch | Permit at most one active session on that host |
| Reconnect/disconnect | Drain or explicitly terminate active consumers before mutation |
| Generation reconciliation | Prevent stale hosts from reusing replaced credential state |

Concurrency greater than one requires a separate provider-specific design proving safe refresh ownership. It is not an operator-tunable default.

---

## 5. Canonical Codex Provider Profile

A first-party Codex OAuth profile has this effective shape:

```yaml
profileId: codex_openai_oauth
runtimeId: codex_cli
providerId: openai
credentialSource: oauth_volume
runtimeMaterializationMode: oauth_home
volumeRef: codex_auth_volume
volumeMountPath: /home/app/.codex
credentialGeneration: 7
maxParallelRuns: 1
enabled: true
authState: connected
```

Profile ids and volume names may be deployment-specific, but runtime, provider, credential source, materialization mode, volume, mount path, and generation must agree. A Claude profile, API-key profile, disabled profile, disconnected profile, or profile with a noncanonical mount fails before host mutation.

Provider Profile readiness remains authoritative. A host binding does not make an otherwise invalid profile launchable.

---

## 6. Safe credential references

### 6.1 `AuthVolumeRef`

```yaml
providerProfileId: codex_openai_oauth
runtimeId: codex_cli
providerId: openai
volumeRef: codex_auth_volume
credentialGeneration: 7
ownerUserId: user_123
```

`AuthVolumeRef` identifies the approved mutable backing store. It contains no credential body.

`credentialGeneration` changes after successful credential replacement or reconnect. A host or binding created for an older generation is stale and cannot be assigned until reconciled.

### 6.2 `CredentialMountRef`

```yaml
authVolumeRef:
  providerProfileId: codex_openai_oauth
  runtimeId: codex_cli
  providerId: openai
  volumeRef: codex_auth_volume
  credentialGeneration: 7
  ownerUserId: user_123
targetPath: /home/app/.codex
accessMode: read_write
runtimeUid: 1000
runtimeGid: 1000
```

The canonical OAuth home is mounted read/write only into the trusted OAuth enrollment/repair runner or the exclusive profile-bound runtime host. Read/write is required because Codex may refresh or migrate credential state.

A read-only seed or copy-on-start design is valid only when a separate contract owns refresh, atomic writeback, conflict handling, and generation replacement. Refreshed state must never be silently discarded.

---

## 7. Durable host binding and lease

### 7.1 `OmnigentOAuthHostBinding`

```yaml
bindingRef: omnigent-oauth:codex_openai_oauth
providerProfileId: codex_openai_oauth
endpointRef: default
harness: codex-native
credentialMountRef: <validated CredentialMountRef>
maxHosts: 1
maxSessionsPerHost: 1
staticHostId: null
hostLaunchProfileRef: codex-on-demand
```

`staticHostId` and `hostLaunchProfileRef` are mutually exclusive. The binding is durable policy materialization for one profile; it is not a second Provider Profile.

A static binding may learn and retain the exact registered host id. An on-demand binding records the approved launch profile/policy reference and resolves the per-lease host id after registration.

### 7.2 `OmnigentHostLease`

```yaml
leaseId: ohl_<deterministic-digest>
providerProfileId: codex_openai_oauth
providerLeaseId: provider_lease_123
bindingRef: omnigent-oauth:codex_openai_oauth
credentialGeneration: 7
containerName: mm-omnigent-host-ohl...
omnigentHostId: host_123
omnigentSessionId: session_123
bridgeSessionId: bridge_123
status: assigned
acquiredAt: 2026-07-18T12:00:00Z
lastHeartbeatAt: 2026-07-18T12:01:00Z
expiresAt: 2026-07-18T13:30:00Z
```

The host lease is deterministic from provider-lease identity and idempotent for the logical run. Its lifecycle is:

```text
allocating -> starting -> ready -> assigned -> draining -> stopped
                                      \-> failed
```

Durable rows contain identifiers, timestamps, bounded status, and safe evidence only.

---

## 8. Launch-mode contract

### 8.1 Static Compose bootstrap

The canonical `docker-compose.yaml` defines `omnigent-host-codex` behind the matching Compose profile. Supported startup uses `COMPOSE_PROFILES` or an explicit `--profile` flag; superseded OAuth-host overlay files are not part of the supported path.

Static mode is appropriate for local/bootstrap operation. It uses the same Provider Profile lease and host lease as on-demand mode, and MoonMind must still:

- resolve the exact registered host rather than a generic picker;
- validate the current credential generation;
- validate `codex-native` capability and login state;
- allow only one assigned session;
- bind bridge authorization before session creation;
- stop or drain the credential consumer before releasing the profile lease.

The static host is not an unrestricted shared Omnigent host.

### 8.2 Deterministic on-demand Docker

On-demand mode starts a lease-owned container only after profile capacity is acquired and durable authorization is reserved. The container name, labels, state volume, and workspace key are deterministic from safe run/lease identity.

The host uses the published image selection and explicit image/tag overrides, runs as UID/GID `1000:1000` from `/home/app`, attaches to the configured MoonMind/Omnigent network, and registers with the selected Omnigent endpoint.

A retry inspects the deterministic container and lease before creating anything. It removes a stopped same-lease container only as part of retry-safe replacement and never removes an unrelated container.

### 8.3 Product selection authority

The durable desired state uses an explicit host mode compiled from the selected Omnigent agent profile and policy. Static or on-demand selection must be visible, validated, and stamped onto run evidence.

`OMNIGENT_CODEX_HOST_LAUNCH_PROFILE` is a bootstrap compatibility input while the first-class policy/profile surface is incomplete. It may seed a binding when no durable selection exists, but it is not workflow-authored authority and must not require manual `hostId` handling.

### 8.4 Image selection authority

The canonical deployment supports:

```text
OMNIGENT_IMAGE_REF
OMNIGENT_HOST_IMAGE_REF
```

These complete references are preferred for production and required by credentialed conformance when immutability is part of the selected profile. They may contain digest-pinned published stock images. Legacy `OMNIGENT_IMAGE` plus `OMNIGENT_IMAGE_TAG`, and `OMNIGENT_HOST_IMAGE` plus `OMNIGENT_HOST_IMAGE_TAG`, remain bootstrap-compatible fallbacks.

A policy that requires immutable stock-image evidence fails before launch when only a mutable tag is available. The effective image reference and host architecture are recorded as safe conformance evidence.

---

## 9. Host filesystem and mount policy

A profile-bound Codex host receives distinct state classes:

| State | Static target | On-demand target | Ownership and lifecycle |
| --- | --- | --- | --- |
| Provider OAuth home | `/home/app/.codex` | `/home/app/.codex` | Canonical profile volume, exclusive read/write, generation-checked |
| Omnigent identity/state | `/home/app/.omnigent` | `/home/app/.omnigent` | Separate from OAuth; static host-specific or on-demand lease-owned |
| Workflow workspace | policy-resolved under `/workspaces` | `/workspaces/run` | Workflow-scoped, never durable as a raw host path |
| Resolved Skill snapshot | `/opt/moonmind-skills` | `/opt/moonmind-skills` | Immutable and read-only |
| Versioned CLI tools | `/opt/moonmind-tools` | `/opt/moonmind-tools` | Pinned, checksum-validated, read-only |
| Runtime scripts | `/opt/moonmind` | `/opt/moonmind` | Trusted, read-only |
| Temporary storage | bounded deployment path | bounded `/tmp` tmpfs | Removable and non-authoritative |
| Artifact handoff | gateway or declared path | gateway or declared path | Separate from OAuth and host state |
| Optional caches | explicit policy | explicit policy | Named owner, scope, retention, and invalidation |

On-demand hosts use a read-only root filesystem and bounded temporary storage.
The current static Compose host does not provide equivalent filesystem
containment: its root filesystem is writable and its shared `/workspaces` mount
is writable. Treat static mode as a less-isolated operator-selected deployment
until its Compose configuration supplies the same protections; do not use
static-host evidence to claim conformance with the on-demand containment
boundary.

The workspace source is resolved from canonical workflow authority. Durable payloads use `WorkspaceLocator`; only the owning worker resolves it and translates it to a daemon-visible bind source after containment and identity validation.

The current private hashed workspace materialization is an implementation compatibility path, not a second durable workspace model. Convergence with shared `WorkspaceLocator`, daemon-visible resolution, artifact handoff, and cache primitives must preserve the separate long-lived host/session lease.

---

## 10. Runtime environment

The Codex host uses:

```text
HOME=/home/app
CODEX_HOME=/home/app/.codex
CODEX_CONFIG_HOME=/home/app/.codex
CODEX_CONFIG_PATH=/home/app/.codex/config.toml
CODEX_VOLUME_PATH=/home/app/.codex
```

Provider-profile materialization is authoritative. Competing credentials and custom-provider overrides are removed before launch, including at least:

```text
OPENAI_API_KEY
CODEX_ACCESS_TOKEN
OPENAI_BASE_URL
ANTHROPIC_API_KEY
ANTHROPIC_AUTH_TOKEN
CLAUDE_API_KEY
CLAUDE_CODE_OAUTH_TOKEN
GEMINI_API_KEY
GOOGLE_API_KEY
```

Required mounted tools and resolved Skill snapshots are validated before the corresponding execution capability is claimed. GitHub credentials, when required, are resolved independently at a trusted boundary and injected only into an isolated on-demand run-dedicated host and its authoritative runner environment. A reusable static OAuth host does not receive per-run GitHub mutation credentials.

---

## 11. Network and resource policy

The host attaches only to the network selected by the effective launch policy. The selected network must provide required MoonMind/Omnigent reachability. Network attachment and egress enforcement are distinct: Docker `bridge` or a Compose network name does not prove restricted egress.

The effective launch snapshot governs:

- image and immutable/pinned verification requirements;
- network attachment and any enforced egress profile;
- CPU, memory, process, file-descriptor, and temporary-storage limits;
- read-only root and writable-path exceptions;
- host and session timeout/lease duration;
- workspace, artifact, tool, skill, cache, and credential mounts;
- capture, cleanup, and retention behavior.

If the worker cannot realize the selected policy, launch fails before a less-constrained host is assigned. Provider Profile capacity remains authoritative even when machine or Docker capacity is also exhausted.

---

## 12. Readiness contract

A host is assignable only after the exact host environment proves:

1. the Provider Profile is enabled, connected, and launch-ready;
2. the Provider Profile lease is held for the correct purpose and owner;
3. the binding and mount refs match the profile and generation;
4. the credential volume exists at `/home/app/.codex` with expected ownership and write access;
5. competing credential variables are absent;
6. `codex login status`, or the canonical registered verifier, reports authenticated state;
7. required Skill and mounted-tool projections are valid;
8. exactly one expected online host is registered;
9. that host advertises `codex-native`;
10. bridge/server authentication and reachability are valid;
11. durable bridge authorization contains profile, provider-lease, generation, binding, and host-lease refs;
12. session creation targets that exact host.

Readiness evidence is reduced to safe structured metadata. Raw credential files, unredacted command output, environment dumps, and tokens are never persisted.

---

## 13. Session launch and authorization

Before the first message, MoonMind:

1. reserves the bridge attempt envelope;
2. acquires the shared Provider Profile lease;
3. creates or reattaches the host lease;
4. persists profile authorization before host/session side effects can become ambiguous;
5. prepares and resolves the exact host;
6. updates authorization with the exact host id;
7. creates or reattaches the Omnigent session on that host;
8. persists session identity and first-message digest state;
9. posts the first message at most once.

The coordinator injects the exact host target immediately before session creation. A workflow or generic Omnigent UI cannot bypass the profile lease by selecting the profile-bound host directly.

---

## 14. Lifecycle evidence

The bridge records bounded lifecycle events for:

```text
request validation
profile resolution and readiness
profile lease wait and acquisition
host binding and host lease creation
container start
credential mount and preflight
host registration and harness readiness
bridge authentication
session creation and first-message post
session running and resource harvest
host cleanup
profile lease release
terminal outcome
```

Every lifecycle boundary records an explicit start followed by a bounded completed or failed state. Failure is attributed to the boundary that actually reports it; for example, credential-generation and mount failures are not mislabeled as generic container failures. Workflow Detail projects these records even when the run fails before a provider stream emits any event.

Safe fields include profile, runtime, provider, credential source, volume ref, expected target path, credential generation, provider-lease, binding, host-lease, policy, workspace-locator, container, host, bridge-session, Omnigent-session, artifact, timestamp, status, and bounded redacted error refs.

Workflow Detail uses these events to explain failures even when no normal Omnigent stream starts.

---

## 15. Cleanup and janitor semantics

Terminal cleanup occurs after available session evidence is harvested:

1. interrupt or stop the active session as required;
2. collect final event, snapshot, resource, and diagnostic artifacts;
3. transition the host lease to draining;
4. stop the credential consumer;
5. for on-demand mode, remove the deterministic container and only its lease-owned Omnigent state volume;
6. for static mode, stop or drain the dedicated Codex host according to policy;
7. persist terminal host/session and cleanup evidence;
8. release the host lease;
9. release the Provider Profile lease last.

If host cleanup fails, the Provider Profile lease remains held or explicitly marked for janitor reconciliation. MoonMind does not report successful release while a credential consumer may still be active.

The janitor reconciles expired leases, missing containers, orphan labeled containers, stale credential generations, and force-drain requests. It uses durable bindings and labels and never removes unrelated containers, unrelated state volumes, the canonical OAuth volume, or application data.

---

## 16. Reconnect and credential-generation drain

Reconnect, repair, and disconnect mutate or invalidate the OAuth home. They require credential-maintenance authority from the same global capacity ledger.

After successful reconnect:

- the Provider Profile generation increments;
- the durable host binding refreshes its safe mount ref;
- active or reusable hosts on the old generation become stale;
- stale hosts are drained before new assignment;
- retries and checkpoints compare the recorded generation with the current profile before reattach.

A stale generation never silently upgrades in place while a session is active. Cold restore reacquires the current generation and creates new host/session authority from validated artifact evidence.

---

## 17. Checkpoint relationship

A checkpoint may safely reference:

```text
providerProfileId
providerLeaseRef
credentialGeneration
omnigentEndpointRef
hostBindingRef
hostLeaseRef
omnigentHostId
bridgeSessionId
omnigentSessionId
idempotencyKey
firstMessageDigest or sent marker
workspaceLocator
externalStateRef
workspace, diff, terminal, diagnostics, and capture artifact refs
```

It must not contain OAuth files, token values, Docker volume credentials, or daemon-visible absolute paths.

Live reattach is permitted only when the profile lease, generation, host registration, session, bridge authorization, and first-message evidence remain valid. Otherwise MoonMind performs evidence-gated cold restore on a new host lease. Branching obtains independent authority and never concurrently reuses the original OAuth lease.

---

## 18. Static Claude compatibility boundary

The canonical Compose file may expose `omnigent-host-claude` as a dedicated static host that mounts only the matching Claude OAuth home and keeps Omnigent state separate. That supported bootstrap slice does not imply the Codex profile-bound coordinator, dynamic on-demand runtime, mounted-tool authorization, recovery path, or conformance matrix already supports Claude.

Claude parity must reuse the same global capacity, binding, generation, exact-host, bridge, workspace, policy, evidence, and cleanup contracts after its runtime-specific credential and configuration rules are reconciled.

---

## 19. Credentialed conformance

`tools/run_omnigent_live_conformance.py` is the credentialed entrypoint for the versioned live matrix. It requires immutable server and host references and an already-enrolled OAuth profile. An operator-provisioned action adapter performs the real live actions; the repository semantic backend is test infrastructure and is not accepted as implicit provider evidence.

Live action results carry durable `evidenceRefs`. Each referenced JSON document is schema-versioned, names its scenario and action, records observed behavior and returned durable identifiers, and is independently resolved and secret-scanned. Missing, opaque, mismatched, malformed, or bare-boolean evidence fails the scenario.

The live runner uses the isolated `moonmind-test-omnigent-live` Compose project. Cleanup always attempts to remove that project's containers and networks and intentionally does not remove volumes, preserving enrolled OAuth and unrelated deployment state. Static restart/replay, published stock-image proxy compatibility, on-demand lifecycle, and failure-path scenarios remain independently gateable.

---

## 20. Security invariants

- One OAuth profile has one active credential consumer globally.
- One profile-bound host mounts one provider OAuth home and serves one active session.
- Codex OAuth state and Omnigent host identity never share a volume.
- Host/server authentication and provider OAuth remain separate.
- No user-authored mount name, host id, network, or daemon path becomes trusted authority.
- Raw OAuth state never enters Temporal history, bridge rows, checkpoints, logs, diagnostics, artifacts, or UI payloads.
- Competing provider credentials are removed before launch.
- Workspace and mount paths are containment-checked at the owning worker.
- On-demand resources are deterministic, labeled, and removed only by matching lease authority.
- A policy realization failure cannot degrade to a broader network, writable root, alternate credential, or generic host.
- Docker daemon administrators remain privileged; this design does not defend against a malicious daemon administrator.

---

## 21. Acceptance contract

A Settings-created Codex OAuth Provider Profile can be selected by `executionProfileRef`; MoonMind acquires the global profile lease, resolves an authorized static or on-demand binding, creates one durable host lease, mounts the exact generation at `/home/app/.codex`, proves login and `codex-native` registration, authorizes one bridge session, posts the first message once, harvests durable evidence, cleans the session and host idempotently, preserves the OAuth and application data volumes, and releases Provider Profile capacity last.

Static and on-demand modes expose the same profile, binding, readiness, bridge, artifact, checkpoint, diagnostic, and cleanup evidence. Productized policy/profile selection replaces environment-only launch choice without removing the canonical Compose bootstrap path. Credentialed cutover evidence uses immutable published stock images and the versioned live-conformance evidence contract.
