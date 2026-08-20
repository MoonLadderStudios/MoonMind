# Omnigent Harness Platform Design

**Status:** Proposed  
**Document Class:** System / Feature Design View  
**Owners:** MoonMind Platform  
**Last updated:** 2026-08-19  
**Authority:** Desired-state architecture for making Omnigent the primary harness wrapper provider for MoonMind while preserving the existing Codex product path until a generic realization proves equivalent support.

## Related documents

- [`docs/Omnigent/OmnigentAdapter.md`](./OmnigentAdapter.md)
- [`docs/Omnigent/OmnigentBridge.md`](./OmnigentBridge.md)
- [`docs/Omnigent/AgentProfiles.md`](./AgentProfiles.md)
- [`docs/Omnigent/PolicyAuthority.md`](./PolicyAuthority.md)
- [`docs/Omnigent/OmnigentHostOAuth.md`](./OmnigentHostOAuth.md)
- [`docs/Omnigent/CodexCreateToHostContract.md`](./CodexCreateToHostContract.md)
- [`docs/Omnigent/NormalCodexProductPathReconciliation.md`](./NormalCodexProductPathReconciliation.md)
- [`docs/Omnigent/CodexSupportAndCutover.md`](./CodexSupportAndCutover.md)
- [`docs/Omnigent/ControlPlaneAggregates.md`](./ControlPlaneAggregates.md)
- [`docs/Omnigent/ControlPlaneConcurrencyAndFencing.md`](./ControlPlaneConcurrencyAndFencing.md)
- [`docs/Omnigent/ConformanceAndLiveSmoke.md`](./ConformanceAndLiveSmoke.md)
- [`docs/Security/ProviderProfiles.md`](../Security/ProviderProfiles.md)
- [`docs/Temporal/ManagedAndExternalAgentExecutionModel.md`](../Temporal/ManagedAndExternalAgentExecutionModel.md)
- [`docs/Workflows/WorkspaceLocators.md`](../Workflows/WorkspaceLocators.md)
- [`docs/Workflows/CheckpointBranchSystem.md`](../Workflows/CheckpointBranchSystem.md)
- [`docs/Workflows/WorkflowRemediation.md`](../Workflows/WorkflowRemediation.md)

## Advance organizer

**One sentence:** MoonMind treats Omnigent as one stable external execution provider whose dynamically discovered harnesses are admitted through capability, credential, host, policy, and conformance contracts rather than through one MoonMind runtime implementation per harness.

**One paragraph:** A workflow selects a versioned Omnigent Agent Profile. MoonMind resolves the profile against a pinned Omnigent harness catalog, compatible Provider Profiles, approved credential materializers, an immutable Host Class, and a launch policy. It compiles these choices into one secret-free execution plan before acquiring credentials or mutating a host. The current Codex profile-bound lane remains a fully valid realization of this plan and continues to serve Codex without behavioral change while the generic planner, catalog, and materializer system mature. New harnesses use the generic contracts without requiring new branches throughout the workflow coordinator.

**Full description:** MoonMind owns durable workflow orchestration, authority, credentials, workspace preparation, publication, evidence, recovery, and cleanup. Omnigent owns provider sessions, runners, harness execution, and the live harness protocol. The platform discovers what Omnigent understands, observes what a particular host can execute, and then intersects those facts with MoonMind policy. Discovery never grants trust. A harness becomes launchable only when its exact version is trusted, its required credential strategy is approved, a compatible host is ready, the selected model is valid, and the requested workflow capabilities can be enforced. Support is proven per combination rather than inferred from code presence.

## 1. Purpose

MoonMind is moving toward Omnigent as the primary wrapper around coding-agent harnesses. The target is not a collection of separate MoonMind runtimes named after every agent. The target is one Omnigent execution platform that can run any trusted and realizable harness reported by the selected Omnigent endpoint.

This design generalizes the existing Codex-through-Omnigent system. It does not discard that work. The current Codex lane supplies the reference implementation for profile selection, credential leasing, host leasing, workspace materialization, bridge authorization, session execution, publication, checkpoint evidence, cleanup, and support qualification.

The design owns the general target architecture. The existing Codex documents continue to own the current Codex specialization until a later evidence-backed cutover explicitly moves that specialization onto the generic realizer. A conflict must be resolved without weakening the current Codex contract.

## 2. Goals

The platform has the following goals:

1. **One top-level Omnigent identity.** Every harness executes through `agentKind=external`, `agentId=omnigent`.
2. **Catalog-driven harness support.** MoonMind discovers harness identities and declared capabilities from Omnigent instead of keeping a hand-maintained runtime list.
3. **No coordinator branch per harness.** Adding an approved harness is primarily a catalog, materializer, Host Class, policy, and conformance operation.
4. **Preserve the Codex path.** Existing Codex identities, Provider Profiles, OAuth volumes, host services, lifecycle evidence, support gates, and rollback behavior remain usable while the generic system develops.
5. **Capability-derived planning.** The planner selects only combinations that satisfy workflow, harness, host, credential, bridge, and policy requirements.
6. **Provider-account capacity across harnesses.** A provider account, quota, cooldown, or mutable credential home remains one shared authority even when several harnesses can use it.
7. **Safe extension.** Built-in and community harnesses can be discovered without granting an untrusted plugin secrets, host authority, or production support.
8. **Local-first operation.** Docker Compose remains the normal local deployment. On-demand hosts add no permanent container footprint.
9. **Objective support claims.** A harness combination is supported only when the required conformance evidence exists.
10. **Durable recovery.** Retry, continuation, branch, remediation, cancellation, and janitor behavior use the same fenced authority model for every harness.

