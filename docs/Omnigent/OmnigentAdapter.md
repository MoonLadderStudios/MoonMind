# Omnigent Adapter Design

**Document Class:** Canonical declarative  
**Status:** Current  
**Owners:** MoonMind Platform  
**Last updated:** 2026-07-18  
**Authority:** Canonical MoonMind contract for Omnigent agent identity, bridge execution, and profile-bound Codex host orchestration

Implementation progress belongs in the roadmap, issues, and pull requests. This document defines the durable desired state and the compatibility rules that implementations must preserve.

The normal product selection and request-compilation boundary for **Codex via Omnigent** is owned by [`CodexCreateToHostContract.md`](./CodexCreateToHostContract.md). This adapter preserves its `external/omnigent` identity and nested `codex-native` harness choice.

## Related documents

- [`docs/Omnigent/OmnigentBridge.md`](./OmnigentBridge.md)
- [`docs/Omnigent/OmnigentHostOAuth.md`](./OmnigentHostOAuth.md)
- [`docs/Omnigent/CombinedStackValidationAndRollback.md`](./CombinedStackValidationAndRollback.md)
- [`docs/Omnigent/ConformanceAndLiveSmoke.md`](./ConformanceAndLiveSmoke.md)
- [`docs/Temporal/ManagedAndExternalAgentExecutionModel.md`](../Temporal/ManagedAndExternalAgentExecutionModel.md)
- [`docs/Temporal/WorkflowArtifactSystemDesign.md`](../Temporal/WorkflowArtifactSystemDesign.md)
- [`docs/Security/ProviderProfiles.md`](../Security/ProviderProfiles.md)
- [`docs/Workflows/WorkspaceLocators.md`](../Workflows/WorkspaceLocators.md)

---

## 1. Purpose

MoonMind uses Omnigent as the live session and harness boundary for Codex while retaining MoonMind authority over product orchestration, credentials, policy, evidence, and recovery.

The canonical execution topology is:

```text
MoonMind.UserWorkflow / MoonMind.AgentRun
  -> profile-bound Omnigent execution coordinator
      -> Provider Profile lease + durable host lease
      -> static Compose host or deterministic on-demand Docker host
      -> MoonMind Omnigent bridge
          -> stock Omnigent server and unchanged omnigent-host
              -> one codex-native session / runner
```

This is a hybrid ownership model:

- Omnigent remains a canonical external agent identity at the workflow contract boundary.
- MoonMind may directly materialize and control the profile-bound host container used by that external session.
- Omnigent owns the live runner and harness protocol inside the selected host.
- MoonMind owns durable authorization, lifecycle evidence, artifacts, cleanup ordering, and recovery decisions.

The existence of MoonMind-managed host materialization does not create a second top-level agent identity and does not turn an Omnigent session into a direct Codex managed session.

---

## 2. Architectural decisions

### 2.1 Canonical agent identity

All Omnigent executions use one top-level identity:

```text
agentKind = external
agentId   = omnigent
```

Harness, agent, endpoint, capture, and session options live under `parameters.omnigent`. Do not register aliases such as `omnigent_codex`, `omnigent_session`, or `omnigent_managed`.

This stable identity keeps routing, checkpoint selection, bridge projections, policy, metrics, and artifact contracts unified while host implementation can evolve independently.

### 2.2 Codex host orchestration is MoonMind-owned

For an OAuth-backed Codex request, MoonMind resolves `executionProfileRef`, acquires the shared Provider Profile lease, resolves or creates the profile-bound host binding, creates an idempotent host lease, prepares the exact host, and binds the resulting host and session identities to the bridge record.

The workflow request never supplies a Docker volume name, credential body, arbitrary host id, or daemon-visible host path. Those values are resolved at trusted activity and runtime boundaries.

### 2.3 Stock Omnigent compatibility is mandatory

The supported proxy topology uses the published Omnigent server and an unchanged upstream `omnigent-host`. MoonMind may configure, launch, observe, and stop the host, but it must not fork the host protocol or require MoonMind-specific runner behavior.

Embedded compatibility mode, when enabled, must reuse upstream authentication and host/runner protocol components rather than inventing a MoonMind-only protocol.

### 2.4 MoonMind remains the evidence authority

Omnigent streams, snapshots, resources, files, and diagnostics are provider observations. MoonMind copies or projects required evidence into MoonMind artifacts and durable bridge records. Omnigent URLs and ids may be retained as references, but they do not replace `ArtifactRef` or MoonMind lifecycle evidence.

---

## 3. Ownership boundary