## 3. Non-goals

This design does not:

- fork the Omnigent host or runner protocol.
- recreate provider agents inside MoonMind.
- claim that every discovered harness is immediately production-supported.
- install arbitrary harness software during an ordinary production workflow launch.
- permit a workflow to supply raw Docker options, host paths, volume names, host ids, or credentials.
- remove the direct Codex fallback or the current Codex-through-Omnigent lane before their existing retirement contracts permit it.
- require one universal host image containing every possible harness.
- treat an upstream capability declaration as proof that MoonMind can safely enforce the capability.
- replace the Omnigent control-plane aggregate and fencing designs with a second session authority.
- make display names, aliases, or provider marketing names durable identity.

## 4. Governing decisions

### 4.1 Stable execution identity

The canonical workflow identity remains:

```text
agentKind = external
agentId   = omnigent
```

Harness, agent, endpoint, model, Provider Profile, Host Class, policy, and session selections are nested authority. MoonMind does not add top-level identities such as `omnigent_opencode`, `omnigent_qwen`, or `omnigent_claude`.

### 4.2 MoonMind owns authority

MoonMind owns:

- workflow and Step ordering.
- immutable Agent Profile selection.
- Provider Profile selection and account capacity.
- credential-materialization approval.
- host and network policy.
- workspace identity and mutation authority.
- repository publication.
- checkpoint and remediation policy.
- capture and evidence.
- cleanup and recovery decisions.

Omnigent owns:

- harness registration and metadata.
- agent bundles and stable upstream agent identity.
- host and runner protocol.
- live provider session behavior.
- harness-specific message, tool, elicitation, and terminal behavior.
- harness-native resume and fork mechanisms.

No Omnigent catalog row, agent bundle, host claim, or plugin declaration can broaden MoonMind authority.

### 4.3 Discovery is not trust

MoonMind distinguishes:

```text
discovered
  != trusted
  != installed
  != authenticated
  != launchable
  != supported
```

A discovered harness may be visible in Settings while remaining non-launchable. A trusted harness may still lack a compatible host. An installed harness may still lack credentials. A launchable combination may remain experimental until conformance evidence proves support.

### 4.4 Current Codex is a conforming specialization

The existing Codex profile-bound implementation is the first registered execution realizer. The generic platform must be able to compile a Codex execution plan that delegates to the existing implementation without changing its observable contract.

The existing path is not an emergency fallback selected after a generic failure. It is an explicit, versioned realization selected before launch. A failed explicit generic selection never silently switches to Codex.

### 4.5 Plans are immutable and secret-free

Every launch is compiled into a digest-addressed `OmnigentExecutionPlan` before Provider Profile lease acquisition, secret resolution, workspace mutation, or host creation.

The plan contains references and compatibility decisions. It never contains secret bodies, OAuth files, resolved host filesystem paths, Docker socket authority, or caller-authored host ids.

### 4.6 Live readiness is authoritative

Catalog and Host Class manifests provide planning evidence. The exact selected host must still report the selected harness as ready before runner or session creation.

### 4.7 Support is combination-specific

Support applies to a combination of:

```text
Omnigent version
+ harness version
+ agent version or bundle digest
+ credential materializer
+ Provider Profile class
+ Host Class and architecture
+ launch policy
+ capability requirements
```

Support for one combination does not imply support for another.

## 5. System topology

```text
MoonMind workflow or Step
  -> immutable Omnigent Agent Profile snapshot
  -> Omnigent Execution Planner
       -> pinned harness catalog
       -> Provider Profile and credential bindings
       -> credential materializer registry
       -> Host Class registry and live host readiness
       -> launch policy and MoonMind capability policy
  -> secret-free OmnigentExecutionPlan
  -> fenced Omnigent control plane
       -> Provider Profile leases
       -> host binding and host lease
       -> workspace materialization
       -> credential materialization
       -> exact host readiness
  -> stock Omnigent server and host protocol
       -> runner
       -> selected harness
       -> selected upstream agent
  -> MoonMind bridge, evidence, publication, checkpoint, and cleanup
```

The planner is pure with respect to provider side effects. The realizer owns side effects through Activities and trusted services. Temporal remains the durable orchestration authority.

## 6. Identity model

The following identities are distinct:

| Identity | Purpose |
| --- | --- |
| `endpointRef` | Selected Omnigent control plane |
| `harnessCatalogRef` | Immutable observed harness catalog snapshot |
| `harnessId` | Canonical Omnigent harness identifier |
| `agentProfileRef` | MoonMind-owned immutable Agent Profile version |
| `agentId` and `agentVersion` | Stable upstream Omnigent agent identity |
| `providerProfileRef` | Provider account, credential, capacity, and cooldown authority |
| `credentialMaterializerRef` | Approved method for presenting one Provider Profile to the harness |
| `credentialBindingSetRef` | Named mapping from Agent Profile credential slots to Provider Profiles and materializers |
| `hostClassRef` | Immutable host environment and image contract |
| `launchPolicyRef` | Host mode, limits, network, capture, cleanup, and control policy |
| `executionRealizerRef` | Versioned implementation that realizes the plan |
| `executionPlanRef` | Digest of the complete secret-free launch decision |
| `hostBindingRef` and `hostLeaseRef` | Durable host ownership |
| `providerLeaseRef` | Durable provider-account capacity ownership |
| `omnigentHostId` | Exact host reported by Omnigent after realization |
| `omnigentSessionId` | Exact provider session |
| `chatBindingId` | Opaque MoonMind native Workflow Chat identity |

Display labels may change without changing identity. Aliases are accepted only at discovery or user-input edges. Durable plans store canonical identifiers.

`executionRealizerRef` is selected only by the trusted planner. It is never workflow-authored and cannot be overridden through Agent Profile settings.

## 7. Omnigent harness catalog

### 7.1 Catalog authority

MoonMind reads the authenticated Omnigent harness catalog and projects it into a bounded, immutable snapshot.

The upstream source includes the harness registry, capability declarations, setup steps, aliases, install metadata, and plugin-load state exposed by the selected Omnigent endpoint. MoonMind also synchronizes stable agent inventory and connected-host readiness.

The effective inventory uses three views:

```text
/v1/harnesses -> what the Omnigent endpoint understands
/v1/agents    -> which stable agent definitions may be selected
/v1/hosts     -> which connected hosts can execute each harness now
```

### 7.2 Catalog snapshot

A snapshot has this logical shape:

```yaml
schemaVersion: moonmind.omnigent-harness-catalog.v1
endpointRef: default
omnigentVersion: "<semver>"
observedAt: 2026-08-19T20:00:00Z
sourceDigest: sha256:...
catalogRef: omnigent-harness-catalog:sha256:...
pluginLoadErrors: []
harnesses:
  - id: opencode-native
    aliases:
      - opencode
      - native-opencode
    label: OpenCode
    source:
      kind: core
      package: omnigent
      version: "<semver>"
    capabilities:
      integrationMode: native-server
      authModel: own-auth
      resume: warm-reattach
      forkHistory: preamble
      modelFamily: multi
      effortFamily: none
      elicitation: sse-permission
      interrupt: true
      streaming: true
      subagents: true
      steering: null
      liveQueue: null
      images: null
      compaction: null
    setupSteps: []
```

Unknown capability values remain unknown. They are not coerced to supported or unsupported.

### 7.3 Trust classification

Each canonical harness version has one trust state:

- `core_trusted`: shipped by the approved Omnigent distribution.
- `plugin_approved`: supplied by an explicitly approved plugin package and version.
- `quarantined`: discovered but not approved to receive credentials or execute workflows.
- `blocked`: explicitly denied by policy or a security finding.

Trust is bound to package identity, version, and digest where available. A plugin name alone is insufficient.

### 7.4 Freshness

Catalog snapshots are immutable. New launches require a snapshot inside the configured freshness bound unless the selected policy explicitly permits a previously verified offline snapshot. Historical runs retain their original snapshot ref.

An endpoint outage may retain the last known catalog for diagnostics. It does not silently make stale harnesses launchable.

## 8. Omnigent Agent Profiles

### 8.1 Role

An Omnigent Agent Profile is the reusable MoonMind selection surface. It describes the desired agent, harness, capabilities, model, workspace, tools, capture, continuation, and publication behavior.

It does not own credentials, raw host authority, or secret material.

### 8.2 Versioned document

The desired document shape is:

```yaml
schemaVersion: moonmind.omnigent-agent-profile.v2
endpointRef: default

source:
  upstreamId: opencode-native-ui
  upstreamVersion: "<agent-version>"

harness:
  id: opencode-native
  catalogRef: omnigent-harness-catalog:sha256:...

requirements:
  harness:
    required:
      - interrupt
    preferred:
      - streaming
      - warm-reattach
  moonmind:
    required:
      - repository.read
      - artifact.capture
  host:
    required:
      - workspace.bind
      - restricted-egress
      - run-dedicated-isolation

credentialSlots:
  - id: primary-model
    optional: false
    acceptedAuthModels:
      - own-auth
    acceptedProviderIds:
      - opencode

model:
  model: null
  effort: null
  settings: {}

workspace:
  mutation: allowed
  requiredCapabilities: []

skills: []
tools: []

capture:
  stream: true
  evidence: true

continuations:
  checkpoint: true
  branch: true
  remediation: true

publish:
  mode: none

allowedLaunchPolicyRefs:
  - omnigent-on-demand@1
```

### 8.3 Compatibility with existing profiles

Existing v1 Agent Profile versions remain immutable and replayable. Their `harness`, `providerRequirements`, execution profile, and policy fields compile through a compatibility decoder into the same generic planning inputs.

New profile versions may continue to use the current v1 form until the v2 authoring surface is available. The compatibility decoder is required for historical and in-flight authority. It is not a second mutable profile system.

### 8.4 Agent templates

A trusted harness may use:

- a stock upstream agent.
- a user-selected upstream agent.
- an immutable imported bundle.
- a MoonMind-generated portable default bundle.

A deterministic template factory may generate a minimal agent bundle for an approved harness. The generated bundle is validated, stored as an artifact, imported through the authenticated Omnigent boundary, and pinned by upstream identity and bundle digest.

The template factory adapts only structural harness needs. It does not duplicate provider-agent semantics.

## 9. Provider Profiles and credential binding sets

### 9.1 Provider Profile continuity

`ManagedAgentProviderProfile` remains the durable authority for provider account selection, credentials, generation, concurrency, cooldown, enabled state, and readiness.

The generic platform does not create a parallel account-profile system. Existing Codex and Claude profiles remain valid.

Runtime-specific fields in an existing profile are interpreted as compatibility constraints for the selected credential materializer. They do not require immediate destructive migration.

### 9.2 Credential slots

An Agent Profile names the credential roles required by the harness. A role may be optional.

Examples include:

```text
primary-model
secondary-model
embedding-provider
vendor-login
```