| Surface | Authority |
| --- | --- |
| Workflow and Step ordering | MoonMind Temporal workflows |
| Agent identity and request contract | MoonMind canonical agent runtime schemas |
| Provider Profile selection and account capacity | MoonMind Provider Profile Manager |
| Host-mode and runtime policy | MoonMind policy/profile selection |
| Host binding, host lease, credential generation | MoonMind durable Omnigent host records |
| Docker materialization for profile-bound hosts | MoonMind Omnigent host runtime |
| Host registration and runner protocol | Stock Omnigent server/host |
| Live Codex process and harness semantics | Omnigent host and `codex-native` runner |
| Session/event compatibility boundary | MoonMind Omnigent bridge |
| Durable artifacts, diagnostics, and audit evidence | MoonMind artifact and evidence systems |
| Checkpoint, resume, retry, and branch decisions | MoonMind recovery systems |

No boundary may silently substitute a different Provider Profile, credential source, host mode, network posture, workspace authority, or less-constrained execution path.

---

## 4. Canonical request contract

A profile-bound Codex Omnigent request uses `AgentExecutionRequest`:

```json
{
  "agentKind": "external",
  "agentId": "omnigent",
  "executionProfileRef": "codex_openai_oauth",
  "correlationId": "workflow:run:step",
  "idempotencyKey": "workflow:step:attempt",
  "instructionRef": "artifact:instructions",
  "parameters": {
    "omnigent": {
      "endpointRef": "default",
      "agent": {
        "agentName": "codex-native-ui",
        "harnessOverride": "codex-native"
      },
      "capture": {
        "stream": true,
        "snapshots": true,
        "changedFiles": true,
        "workspaceFiles": true,
        "sessionFiles": true
      }
    }
  }
}
```

Rules:

- `executionProfileRef` is required for the profile-bound Codex path.
- The selected profile must be enabled, connected, launch-ready, OpenAI-backed, and compatible with `codex_cli` OAuth materialization.
- A caller-provided `session.hostId` is always rejected. The trusted coordinator alone injects the exact host id from the durable profile binding immediately before session creation; product flows never accept manual host ids.
- The coordinator injects `hostType=external`, the exact registered host id, the resolved workspace path, `codex-native`, and a safe profile-authorization envelope immediately before session creation.
- Raw credentials, host registration tokens, Docker volume names, and absolute daemon paths never enter workflow-authored parameters.

Large instructions and inputs remain artifact-backed. The bridge posts the first message only after durable authorization and session identity are persisted.

---

## 5. Launch modes

The profile-bound Codex lane supports two host modes under one binding, bridge, readiness, evidence, and cleanup contract.

### 5.1 Static Compose bootstrap

`static_compose` uses the canonical repository `docker-compose.yaml` and the `omnigent-host-codex` profile. It is the supported local/bootstrap path and may keep a stable host identity between runs.

MoonMind still acquires the Provider Profile lease before assignment, validates the exact registered host and credential generation, permits at most one active session, and drains the host before releasing the profile lease.

### 5.2 Deterministic on-demand Docker

`on_demand_docker` starts one lease-owned container after the Provider Profile lease is acquired. Its name and labels are deterministic from durable lease identity. It receives one workflow workspace, one Codex OAuth home, one Omnigent state volume, and one active session.

Terminal cleanup removes only the lease-owned container and Omnigent state volume. The canonical OAuth volume and unrelated containers or volumes survive.

### 5.3 Selection authority

The desired product authority is an explicit, versioned host-mode choice compiled from the selected Omnigent agent profile and policy. Workflows and operators select policy/profile concepts, not a raw launch-profile string.

Workflow Create selects the versioned `omnigent-codex@1` execution target and
one of the built-in `codex-static@1` or `codex-on-demand@1` launch policies.
MoonMind compiles that selection into secret-free `effectiveLaunch` evidence
before acquiring a Provider Profile lease or mutating a host. Existing durable
bindings remain authoritative for retry and conflicting explicit policy fails
closed.

`OMNIGENT_CODEX_HOST_LAUNCH_PROFILE` is bootstrap compatibility for local
development only. It is consulted only when a request has no product-owned
selection and no durable binding; it cannot override either authority. Workflow
requests cannot provide host IDs, Docker volume names, credential bodies, or
absolute bind sources.

### 5.4 Image authority

Production and credentialed conformance prefer immutable `OMNIGENT_IMAGE_REF` and `OMNIGENT_HOST_IMAGE_REF` values containing digest-pinned server and host images. Legacy repository/tag pairs remain bootstrap-compatible, but a policy or conformance profile that requires immutability fails closed when only a mutable tag is available.

---

## 6. Durable lifecycle

For one logical idempotency key, the coordinator follows this order:

1. Reserve or load the bridge attempt envelope.
2. Validate `executionProfileRef` and profile readiness.
3. Acquire the purpose-aware Provider Profile lease.
4. Resolve the profile-bound host binding.
5. Create or reattach the deterministic host lease.
6. Persist bridge authorization with profile, provider lease, credential generation, binding, and host-lease refs.
7. Prepare the static or on-demand host.
8. Validate the credential mount and Codex login in the exact host environment.
9. Resolve exactly one online host advertising `codex-native`.
10. Persist the exact host id and readiness evidence.
11. Create or reattach the Omnigent session on that host.
12. Persist session identity before posting the first message.
13. Stream and normalize events, then harvest terminal resources and artifacts.
14. Interrupt or stop the session as required.
15. Drain the static host or remove the on-demand host and lease-owned state.
16. Persist terminal host state and release the host lease.
17. Release the Provider Profile lease last.

Retries reuse durable bridge, binding, host-lease, session, and first-message evidence. They do not create a second host or post a duplicate first message while the original authority may still be valid.

---

## 7. Capacity and policy hierarchy

The capacity hierarchy is additive, not competing:

1. **Provider Profile capacity** protects the mutable OpenAI OAuth identity and remains authoritative. OAuth capacity is one across direct execution, Omnigent execution, and credential-maintenance operations.
2. **Host capacity** permits at most one active host for the profile binding.
3. **Session capacity** permits at most one active session/runner on that profile-bound host.
4. **Machine and Docker capacity** governs whether a worker may materialize another container.
5. **Runtime resource policy** governs CPU, memory, process, temporary-storage, image, and timeout limits.
6. **Network policy** governs network attachment and allowed egress.

Host, machine, and policy counters must not release or override Provider Profile capacity. The profile lease is released only after credential consumers and host cleanup are complete.

Every run records the selected policy/profile refs and the effective launch snapshot so later evidence can explain why a mode, image, network, mount, or limit was chosen.

---

## 8. Workspace, mount, and network contract

### 8.1 Durable workspace identity

Durable workflows identify workspaces with the canonical `WorkspaceLocator` discriminated union. Absolute worker or Docker-daemon paths are runtime resolutions, not durable authority.

The owning worker resolves and validates the locator, performs root-containment checks, and then translates the approved path to a daemon-visible bind source. The host runtime must not derive authority from an arbitrary `session.workspace` value or legacy absolute path.

### 8.2 Host filesystem classes

| State class | Canonical target | Rule |
| --- | --- | --- |
| Codex OAuth home | `/home/app/.codex` | Profile-bound, read/write, generation-checked, exclusive |
| Omnigent host state | `/home/app/.omnigent` | Separate host or lease-owned state; never the OAuth volume |
| Workflow workspace | `/workspaces/run` for on-demand | Workflow-scoped, policy-resolved, daemon-visible |
| Resolved skills | `/opt/moonmind-skills` | Immutable run snapshot, read-only |
| Versioned tools | `/opt/moonmind-tools` | Pinned bundle, read-only |
| Artifact handoff | Policy-selected path or gateway | Never conflated with the OAuth or host-state volume |
| Temporary storage | `/tmp` | Bounded, removable, and non-authoritative |
| Optional caches | Policy-selected | Explicit owner, scope, retention, and invalidation |

The supported host user is UID/GID `1000:1000` with `HOME=/home/app`. On-demand hosts use a read-only root filesystem and bounded temporary storage. Static and on-demand modes must preserve the same safe target paths and credential separation.

### 8.3 Network policy

The host attaches only to the network selected by the effective deployment/policy snapshot and required for MoonMind/Omnigent communication. A network name is not itself an egress policy. Restricted egress requires an enforced network, proxy, or firewall boundary and must fail closed when a selected policy cannot be realized.

Host registration credentials and OpenAI OAuth credentials remain separate. Neither may substitute for the other.

---

## 9. Readiness and observability

Readiness is a sequence of durable stages, not one opaque Docker start:

```text
request_validated
profile_resolution
profile_readiness
profile_lease_wait / profile_lease_acquired
host_binding_resolution
host_lease_created
container_start
credential_mount
credential_preflight
host_registration
harness_readiness
bridge_authentication
session_creation
first_message_prepare / first_message_post
session_running
resource_harvest
host_cleanup
profile_lease_release
terminal
```

Every stage has a bounded status, timestamp, safe identifiers, and an actionable failure classification. Workflow Detail consumes the bridge projection, including failures before a normal Omnigent stream exists and runs with zero provider events.

Safe evidence includes profile, binding, provider-lease, host-lease, credential-generation, container, host, bridge-session, Omnigent-session, policy, workspace-locator, and artifact refs. Credential bodies, raw environment dumps, unredacted command output, and token-shaped values are forbidden.

---

## 10. Session, stream, and artifact contract

`integration.omnigent.execute` is the canonical streaming-gateway activity for the current bridge lane. It may create or reattach to a session, post the first message idempotently, stream events, resolve supported interventions, harvest resources, and return a terminal `AgentRunResult`.

The bridge preserves:

- a durable event page and cursorable stream;
- normalized conversation, tool, lifecycle, elicitation, approval, interrupt, stop, and resource events;
- a raw bounded event journal as artifact evidence;
- initial and terminal snapshots;
- capture manifests and redacted diagnostics;
- changed files, workspace files, optional diffs, session files, and child-session evidence;
- terminal and checkpoint external-state refs.

The Workflow Detail projection uses these records rather than relying on host-local logs or an Omnigent-only dashboard. Replay remains useful after a static host restart or an on-demand host removal.

---

## 11. Cancellation, cleanup, and reconciliation

Cancellation is typed and evidence-producing. Depending on runtime state, MoonMind may interrupt the active turn, stop the session, harvest available evidence, drain the host, and remove lease-owned resources.

Cleanup is idempotent across activity retry and worker failure:

- on-demand cleanup stops and removes the deterministic container and its lease-owned Omnigent state volume;
- static cleanup stops or drains the dedicated Codex host according to policy;
- expired, missing, orphaned, and stale-generation hosts are reconciled by the host janitor;
- cleanup failure leaves explicit `janitorRequired` evidence and does not falsely release credential capacity;
- Provider Profile release is the final lifecycle action.

Typed product controls for interrupt, stop, terminate, harvest, remove, generation drain, and stale-lease reconciliation must use the same durable authority checks. Direct Docker actions from UI callers are not an accepted control plane.

---

## 12. Recovery and checkpoints

Omnigent checkpoints use the `external_state_ref` lane. Durable checkpoint identity may include profile, provider-lease, binding, host-lease, credential-generation, host, bridge-session, Omnigent-session, first-message, workspace-locator, diagnostics, terminal, and artifact refs, but never credentials or daemon paths.

Recovery chooses between:

- **live reattach**, only when the original profile lease, credential generation, host registration, session, authorization, and first-message evidence remain valid; and
- **cold restore**, which reacquires the same profile, creates a new host lease, materializes validated artifact-backed state, and starts a fresh session.

A branch always obtains independent host/session authority and does not concurrently reuse the original OAuth lease.

---

## 13. Shared Docker substrate boundary

The generic Container Jobs/workload plane owns reusable primitives for `WorkspaceLocator` resolution, daemon-visible path translation, bounded/redacted logs, output manifests, runtime diagnostics, cancellation, and cleanup.

Profile-bound Omnigent hosts should reuse those primitives where their contracts match. They remain a distinct long-lived host/session lease model rather than being forced into a one-shot Container Job abstraction. Shared code must preserve:

- host and session identity across the live bridge lifecycle;
- exclusive credential ownership;
- exact registration and harness readiness;
- terminal resource harvest before cleanup;
- cleanup-before-profile-release ordering.

---

## 14. Security invariants

- One Codex OAuth profile has at most one active credential consumer across all MoonMind execution paths.
- One profile binding has at most one active host and one active session.
- The host mounts only the selected profile's OAuth home.
- Competing provider credentials and provider-base overrides are removed before launch.
- Host registration authentication is independent from provider OAuth.
- Durable payloads contain safe refs, not credential contents or daemon authority.
- Workspace and mount sources are resolved at trusted boundaries and containment-checked.
- On-demand containers are deterministic, labeled, non-root, read-only at the root filesystem, and limited by the effective runtime policy.
- Policy realization fails closed; MoonMind never silently selects a broader network, writable mount, alternate credential, or static host.
- Artifact and lifecycle evidence is redacted before publication.

---

## 15. Conformance and acceptance

The design is satisfied when a workflow can select a Codex Omnigent agent and Provider Profile without a pre-provisioned host or manual host id; MoonMind chooses a policy-authorized static or on-demand mode, acquires all required capacity, resolves a canonical workspace, materializes exactly one stock compatible host, proves credential and `codex-native` readiness, runs and harvests one session, projects durable evidence into Workflow Detail, cleans only owned resources, and releases the Provider Profile lease last.

The same request, binding, bridge, readiness, artifact, checkpoint, and cleanup contracts apply to both launch modes. Direct Codex remains a compatibility producer until the Omnigent path passes the required deterministic and credentialed live conformance gates.

Credentialed conformance uses `tools/run_omnigent_live_conformance.py` with digest-pinned server and host references and an operator-provisioned live action adapter. Evidence references are durable, schema-versioned, scenario-bound, secret-scanned, and independently resolved; a bare success boolean or repository semantic test backend is not accepted as proof of live behavior. The isolated live Compose project cleans its own containers and networks without removing enrolled OAuth or unrelated volumes.