Repository and publication credentials remain capability-scoped MoonMind credentials. They are not model-provider slots.

### 9.3 Credential binding set

A versioned binding set maps every required slot to one Provider Profile and one materializer:

```yaml
schemaVersion: moonmind.omnigent-credential-bindings.v1
bindingSetId: opencode-go-primary
bindings:
  primary-model:
    providerProfileRef: opencode-go-default
    materializerRef: opencode-auth-json@1
```

A binding set contains references only.

### 9.4 Capacity

Effective capacity is the minimum of:

```text
Provider Profile account capacity
credential materializer capacity
host binding capacity
launch policy capacity
worker and container backend capacity
```

All leases for one execution are acquired in deterministic order by Provider Profile id. They are released in reverse order after host and credential cleanup.

A provider cooldown applies across every harness using the same Provider Profile. Switching harnesses does not evade quota or cooldown authority.

## 10. Credential materializer registry

### 10.1 Purpose

A credential materializer is the trusted boundary that turns a Provider Profile reference into the runtime state a harness can consume.

The harness plugin may describe what it needs. MoonMind decides whether a materializer is trusted, which secret roles it may resolve, which target paths are allowed, which host modes may use it, and how cleanup works.

### 10.2 Materializer contract

A materializer declares:

```yaml
materializerId: opencode-auth-json
version: 1
acceptedHarnesses:
  - opencode-native
acceptedAuthModels:
  - own-auth
supportedHostModes:
  - on-demand
  - static-connected
requiredSecretRoles:
  - api_key
state:
  scope: run
  mutable: false
target:
  kind: generated-file
  path: /home/app/.local/share/opencode/auth.json
  permissions: "0600"
preflight:
  kind: live-model-options
cleanup:
  mode: remove-owned-state
```

The trusted implementation provides these operations:

```text
validateBinding
reserve
materialize
attest
refresh or replace when allowed
cleanup
```

### 10.3 Materializer outputs

Materialization returns a secret-free handle:

```yaml
credentialRuntimeRef: credential-runtime:...
providerProfileRef: opencode-go-default
credentialGeneration: 4
materializerRef: opencode-auth-json@1
mountClass: provider-auth
targetPath: /home/app/.local/share/opencode/auth.json
accessMode: read-only
cleanupRef: credential-cleanup:...
attestationRef: artifact:...
```

Secret bodies never enter the handle.

### 10.4 Built-in materializer classes

The platform supports these materializer classes:

| Class | Use |
| --- | --- |
| `oauth-home` | Exclusive mutable CLI OAuth home with generation tracking |
| `omnigent-provider-config` | Lease-owned Omnigent provider configuration backed by MoonMind secret refs |
| `generated-auth-file` | Vendor-owned API-key or OAuth file generated in protected run state |
| `secret-env-file` | Protected environment file consumed by a trusted entrypoint |
| `session-scoped-config` | Per-session generated provider or vendor configuration |
| `host-owned-auth` | Pre-authenticated connected host where MoonMind does not copy the credential |
| `none` | Harness requires no model credential |

### 10.5 Mutable credential state

A mutable credential store requires exclusive ownership unless a provider-specific design proves safe concurrent refresh.

Refreshed state must either persist to the authoritative credential store or be rejected before launch. A disposable copy that silently loses refresh state is invalid.

### 10.6 Current Codex materializer

The existing Codex OAuth volume, generation checks, startup scripts, readiness checks, profile lease, and release-last cleanup form the initial `codex-oauth-home@1` materializer.

The generic materializer interface wraps those existing operations. It does not require rewriting them before another harness can be added.

## 11. Host Classes

### 11.1 Purpose

A Host Class is an immutable declaration of an environment capable of running a set of harnesses and materializers.

A Host Class is not a live host. It is a selectable environment contract. The exact host must still prove live readiness.

### 11.2 Host Class document

```yaml
schemaVersion: moonmind.omnigent-host-class.v1
hostClassId: omnigent-native-standard
version: 3
imageRef: ghcr.io/example/omnigent-host@sha256:...
omnigentVersion: "<semver>"
architectures:
  - linux/amd64

declaredHarnesses:
  - codex-native
  - claude-native
  - opencode-native
  - pi-native

integrationModes:
  - native-tui
  - native-server
  - cli-subprocess
  - sdk-in-process

materializerRefs:
  - codex-oauth-home@1
  - opencode-auth-json@1
  - omnigent-provider-config@1

features:
  git: true
  tmux: true
  bubblewrap: true
  workspaceBind: true
  readOnlyRoot: true
  restrictedEgress: true
  mountedSkills: true
  mountedTools: true

runtime:
  uid: 1000
  gid: 1000
  home: /home/app
```

### 11.3 Existing host realizations

The existing `omnigent-host-codex` static service, Codex on-demand container path, scripts, OAuth volume, state volumes, mounted tools, Skill projection, restricted egress, and health checks remain one registered Host Class realization.

The current Claude static service may be registered independently.

No existing service is renamed merely to make the implementation appear generic. The generic planner selects these concrete realizations through descriptors.

### 11.4 Host composition

MoonMind may operate several bounded Host Classes:

- core SDK and subprocess host.
- standard native harness host.
- specialized vendor host.
- approved community-plugin host.
- connected static host for host-owned authentication.

A single large image is not required.

### 11.5 Installation policy

Host-side harness installation is an operator setup or image-building action. An ordinary production workflow does not download or install a new harness.

Development and connected-host flows may expose Omnigent setup operations. The resulting host readiness is observed and never inferred from an installation request alone.

## 12. Launch policies

### 12.1 Generic policy

A launch policy governs host behavior rather than provider identity:

```yaml
schemaVersion: moonmind.omnigent-launch-policy.v2
policyId: omnigent-on-demand
version: 1
hostMode: on-demand
hostClassSelector:
  requiredFeatures:
    - readOnlyRoot
    - restrictedEgress
    - workspaceBind
isolation:
  runDedicated: true
limits:
  cpuMillis: 2000
  memoryMiB: 4096
  processes: 256
  timeoutSeconds: 5400
  temporaryStorageMiB: 256
network:
  egressPolicyRef: omnigent-restricted-egress@1
capture:
  required: true
  retentionDays: 30
cleanup:
  mode: remove
  janitor: true
controlCapabilities:
  - interrupt
  - terminate
  - clear_context
```

### 12.2 Existing Codex policies

`codex-on-demand@1` and `codex-static@1` remain valid policy versions. The generic compiler reads their normalized policy fields without relying on the `codex-` prefix.

They may continue to govern Codex for as long as their support and rollback contracts require. A future generic policy may replace them for new selections only after equivalent behavior is proven.

### 12.3 Policy intersection

A policy is compatible only when it permits:

- the harness integration mode.
- the selected Host Class.
- every credential materializer.
- the requested workspace mutation.
- required repository and publication operations.
- required control and continuation capabilities.
- the required network and capture posture.

Policy mismatch blocks before lease acquisition.

## 13. Capability negotiation

### 13.1 Capability planes

The planner computes:

```text
workflow requirements
∩ Agent Profile requirements
∩ harness declarations
∩ exact host readiness
∩ credential materializer capabilities
∩ MoonMind bridge capabilities
∩ launch policy
```

### 13.2 Required, preferred, and unknown

- Missing required capability blocks launch.
- Unknown required capability blocks launch.
- Missing preferred capability may produce an explicit degraded decision.
- Unknown preferred capability is recorded as unknown and may be treated as unavailable.
- A degraded decision never broadens authority.
- No mismatch silently selects another harness, Provider Profile, Host Class, or policy.

### 13.3 Representative capability rules

| Requirement | Admission evidence |
| --- | --- |
| Active cancellation | Harness interrupt support, bridge control support, and policy permission |
| Token streaming | Harness streaming declaration and observed stream conformance |
| Warm continuation | Warm reattach support and retained session or host state |
| Cold continuation | Workspace checkpoint plus a supported rebuild or new-session continuation strategy |
| Tool approval | Harness elicitation mode plus MoonMind approval authority |
| Unattended execution | No unresolved interactive login, trust, or permission step |
| Subagent fanout | Harness subagent support plus MoonMind execution-fanout capability |
| Image input | Harness image support and bridge transport support |
| Reasoning effort | Compatible effort family and model support |
| Model override | Compatible model family and live model-option validation |
| Repository mutation | Workspace authority, Git tooling, credential capability, and publish policy |
| Restricted egress | Enforced network attestation for the exact host |
| Native Workflow Chat | Capability intersection and binding-scoped enforcement |

## 14. Omnigent execution plan

### 14.1 Plan document

The planner emits:

```yaml
schemaVersion: moonmind.omnigent-execution-plan.v1
endpointRef: default
agentProfileSnapshotRef: omnigent-agent-profile:...
harnessCatalogRef: omnigent-harness-catalog:sha256:...
harnessId: opencode-native
agentId: opencode-native-ui
agentVersion: "<agent-version>"

credentialBindingSetRef: opencode-go-primary
credentialBindings:
  primary-model:
    providerProfileRef: opencode-go-default
    credentialGeneration: 4
    materializerRef: opencode-auth-json@1

hostClassRef: omnigent-native-standard@3
launchPolicyRef: omnigent-on-demand@1
executionRealizerRef: generic-omnigent-host@1

model:
  id: opencode/...
  effort: null

capabilityDecision:
  requiredSatisfied:
    - interrupt
    - repository.read
    - artifact.capture
  preferredSatisfied:
    - streaming
  degraded: []
  unknown: []

workspaceIntentRef: workspace-intent:sha256:...
capturePolicyRef: ...
policySnapshotRef: omnigent-policy:sha256:...
planRef: omnigent-execution-plan:sha256:...
```

### 14.2 Plan exclusions

The plan excludes:

- secret bodies.
- OAuth or vendor credential files.
- Docker volume names.
- Docker socket access.
- arbitrary bind sources.
- resolved worker or daemon paths.
- caller-provided host ids.
- mutable environment-derived authority.
- unbounded upstream metadata.

### 14.3 Runtime binding

After the plan is committed and leases are acquired, the realizer creates a separate fenced runtime binding containing:

- Provider Profile lease refs.
- credential runtime refs.
- host binding and host lease refs.
- credential generations.
- exact host id.
- exact workspace resolution.
- exact model-option attestation.
- exact session id.
- cleanup authority refs.

The runtime binding references the plan and never changes its decisions.

## 15. Control-plane integration

The generic platform uses the canonical Omnigent control-plane aggregates.

`OmnigentSession` owns the immutable Agent Profile snapshot ref, execution plan ref, provider session authority, chat binding, desired and observed lifecycle state, revision, and fencing generations.

`OmnigentTurnAttempt` owns request idempotency and attempt delivery. It cannot replace the execution plan or terminalize the session.

`OmnigentObservation` records bounded catalog, host, model, event, and cleanup evidence. Full payloads remain artifact-backed.

`OmnigentCommand` journals host, runner, session, message, interruption, harvest, and cleanup side effects.

Provider Profile, host lease, session supervisor, and cleanup generations fence stale owners. A stale activity result triggers fresh reconciliation. It never blindly repeats a provider side effect.

The generic design does not create a second session authority beside these aggregates.

## 16. Execution lifecycle

A conforming realization preserves this order:

1. Validate workflow and Step authority.
2. Resolve the immutable Agent Profile snapshot.
3. Resolve the pinned harness catalog and trust state.
4. Resolve the exact upstream agent or bundle.
5. Negotiate required and preferred capabilities.
6. Resolve credential slots and compatible Provider Profiles.
7. Select compatible materializers.
8. Select a compatible Host Class and launch policy.
9. Compile and persist the execution plan.
10. Acquire Provider Profile leases in deterministic order.
11. Resolve or create the durable host binding and host lease.
12. Materialize the authoritative workspace.
13. Materialize credential runtime state.
14. Start or attach the selected host realization.
15. Confirm the exact host reports the exact harness as ready.
16. Resolve and attest live model options when a model is selected.
17. Persist the exact host and credential binding.
18. Create or reattach the Omnigent session.
19. Persist the session identity before posting the first message.
20. Prepare and post the idempotent first message.
21. Stream and normalize events.
22. Route approvals, intervention, and control through capability enforcement.
23. Harvest artifacts, repository evidence, capture, and checkpoints.
24. Stop or drain the provider session as required.
25. Clean up run-scoped materializer state.
26. Remove the on-demand host or release the connected host.
27. Persist terminal cleanup evidence.
28. Release Provider Profile leases last.

A retry reuses the same plan, session, command identities, workspace authority, and applicable host authority. It does not replan against a newer catalog or silently change account, model, harness, or host policy.

## 17. Session, continuation, branch, and remediation semantics

Harness declarations guide continuation but do not replace MoonMind checkpoint authority.

### 17.1 Warm reattach

Warm reattach is valid only when the same provider session and compatible host state remain authoritative. A newer host or credential generation requires explicit reconciliation.

### 17.2 Cold continuation

A cold continuation uses immutable workspace checkpoint evidence, prior result refs, and a harness-supported strategy:

- rebuild vendor-native history.
- inject a bounded continuation preamble.
- create a new session with an explicit context package.
- reject when no safe strategy exists.

The planner does not pretend that all harnesses have equivalent resume semantics.

### 17.3 Branches and remediation

Checkpoint branches and remediation preserve:

- Agent Profile snapshot.
- harness and agent identity.
- Provider Profile bindings and generations.
- materializer refs.
- Host Class and launch policy.
- execution plan lineage.
- workspace checkpoint authority.

A branch or remediation attempt may select a different harness only through a new explicit Agent Profile and policy decision. It is never an implicit recovery fallback.

## 18. Native Workflow Chat and controls

Native Workflow Chat continues to use the binding-scoped facade.

The effective control surface is the intersection of:

```text
upstream session and harness capabilities
∩ Agent Profile snapshot
∩ execution plan
∩ workflow and Step state
∩ caller permission
```

The browser may hide unavailable controls. The bridge remains the enforcement boundary.

Model, effort, terminal, file, approval, interrupt, stop, clear-context, workspace, subagent, and resource controls remain separately gated. An upstream control is technical availability, not authorization.

## 19. Evidence and observability

Every run records safe references for:

- Agent Profile version and digest.
- harness catalog ref and trust classification.
- harness and upstream agent identity.
- credential binding set.
- Provider Profile refs and generations.
- materializer refs and attestations.
- Host Class and immutable image digest.
- launch policy and compiled policy ref.
- execution plan ref.
- capability decision.
- model-option attestation.
- host, runner, session, and chat binding identity.
- workspace resolution.
- capture and repository evidence.
- checkpoint and continuation lineage.
- cleanup claims and results.

Logs and metrics use bounded reason codes and low-cardinality labels. Secret values, raw provider payloads, terminal transcripts, and unbounded diagnostics remain artifact-backed and redacted.

Objective terminal evidence remains required. Process exit, wrapper completion, assistant prose, or a mutable filesystem path is not completion.

## 20. Cleanup and janitor authority

Every materializer returns a non-secret cleanup ref. Every host realization returns host cleanup authority. Cleanup is revision and generation fenced.

On-demand cleanup removes only plan-owned resources. Static connected-host cleanup drains plan-owned sessions and temporary state without deleting unrelated host authentication.

Provider Profile leases release only after:

- the harness process no longer consumes the credential.
- materializer cleanup is complete or durably delegated to the janitor.
- host cleanup is complete or durably delegated.
- terminal evidence records the cleanup state.

A cancellation or ambiguous provider outcome retains enough durable authority for retry or janitor reconciliation. It does not release credentials while a consumer may still be alive.

## 21. Support classification

MoonMind reports one support classification for each exact combination.

### 21.1 Fully managed

- approved on-demand or managed host.
- MoonMind-managed credential materialization.
- unattended launch.
- live model validation when applicable.
- interruption and capture.
- cleanup and janitor evidence.
- checkpoint and recovery coverage required by the selected capabilities.

### 21.2 Connected host

- approved static host.
- host-owned authentication or device-bound setup.
- MoonMind can select, lease, and drain the host.
- workflow launch is unattended after operator setup.

### 21.3 Experimental

- trusted and launchable.
- bounded smoke validation passes.
- one or more support rows lack protected evidence.

### 21.4 Discovered only

- present in the catalog.
- no approved materializer, Host Class, or policy combination.
- visible with actionable setup guidance.
- not launchable.

### 21.5 Quarantined

- plugin or package is not approved.
- receives no provider credentials, workspace mutation authority, or workflow execution authority.

## 22. Conformance

### 22.1 Generic contract suite

Every supported combination proves:

- catalog identity and freshness.
- trust decision.
- Agent Profile and bundle identity.
- materializer secret containment.
- Provider Profile capacity and cooldown.
- exact host readiness.
- model validation.
- session creation and idempotent first message.
- stream and terminal evidence.
- requested control capabilities.
- workspace and repository boundaries.
- cancellation and ambiguous delivery.
- host and credential cleanup.
- janitor recovery.
- checkpoint behavior required by the profile.
- raw-channel secret scans.

### 22.2 Harness-specific evidence

Harness-specific tests prove claims that cannot be inferred from the generic contract, including:

- vendor login and refresh behavior.
- exact model-option behavior.
- native terminal takeover.
- elicitation behavior.
- fork-history semantics.
- streaming granularity.
- interruption behavior.
- subagent behavior.

### 22.3 Codex regression requirement

The current Codex conformance and support matrices remain required while Codex uses either the current or generic realizer.

A generic refactor is not allowed to reduce:

- OAuth exclusivity.
- exact host binding.
- workspace isolation.
- mounted Skill and tool projection.
- restricted egress.
- capture.
- repository publication.
- checkpoint evidence.
- cancellation.
- cleanup.
- replay and historical read compatibility.
- rollback behavior.

## 23. Product experience

### 23.1 Settings

Settings exposes:

- Omnigent endpoint and version.
- discovered harnesses.
- trust and support classification.
- capability declarations.
- setup steps.
- compatible Host Classes.
- compatible Provider Profiles.
- credential binding sets.
- model options.
- validation and smoke status.
- active lease and cooldown state.

The normal view shows only essential setup. Advanced host, policy, and materializer details use progressive disclosure.

### 23.2 Workflow Create

The normal selection is:

```text
Execution provider: Omnigent
Agent Profile: <profile>
Provider account: <compatible Provider Profile>
Model: <profile default or explicit selection>
Host policy: <default compatible policy>
```

Raw harness selection may be exposed as an advanced Agent Profile authoring option. Raw host ids, Docker volumes, credential files, and environment variables are never authoring controls.

### 23.3 Default behavior

Omnigent may become the default execution provider before every harness is fully managed. The default Agent Profile may remain the proven Codex profile while additional harnesses progress through support classifications.

A new harness becoming available does not change existing workflow defaults.

## 24. Codex continuity and preservation contract

### 24.1 Existing assets remain authoritative

The current Codex lane maps into the generic design as follows:

| Existing Codex asset | Generic platform role |
| --- | --- |
| `external/omnigent` | Stable top-level execution identity |
| `codex-native-ui` | Upstream agent identity |
| `codex-native` | Harness catalog identity |
| Codex OpenAI OAuth Provider Profile | Provider account and capacity authority |
| `codex_auth_volume` and generation | `codex-oauth-home@1` materializer state |
| `omnigent-host-codex` Compose service | Existing static Host Class realization |
| Current on-demand Codex container path | Existing on-demand Host Class realization |
| `codex-on-demand@1` and `codex-static@1` | Existing launch policy versions |
| `profile_bound_execution.py` | Initial registered execution realizer |
| bridge, checkpoint, publication, cleanup, and janitor code | Shared lifecycle implementation |
| Codex support and cutover matrices | Required support evidence |

### 24.2 No big-bang dependency

The generic catalog and planner may be introduced while Codex still uses `codex-profile-bound@1`.

A representative Codex plan is:

```yaml
harnessId: codex-native
agentId: codex-native-ui
credentialBindings:
  primary-model:
    providerProfileRef: codex_openai_oauth
    materializerRef: codex-oauth-home@1
hostClassRef: omnigent-codex-current@1
launchPolicyRef: codex-on-demand@1
executionRealizerRef: codex-profile-bound@1
```

The realizer delegates to the existing coordinator and scripts. New harnesses may use `generic-omnigent-host@1` at the same time.

This coexistence is deterministic planning, not runtime fallback accumulation.

### 24.3 Stable persisted identity

Existing Codex workflow snapshots, Agent Profile versions, Provider Profile ids, policy refs, bridge records, checkpoint refs, and Temporal histories remain readable and replayable.

New generic fields are additive or compiled from existing immutable snapshots. Historical records are not rewritten to claim that they used a generic realizer.

### 24.4 Parity before reassignment

Codex may move from `codex-profile-bound@1` to a generic realizer only when the generic realizer proves the existing Codex support matrix for the same host modes, images, architectures, profile versions, policy versions, and lifecycle requirements.

A reassignment changes only new plans. Existing plans retain their realizer ref.

### 24.5 Rollback

Rollback changes the selected realizer for future eligible Codex plans. It does not mutate existing session, plan, checkpoint, or support evidence.

The current Codex lane remains available until its existing cutover and retirement contracts permit removal.

## 25. OpenCode Go example

OpenCode Go is one composition of the generic system:

```yaml
agentProfile:
  source:
    upstreamId: opencode-native-ui
  harness:
    id: opencode-native
  credentialSlots:
    - id: primary-model
      acceptedProviderIds:
        - opencode

providerProfile:
  profileId: opencode-go-default
  providerId: opencode
  credentialSource: secret_ref
  secretRefs:
    api_key: secret://...

credentialBindingSet:
  primary-model:
    providerProfileRef: opencode-go-default
    materializerRef: opencode-auth-json@1

hostClassRef: omnigent-native-standard@3
launchPolicyRef: omnigent-on-demand@1
executionRealizerRef: generic-omnigent-host@1
```

The OpenCode materializer writes a protected, plan-owned auth file, validates the live model catalog, and removes its state before Provider Profile lease release.

This addition requires no new top-level agent, Temporal workflow, or harness-named branch in the lifecycle coordinator.

## 26. Extension boundary

### 26.1 Upstream harness metadata

MoonMind consumes upstream capability and setup metadata. When upstream metadata is insufficient for automatic credentials or host selection, MoonMind uses an approved companion descriptor keyed by canonical harness id and package version.

The companion descriptor may declare:

- credential slots.
- accepted materializer classes.
- host features.
- required binaries and services.
- mutable state paths.
- validation probes.
- known conformance limitations.

It cannot declare secret values, arbitrary mounts, Docker authority, or policy exceptions.

### 26.2 Community plugins

A community plugin is launchable only when:

- its package and version are approved.
- its catalog contribution is stable and conflict-free.
- a compatible Host Class pins the plugin artifact.
- every credential slot uses an approved materializer.
- its required capabilities can be enforced.
- its support classification permits the requested workflow.

An unapproved plugin remains visible as quarantined.

## 27. Failure taxonomy

The platform uses typed low-cardinality failures, including:

```text
OMNIGENT_HARNESS_CATALOG_UNAVAILABLE
OMNIGENT_HARNESS_CATALOG_STALE
OMNIGENT_HARNESS_UNKNOWN
OMNIGENT_HARNESS_UNTRUSTED
OMNIGENT_AGENT_IDENTITY_UNAVAILABLE
OMNIGENT_CAPABILITY_REQUIRED_UNSUPPORTED
OMNIGENT_CAPABILITY_REQUIRED_UNKNOWN
OMNIGENT_CREDENTIAL_SLOT_UNBOUND
OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE
OMNIGENT_CREDENTIAL_MATERIALIZER_UNAVAILABLE
OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED
OMNIGENT_HOST_CLASS_UNAVAILABLE
OMNIGENT_HOST_HARNESS_NOT_READY
OMNIGENT_MODEL_UNAVAILABLE
OMNIGENT_LAUNCH_POLICY_INCOMPATIBLE
OMNIGENT_EXECUTION_PLAN_CONFLICT
OMNIGENT_EXECUTION_REALIZER_UNAVAILABLE
OMNIGENT_CLEANUP_DEFERRED
```

Diagnostics name the failed boundary and an actionable remediation. They do not parse vendor log text as authority.

## 28. Rejected alternatives

### 28.1 One MoonMind runtime per harness

Rejected because it duplicates selection, credentials, host lifecycle, recovery, and evidence code and makes Omnigent a transport detail rather than the primary harness provider.

### 28.2 Big-bang replacement of the Codex lane

Rejected because the existing Codex path contains substantial verified authority and recovery behavior. Replacing it before generic parity would increase risk and delay use of new harnesses.

### 28.3 One universal host image

Rejected because harness dependencies, release cadence, size, authentication, and architecture support differ. Host Classes provide bounded composition.

### 28.4 Trust upstream declarations alone

Rejected because capability declarations do not prove MoonMind policy enforcement, cleanup, secret containment, or live behavior.

### 28.5 Workflow-time software installation

Rejected for supported production execution because mutable installation breaks image authority, reproducibility, egress policy, and conformance evidence.

### 28.6 Silent fallback to another harness

Rejected because it changes credentials, billing, model behavior, continuation semantics, and evidence authority.

## 29. Acceptance criteria

The design is realized when:

1. MoonMind projects the selected Omnigent endpoint's harness catalog without a Codex-only allowlist.
2. Every catalog row has a trust and support classification.
3. Agent Profiles pin a canonical harness and catalog snapshot.
4. Required and preferred capabilities are negotiated before lease acquisition.
5. Provider Profiles remain the single account-capacity and cooldown authority.
6. Credential materializers are versioned, allowlisted, secret-safe, and cleanup-aware.
7. Host Classes are immutable and live host readiness is checked for the exact harness.
8. Launch policies no longer require harness-named runtime branches.
9. Every run persists one secret-free execution plan.
10. The fenced Omnigent control plane owns the session and side-effect journal.
11. Adding an approved harness does not require a new branch in the generic lifecycle coordinator.
12. Unknown community harnesses receive no credentials or workflow authority.
13. Existing Codex workflows continue to run through the current realizer without reduced behavior.
14. Existing Codex histories, checkpoints, and evidence remain readable.
15. The generic realizer can run at least one non-Codex own-auth harness and one different integration class.
16. OpenCode Go can run through `opencode-native` with managed credential materialization.
17. Cancellation, cleanup, and janitor recovery are proven for generic hosts.
18. Codex moves to the generic realizer only after the existing Codex support matrix passes for that realizer.
19. Omnigent can be the preselected execution provider while Codex remains the default proven Agent Profile.
20. Direct runtimes remain available only according to their existing rollback and retirement contracts.

## 30. Document authority and future promotion

This design owns the target generic harness-platform model.

The current Codex-specific documents remain authoritative for the existing Codex specialization and its support state. This design extends them. It does not silently supersede their current guarantees.

When the generic platform is implemented, its settled architecture must be promoted into the appropriate Omnigent module architecture and contract documents. This design is then marked Implemented and superseded or removed according to the documentation architecture standard.
