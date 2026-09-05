# Omnigent Harness Platform Design

**Status:** Proposed  
**Document Class:** System / Feature Design View  
**Owners:** MoonMind Platform  
**Last updated:** 2026-08-20  
**Authority:** Desired-state architecture for making Omnigent the primary harness-wrapper provider for MoonMind while preserving the existing Codex product path until a generic realization proves equivalent support.

## Related documents

- [`docs/Omnigent/OmnigentModuleArchitecture.md`](./OmnigentModuleArchitecture.md)
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
- [`docs/Omnigent/OmnigentHostMountedTools.md`](./OmnigentHostMountedTools.md)
- [`docs/Security/ProviderProfiles.md`](../Security/ProviderProfiles.md)
- [`docs/Security/SecretsSystem.md`](../Security/SecretsSystem.md)
- [`docs/Steps/SkillSystem.md`](../Steps/SkillSystem.md)
- [`docs/Temporal/ManagedAndExternalAgentExecutionModel.md`](../Temporal/ManagedAndExternalAgentExecutionModel.md)
- [`docs/Workflows/WorkspaceLocators.md`](../Workflows/WorkspaceLocators.md)
- [`docs/Workflows/CheckpointBranchSystem.md`](../Workflows/CheckpointBranchSystem.md)
- [`docs/Workflows/WorkflowRemediation.md`](../Workflows/WorkflowRemediation.md)

## Advance organizer

**One sentence:** MoonMind treats Omnigent as one stable external execution provider whose dynamically discovered harnesses are admitted through immutable agent, capability, credential, host, policy, Skill, and conformance contracts rather than through one MoonMind runtime implementation per harness.

**One paragraph:** A workflow selects a versioned Omnigent Agent Profile. MoonMind resolves the exact agent source, pinned Omnigent harness build, resolved Skill snapshot, versioned credential-binding set, compatible Provider Profiles, approved credential materializers, immutable Host Class, launch policy, model configuration, and execution realizer. It compiles the pre-host decisions into one secret-free execution-plan payload before acquiring provider leases or mutating a host. After lease acquisition and host realization, a separate fenced runtime binding records the acquired credential generations, exact-host harness attestation, live model validation, workspace resolution, session identity, and cleanup authority. The current Codex profile-bound lane remains a valid explicit realizer and continues to serve Codex without reduced behavior while the generic realizer matures.

**Full description:** MoonMind owns durable workflow orchestration, authority, credentials, workspace preparation, Skills, publication, evidence, recovery, and cleanup. Omnigent owns provider sessions, runners, harness execution, and the live harness protocol. The platform discovers what an Omnigent endpoint understands, admits a Host Class using immutable class-level evidence, and later verifies what the exact selected host actually contains. Discovery never grants trust. Class-level admission never substitutes for exact-host proof. A harness combination becomes launchable only when its exact implementation is trusted, its agent source is immutable, every required credential slot is safely materializable, its model configuration is valid, its resolved Skills are pinned, an exact host proves the selected harness build and required capabilities, and MoonMind can enforce the requested policy. Support is proven for the exact model and execution realizer, not inferred from code presence or a harness name.

## 1. Purpose

MoonMind is moving toward Omnigent as the primary wrapper around coding-agent harnesses. The target is not a collection of separate MoonMind runtimes named after every agent. The target is one Omnigent execution platform that can run any trusted and realizable harness reported by the selected Omnigent endpoint.

This design generalizes the existing Codex-through-Omnigent system. It does not discard that work. The current Codex lane supplies the reference implementation for Agent Profile selection, Provider Profile leasing, OAuth-generation fencing, host leasing, workspace materialization, resolved Skill delivery, bridge authorization, session execution, publication, checkpoint evidence, cleanup, and support qualification.

The design owns the generic target architecture. The existing Codex documents continue to own the current Codex specialization until a later evidence-backed cutover explicitly moves that specialization onto the generic realizer. A conflict is resolved without weakening the current Codex contract.

## 2. Goals

The platform has the following goals:

1. **One top-level Omnigent identity.** Every harness executes through `agentKind=external`, `agentId=omnigent`.
2. **Catalog-driven harness support.** MoonMind discovers harness identities and declared capabilities from Omnigent instead of keeping a hand-maintained runtime list.
3. **Exact implementation identity.** Trust, launch, and support bind the harness package, plugin, Omnigent build, vendor runtime, and content digests rather than a canonical harness name alone.
4. **No coordinator branch per harness.** Adding an approved harness is primarily a catalog, materializer, Host Class, policy, Agent Profile, and conformance operation.
5. **Preserve the Codex path.** Existing Codex identities, Provider Profiles, OAuth volumes, host services, lifecycle evidence, support gates, and rollback behavior remain usable while the generic system develops.
6. **Two-stage capability proof.** Pre-host admission uses catalog and Host Class evidence. Exact-host capabilities are validated after host realization and before runner or session creation.
7. **Provider-account capacity across harnesses.** A provider account, quota, cooldown, or mutable credential home remains one shared authority even when several harnesses can use it.
8. **Immutable Skills.** Agent Profile Skill intent resolves to one per-run Skill snapshot and delivery descriptor before plan commitment.
9. **Safe extension.** Built-in and community harnesses can be discovered without granting an untrusted plugin secrets, host authority, or production support.
10. **Local-first operation.** Docker Compose remains the normal local deployment. On-demand hosts add no permanent container footprint.
11. **Objective support claims.** A combination is supported only when evidence proves its exact model configuration and execution realizer.
12. **Durable recovery.** Retry, continuation, branch, remediation, cancellation, credential rotation, and janitor behavior use the same fenced authority model for every harness.

## 3. Non-goals

This design does not:

- fork the Omnigent host or runner protocol.
- recreate provider agents inside MoonMind.
- claim that every discovered harness is immediately production-supported.
- install arbitrary harness software during an ordinary production workflow launch.
- permit a workflow to supply raw Docker options, host paths, volume names, host ids, credential generations, or credentials.
- remove the direct Codex fallback or the current Codex-through-Omnigent lane before their existing retirement contracts permit it.
- require one universal host image containing every possible harness.
- treat a Host Class declaration as proof that an exact host is ready.
- treat an upstream capability declaration as proof that MoonMind can safely enforce the capability.
- replace the Omnigent control-plane aggregate and fencing designs with a second session authority.
- put mutable Skill source content or Skill bodies in the execution plan.
- make display names, aliases, or provider marketing names durable identity.

## 4. Governing decisions

### 4.1 Stable execution identity

The canonical workflow identity remains:

```text
agentKind = external
agentId   = omnigent
```

Harness, agent source, endpoint, model, Provider Profile, Host Class, policy, realizer, host, and session selections are nested authority. MoonMind does not add top-level identities such as `omnigent_opencode`, `omnigent_qwen`, or `omnigent_claude`.

### 4.2 MoonMind owns authority

MoonMind owns:

- workflow and Step ordering.
- immutable Agent Profile selection.
- immutable agent-source and bundle identity.
- resolved Skill selection and delivery.
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
- agent imports and stable upstream agent identity.
- host and runner protocol.
- live provider session behavior.
- harness-specific message, tool, elicitation, and terminal behavior.
- harness-native resume and fork mechanisms.

No Omnigent catalog row, agent bundle, host claim, setup operation, or plugin declaration can broaden MoonMind authority.

### 4.3 Discovery is not trust

MoonMind distinguishes:

```text
discovered
  != trusted
  != installed
  != authenticated
  != class-admissible
  != exact-host-attested
  != launchable
  != supported
```

A discovered harness may be visible in Settings while remaining non-launchable. A trusted harness may still lack a compatible Host Class. A class-admissible on-demand combination may still fail exact-host attestation after the container starts. A launchable combination may remain experimental until conformance evidence proves support.

### 4.4 Current Codex is a conforming specialization

The existing Codex profile-bound implementation is the first registered execution realizer. The generic platform can compile a Codex execution plan that delegates to the existing implementation without changing its observable contract.

The existing path is not an emergency fallback selected after a generic failure. It is an explicit, versioned realization selected before launch. A failed explicit generic selection never silently switches to Codex.

### 4.5 Plans and runtime bindings are separate authority

A launch has two immutable authority objects:

1. `OmnigentExecutionPlanPayload` records decisions that can be made before provider leases and an exact host exist.
2. `OmnigentRuntimeBinding` records the resources and generations actually acquired or attested after plan commitment.

The plan never pretends to know an exact host or a leased credential generation before those exist. The runtime binding cannot change plan decisions such as harness, agent source, Provider Profile, materializer, Host Class, policy, model configuration, Skills, or realizer.

One immutable plan may govern multiple execution realizations. Each rerun, linked continuation, and recurring occurrence owns a distinct runtime-binding aggregate identified by `(planRef, executionScopeRef)`. Activity retries within that execution scope reconcile the same aggregate; they do not create a second live owner. The digest-addressed `runtimeBindingRef`, revision, and fencing generation advance when acquired or attested authority is replaced.

Before creating a new runtime binding, admission verifies that the mutable
Omnigent endpoint still exposes the exact server build recorded by the plan's
support identity. A mismatch stops before provider leases or host launch; it
never rewrites the plan or substitutes a host. Exact rerun admission applies
the same check and returns an actionable `edit_for_rerun` adaptation so the
ordinary authoring/compiler boundary can produce fresh runtime authority from
reviewed task input. Activity retries inside an already-owned runtime binding
continue to reconcile that binding rather than re-selecting deployment state.

### 4.6 Plan digests are not self-referential

The canonical plan payload does not contain its own digest. MoonMind canonicalizes and hashes `OmnigentExecutionPlanPayload`, then stores it in an envelope:

```yaml
schemaVersion: moonmind.omnigent-execution-plan-envelope.v1
planRef: omnigent-execution-plan:sha256:...
payload: <canonical OmnigentExecutionPlanPayload>
```

`planRef` is computed only from canonical `payload` bytes. It is never part of those bytes. Recomputing the digest from the payload must reproduce the envelope ref exactly.

### 4.7 Capability proof is two-stage

Before lease acquisition, the planner computes a class-level admission decision from workflow requirements, Agent Profile requirements, the pinned harness catalog, approved materializers, Host Class declarations, bridge capabilities, launch policy, and support policy.

After host realization, a fenced verifier computes an exact-host capability attestation from the live host and compares it with the plan. Runner and session creation are forbidden until this second decision passes.

### 4.8 Support is combination-specific

Support applies to the digest of an exact support combination:

```text
Omnigent server and host build
+ harness implementation package/version/digest
+ vendor runtime or CLI version/digest when applicable
+ agent source identity and bundle digest when applicable
+ credential materializer versions
+ Provider Profile compatibility class
+ Host Class, host image, and architecture
+ launch policy version
+ exact normalized model configuration digest
+ execution realizer version
+ required capability set
```

Two runs that differ by selected model, normalized model options, reasoning effort, or `executionRealizerRef` have different support keys. Evidence for one does not qualify the other.

## 5. System topology

```text
MoonMind workflow or Step
  -> immutable Omnigent Agent Profile snapshot
  -> immutable agent-source resolution
  -> immutable ResolvedSkillSet and delivery descriptor
  -> Omnigent Execution Planner
       -> pinned harness catalog and trust record
       -> versioned credential-binding set
       -> Provider Profile compatibility
       -> credential materializer registry
       -> Host Class registry
       -> launch policy and MoonMind capability policy
       -> class-level admission decision
  -> digest-addressed OmnigentExecutionPlan envelope
  -> fenced Omnigent control plane
       -> Provider Profile leases and acquired generations
       -> host binding and host lease
       -> workspace materialization
       -> credential materialization
       -> exact-host harness-build attestation
       -> exact-host capability and model validation
       -> immutable OmnigentRuntimeBinding
  -> stock Omnigent server and host protocol
       -> runner
       -> selected harness
       -> selected upstream agent or imported bundle
  -> MoonMind bridge, evidence, publication, checkpoint, and cleanup
```

The planner is pure with respect to provider side effects. The realizer owns side effects through Activities and trusted services. Temporal remains the durable orchestration authority.

## 6. Identity model

The following identities are distinct:

| Identity | Purpose |
| --- | --- |
| `endpointRef` | Selected Omnigent control plane |
| `harnessCatalogRef` | Immutable observed harness catalog snapshot |
| `harnessImplementationRef` | Exact trusted core/plugin implementation package, version, and digest |
| `harnessId` | Canonical Omnigent harness identifier |
| `agentProfileSnapshotRef` | MoonMind-owned immutable Agent Profile version and digest |
| `agentSourceRef` | Immutable upstream or artifact-backed agent-source identity |
| `resolvedSkillSetRef` | Immutable per-run resolved Skill snapshot |
| `skillDeliveryRef` | Immutable metadata describing how the Skill snapshot is delivered to the selected host |
| `providerProfileRef` | Provider account, credential, capacity, and cooldown authority |
| `credentialMaterializerRef` | Approved method for presenting one Provider Profile to the harness |
| `credentialBindingSetRef` | Immutable binding-set id, version, and digest |
| `hostClassRef` | Immutable host environment and image contract |
| `launchPolicyRef` | Host mode, limits, network, capture, cleanup, and control policy |
| `modelConfigDigest` | Digest of qualified model id, effort, route, and normalized options |
| `executionRealizerRef` | Versioned implementation that realizes the plan |
| `executionPlanRef` | Digest of the canonical pre-host plan payload |
| `executionScopeRef` | Durable Workflow/execution identity that owns one live binding aggregate |
| `runtimeBindingRef` | Digest-addressed acquired-generation and exact-host binding |
| `hostBindingRef` and `hostLeaseRef` | Durable host ownership |
| `providerLeaseRef` | Durable provider-account capacity ownership |
| `omnigentHostId` | Exact host reported by Omnigent after realization |
| `omnigentSessionId` | Exact provider session |
| `chatBindingId` | Opaque MoonMind native Workflow Chat identity |
| `supportCombinationKey` | Digest identifying the exact combination qualified by support evidence |

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
/v1/hosts     -> which connected hosts exist and their current readiness
```

The first two views provide pre-host planning evidence. The third provides live evidence only for already-existing connected hosts. It cannot stand in for a not-yet-created on-demand host.

### 7.2 Catalog snapshot

A snapshot has this logical shape:

```yaml
schemaVersion: moonmind.omnigent-harness-catalog.v1
endpointRef: default
omnigentVersion: "<semver>"
omnigentBuildDigest: sha256:...
observedAt: 2026-08-20T06:00:00Z
sourceDigest: sha256:...
catalogRef: omnigent-harness-catalog:sha256:...
pluginLoadErrors: []
harnesses:
  - id: opencode-native
    aliases:
      - opencode
      - native-opencode
    label: OpenCode
    implementation:
      sourceKind: core
      package: omnigent
      version: "<semver>"
      digest: sha256:...
      pluginEntryPoint: null
    runtimeRequirements:
      binaries:
        - name: opencode
          versionConstraint: ">=1.17.7,<1.19.0"
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

Each canonical harness implementation has one trust state:

- `core_trusted`: shipped by the approved Omnigent distribution and bound to its build digest.
- `plugin_approved`: supplied by an explicitly approved plugin package, version, entry point, and digest.
- `quarantined`: discovered but not approved to receive credentials or execute workflows.
- `blocked`: explicitly denied by policy or a security finding.

Trust is bound to implementation identity. A plugin name or canonical harness id alone is insufficient.

### 7.4 Freshness

Catalog snapshots are immutable. New plans retain the Agent Profile's pinned catalog as authority and require a current observation that attests the same endpoint, Omnigent build, and harness implementation. The current observation supplies freshness only; it never replaces the pinned authority. Historical plans retain their original snapshot ref.

An endpoint outage may retain the last known catalog for diagnostics. It does not silently make stale harnesses launchable.

## 8. Exact-host harness attestation

### 8.1 Purpose

A Host Class says what an image or connected-host class is expected to contain. It does not prove what the exact selected host contains at launch time.

Before runner or session creation, the selected host must publish or make available a bounded `HostHarnessAttestation` for the exact harness:

```yaml
schemaVersion: moonmind.omnigent-host-harness-attestation.v1
hostId: host_...
hostClassRef: omnigent-native-standard@3
hostImageRef: ghcr.io/example/omnigent-host@sha256:...
omnigentVersion: "<semver>"
omnigentBuildDigest: sha256:...
harnessId: opencode-native
harnessImplementation:
  package: omnigent
  version: "<semver>"
  digest: sha256:...
  pluginEntryPoint: null
runtimeDependencies:
  - name: opencode
    version: 1.18.11
    digest: sha256:...
configured: true
capabilities:
  interrupt: true
  streaming: true
observedAt: 2026-08-20T06:05:00Z
attestationRef: artifact:...
```

The attestation carries no credentials or unbounded environment data.

### 8.2 Match rule

The exact host passes only when:

- `hostClassRef`, image digest, architecture, and Omnigent build match the plan.
- the canonical harness id matches.
- the harness implementation package, version, entry point, and digest match the trusted catalog record.
- every required vendor runtime or CLI satisfies the pinned or policy-allowed version and digest rule.
- every required exact-host capability is positively reported.
- the attestation is fresh and belongs to the current host-lease fencing generation.

Readiness by canonical harness id alone is insufficient.

### 8.3 Protocol compatibility

MoonMind prefers an upstream Omnigent host-protocol or read-only host endpoint that exposes this safe attestation. Until upstream exposes all fields, a Host Class may use an approved MoonMind verifier at the trusted host-realization boundary. The verifier may inspect the immutable image and exact runtime binaries, but it does not alter Omnigent runner behavior or invent a second host protocol.

A connected static host that changes its plugin or vendor runtime invalidates prior attestation. New sessions fail closed until the new implementation is trusted and requalified.

## 9. Omnigent Agent Profiles

### 9.1 Role

An Omnigent Agent Profile is the reusable MoonMind selection surface. It describes the desired agent source, harness, capabilities, model, workspace, Skills, tools, capture, continuation, and publication behavior.

It does not own credentials, acquired credential generations, raw host authority, or secret material.

The profile model and effort are defaults when a workflow omits those runtime fields. An explicit workflow runtime model or effort remains authoritative through execution-plan compilation; the compiled plan and Temporal parameters must record the same resolved selection.

### 9.2 Discriminated agent source

An Agent Profile v2 uses exactly one source variant.

A stock or pre-existing upstream agent uses:

```yaml
source:
  kind: upstream
  upstreamId: opencode-native-ui
  upstreamVersion: "<agent-version>"
  upstreamSnapshotDigest: sha256:...
```

An imported, custom, or MoonMind-generated bundle uses:

```yaml
source:
  kind: bundle
  bundleArtifactRef: artifact:...
  bundleDigest: sha256:...
  importReceiptRef: omnigent-agent-import:...
  importedAgentId: moonmind-opencode-default
  importedAgentVersion: "<upstream-version>"
  importedContentDigest: sha256:...
```

The bundle variant pins both MoonMind artifact authority and the resulting upstream import identity. A historical plan can therefore detect replacement or conflicting content under the same upstream id.

### 9.3 Versioned document

The desired document shape is:

```yaml
schemaVersion: moonmind.omnigent-agent-profile.v2
endpointRef: default

source:
  kind: upstream
  upstreamId: opencode-native-ui
  upstreamVersion: "<agent-version>"
  upstreamSnapshotDigest: sha256:...

harness:
  id: opencode-native
  catalogRef: omnigent-harness-catalog:sha256:...
  implementationRef: omnigent-harness-implementation:sha256:...

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

### 9.4 Compatibility with existing profiles

Existing v1 Agent Profile versions remain immutable and replayable. Their source, `harness`, `providerRequirements`, execution profile, policy, Skill intent, and model fields compile through a compatibility decoder into the generic planning inputs.

New profile versions may continue to use the current v1 form until the v2 authoring surface is available. The compatibility decoder is required for historical and in-flight authority. It is not a second mutable profile system.

### 9.5 Agent templates

A trusted harness may use:

- a stock upstream agent.
- a user-selected upstream agent.
- an immutable imported bundle.
- a MoonMind-generated portable default bundle.

A deterministic template factory may generate a minimal agent bundle for an approved harness. The generated bundle is validated, stored as an artifact, imported through the authenticated Omnigent boundary, and represented with the bundle source variant.

The template factory adapts only structural harness needs. It does not duplicate provider-agent semantics.

## 10. Resolved Skills and delivery

### 10.1 Skill intent is not executable authority

The `skills` list in an Agent Profile identifies desired Skill names and constraints. It is not the executable Skill content. Repository, deployment-managed, and local-overlay sources can change independently of the Agent Profile.

Before plan commitment, MoonMind resolves Skill intent through the canonical Skill System into one immutable per-run snapshot.

### 10.2 Plan references

The execution plan carries compact references only:

```yaml
resolvedSkills:
  resolvedSkillSetRef: artifact:...
  resolvedSkillSetDigest: sha256:...
  skillDeliveryRef: skill-delivery:sha256:...
```

`resolvedSkillSetRef` identifies the immutable Skill bundle. `skillDeliveryRef` identifies the normalized materialization metadata needed to expose the same bundle through `$MOONMIND_ACTIVE_SKILLS_DIR` and any safe convenience aliases.

Skill bodies, source repositories, local paths, and unbounded instructions do not enter the plan or Temporal history.

### 10.3 Retry and continuation

A retry reuses the same resolved Skill refs. It does not re-resolve mutable Skill sources.

A branch or remediation run may select and resolve a new Skill snapshot only through a new explicit execution plan. The lineage records both the prior and replacement Skill refs.

## 11. Provider Profiles and credential-binding sets

### 11.1 Provider Profile continuity

`ManagedAgentProviderProfile` remains the durable authority for provider account selection, credentials, current generation, concurrency, cooldown, enabled state, and readiness.

The generic platform does not create a parallel account-profile system. Existing Codex and Claude profiles remain valid.

Runtime-specific fields in an existing profile are interpreted as compatibility constraints for the selected credential materializer. They do not require immediate destructive migration.

### 11.2 Credential slots

An Agent Profile names the credential roles required by the harness. A role may be optional.

Examples include:

```text
primary-model
secondary-model
embedding-provider
vendor-login
```

Repository and publication credentials remain capability-scoped MoonMind credentials. They are not model-provider slots.

### 11.3 Versioned credential-binding set

A binding set has a stable id and immutable versions. Each version stores canonical JSON and a digest:

```yaml
schemaVersion: moonmind.omnigent-credential-bindings.v1
bindingSetId: opencode-go-primary
version: 3
digest: sha256:...
bindings:
  primary-model:
    providerProfileRef: opencode-go-default
    materializerRef: opencode-auth-json@1
```

The authoritative ref includes all three identity parts:

```text
omnigent-credential-bindings:opencode-go-primary@3#sha256:<digest>
```

Plans and evidence carry that exact ref. Editing a binding set appends a new version and never changes historical authority.

### 11.4 Credential generation ownership

The plan selects a Provider Profile and materializer. It does not select or record a credential generation before lease acquisition.

After the Provider Profile lease is acquired, the execution-scoped runtime binding records the exact acquired generation. This rule handles rotation safely:

- rotation before the first lease acquisition is allowed because no run has acquired a generation yet.
- an Activity retry reuses the recorded lease and generation when the acquisition authority is unchanged.
- when the Provider Profile manager returns a newly acquired lease or generation for the same execution scope, the store compare-and-swaps a replacement binding revision, advances its fencing generation, and clears superseded host/session authority before any new provider mutation.
- rotation after host or session realization requires the credential-maintenance lane to drain or fence the bound consumer; a former host, lease owner, cleanup worker, or janitor cannot write through the replacement fence.
- reruns, linked continuations, and recurring occurrences reuse the immutable plan but acquire independent execution-scoped bindings.

A generation mismatch after binding produces a typed fenced outcome and reconciliation. It never causes plan mutation or selection of another Provider Profile.

### 11.5 Capacity

#### Four independently governed layers

Operators reason about four distinct limits. They are named separately because they are owned by different parties, changed for different reasons, and surfaced separately when a run waits:

| Layer | Owner | What it means | Where it is set |
| --- | --- | --- | --- |
| **Configured profile capacity** | Operator | The ceiling this Provider Profile may ever admit. Never lowered by runtime behavior. | `max_parallel_runs` on the Provider Profile |
| **Effective provider capacity** | Runtime | The limit currently applied, at or below the ceiling. Lowered by adaptive rate-limit backpressure or operator policy, and restored toward the ceiling as the provider recovers. | Derived; reported as `effective_capacity` |
| **Host capacity** | Deployment | How many on-demand generic hosts the machine may carry at once, plus a separately bounded cold-launch rate. | `MOONMIND_OMNIGENT_GENERIC_HOST_CAPACITY`, `MOONMIND_OMNIGENT_GENERIC_HOST_COLD_LAUNCH_BURST`, `MOONMIND_OMNIGENT_GENERIC_HOST_COLD_LAUNCH_WINDOW_SECONDS` |
| **Worker capacity** | Deployment | How many Activities one worker fleet executes concurrently. | `TEMPORAL_AGENT_RUNTIME_WORKER_CONCURRENCY` |

Effective concurrency is the minimum of all four:

```text
min(
  configured profile capacity,
  effective provider capacity,
  available generic-host capacity,
  available Temporal execution capacity
)
```

A configured ceiling of `N` is not a promise of `N` concurrent runs. It is a promise that nothing above `N` is admitted. Any of the other three layers may hold the run lower, and readiness must name which one did.

#### Configured versus effective

Raising or lowering the configured ceiling is an operator action and is always safe while work is running: reduction stops new admission and never evicts an active workflow, and admission resumes once usage falls below the new limit. Lowering the *effective* limit is a runtime action — for example, halving admission after a provider rate-limit report — and it never edits the operator's configured value or discards queued work. Queued work simply waits for one of the remaining admitted slots.

#### Waiting

Work above the effective limit waits as durable workflow state with an explicit reason. It does not occupy a long-running execution Activity slot, silently fall back to another runtime, profile, model, or Host Class, or launch a host the machine cannot carry.

#### Admission sequence

Provider capacity and host capacity are admitted in one documented order, by the launching AgentRun workflow, before the long execution Activity is scheduled:

1. **Request provider capacity.** The workflow queues with the ProviderProfileManager for the exact Provider Profile the committed plan selected, and waits as durable workflow state. The workflow — not the Activity — is the lease owner.

   The committed plan reference the whole hand-off is fenced on is read from the request's execution-plan binding — the same authority that selects this lane and that the Activity loads its plan from. An optional workflow-authored `executionPlanRef` parameter may also be present; when both are, they must name the same plan, and a disagreement is a plan-substitution conflict that fails closed rather than picking one.
2. **Admit host capacity.** With provider capacity held, the workflow polls a short control Activity that evaluates the aggregate host ceiling and the cold-launch rate against the durable host-lease ledger, and it waits on a workflow timer between polls. The poll names the run's stable host-lease identity (execution plan, request idempotency key, Host Class), so whether this run already holds a reservation is read from the ledger rather than from a caller-supplied flag.
3. **Release on failure.** If host capacity cannot be admitted, provider capacity is released immediately. Provisional ownership is bounded: a run never occupies a provider lease indefinitely while waiting for a machine.
4. **Execute.** The Activity receives a compact, secret-free admitted-capacity ticket and *consumes* it by inspection. It never calls an acquiring client, so it can neither grant new capacity nor wait for a replacement inside the execution slot.
5. **Release last.** Provider capacity is released by its owner after the Activity's host, session, credential, and workspace cleanup has completed.

Three waits are bounded and reported separately, because they mean different things to an operator: the provider queue wait and the host wait are durable workflow state outside any Activity, and the hand-off from "admitted" to "an execution worker started this" gets its own ScheduleToClose allowance so worker-queue time is never charged to the run's execution budget.

#### Admitted-capacity ticket

The ticket binds the whole authority the Activity must positively establish: the committed execution plan, the AgentRun workflow and run ids, the step execution and request identity, the selected Provider Profiles with their capacity scopes, the credential generation observed at admission, and a monotonic admission epoch. It carries identities only; credentials are resolved in Activities.

The ProviderProfileManager records the same plan, step, request and credential-generation identity on the durable lease row, so the fence survives a manager restart. Consumption fails closed — before any host or credential side effect — when the inspection is empty or malformed, reports no active lease, or names another profile, owner, plan, step or request, or when the lease has expired or the credential generation has rotated. Absence of evidence is never acceptance.

A failure of this kind is recoverable, not terminal: the run returns to durable waiting under the same owner and is re-admitted with a bumped epoch, bounded by a small number of attempts. Nothing leaks, because provider capacity is already released when the failure is observed, and no duplicate host is created, because the host lease is keyed by the run's stable runtime binding.

#### Multi-profile plans

The ProviderProfileManager ledger holds at most one lease per requester workflow, so a workflow-owned all-required admission across several Provider Profiles cannot be expressed without a second capacity ledger. An execution plan that selects more than one Provider Profile is therefore **rejected before execution** on this path, with an actionable typed error. It is never silently routed back to Activity-side queueing. Histories recorded before pre-Activity admission keep their classified Activity acquisition: the rejection carries its own workflow patch marker, so a retained history replays onto the Activity-owned execution it already scheduled while every new run is rejected before execution.

#### Worker lanes

The agent-runtime fleet polls two task queues from one worker process, each with its own activity budget:

| Lane | Task queue | What runs there |
| --- | --- | --- |
| **Execution** | `TEMPORAL_ACTIVITY_AGENT_RUNTIME_TASK_QUEUE` | Long turn execution and the Activities that drive a live turn. |
| **Control, liveness and cleanup** | `TEMPORAL_ACTIVITY_AGENT_RUNTIME_CONTROL_TASK_QUEUE` | Capacity admission, cancellation, host-lease heartbeats, session stop, host stop, lease release, and host reclamation. |

Saturating the execution lane at the deployment's configured concurrency therefore cannot starve cancellation, liveness, or the cleanup that releases the capacity queued runs are waiting for. Configuring both lanes to the same queue removes that guarantee and is rejected when the activity catalog is built. The lanes are queues, not services: they add no always-on container.

#### Lease purposes

Not every lease consumes an execution slot:

- **Shared execution** leases count against the effective provider capacity.
- **Single-flight validation** leases apply to credentialless profiles, which own no shared mutable authentication state. One holder per exact evidence identity is sufficient, so these consume no execution slot and impose no capacity-one requirement.
- **Exclusive credential maintenance** leases block new consumers and wait for every existing credential consumer to drain.

Mutable OAuth-home profiles remain capacity one. Their validation is exclusive maintenance, not single-flight, because a probe and a run would share the same authentication home.

All Provider Profile leases for one execution are acquired in deterministic order by Provider Profile id. They are released in reverse order after host and credential cleanup, and provider capacity is released last.

A provider cooldown applies across every harness using the same Provider Profile. Switching harnesses does not evade quota or cooldown authority.

## 12. Credential materializer registry

### 12.1 Purpose

A credential materializer is the trusted boundary that turns a leased Provider Profile and acquired generation into runtime state a harness can consume.

The harness plugin may describe what it needs. MoonMind decides whether a materializer is trusted, which secret roles it may resolve, which target paths are allowed, which host modes may use it, and how cleanup works.

### 12.2 Materializer contract

A materializer declares:

```yaml
materializerId: opencode-auth-json
version: 1
acceptedHarnessImplementations:
  - omnigent-harness-implementation:sha256:...
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

### 12.3 Materializer outputs

Materialization returns a secret-free handle:

```yaml
credentialRuntimeRef: credential-runtime:...
providerProfileRef: opencode-go-default
providerLeaseRef: provider-lease:...
credentialGeneration: 4
materializerRef: opencode-auth-json@1
mountClass: provider-auth
targetPath: /home/app/.local/share/opencode/auth.json
accessMode: read-only
cleanupRef: credential-cleanup:...
attestationRef: artifact:...
```

Secret bodies never enter the handle.

### 12.4 Built-in materializer classes

| Class | Use |
| --- | --- |
| `oauth-home` | Exclusive mutable CLI OAuth home with generation tracking |
| `omnigent-provider-config` | Lease-owned Omnigent provider configuration backed by MoonMind secret refs |
| `generated-auth-file` | Vendor-owned API-key or OAuth file generated in protected run state |
| `secret-env-file` | Protected environment file consumed by a trusted entrypoint |
| `session-scoped-config` | Per-session generated provider or vendor configuration |
| `host-owned-auth` | Pre-authenticated connected host where MoonMind does not copy the credential |
| `none` | Harness requires no model credential |

### 12.5 Mutable credential state

A mutable credential store requires exclusive ownership unless a provider-specific design proves safe concurrent refresh.

Refreshed state either persists to the authoritative credential store or is rejected before launch. A disposable copy that silently loses refresh state is invalid.

### 12.6 Current Codex materializer

The existing Codex OAuth volume, generation checks, startup scripts, readiness checks, profile lease, and release-last cleanup form the initial `codex-oauth-home@1` materializer.

The generic materializer interface wraps those existing operations. It does not require rewriting them before another harness can be added.

## 13. Host Classes

### 13.1 Purpose

A Host Class is an immutable declaration of an environment expected to run a set of harness implementations and materializers.

A Host Class is not a live host. It is class-level admission evidence. The exact host must still pass the attestation in section 8.

### 13.2 Host Class document

```yaml
schemaVersion: moonmind.omnigent-host-class.v1
hostClassId: omnigent-native-standard
version: 3
imageRef: ghcr.io/example/omnigent-host@sha256:...
omnigentVersion: "<semver>"
omnigentBuildDigest: sha256:...
architectures:
  - linux/amd64

declaredHarnessImplementations:
  - harnessId: opencode-native
    implementationRef: omnigent-harness-implementation:sha256:...
    runtimeDependencies:
      - name: opencode
        version: 1.18.11
        digest: sha256:...
  - harnessId: codex-native
    implementationRef: omnigent-harness-implementation:sha256:...

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

### 13.3 Existing host realizations

The existing `omnigent-host-codex` static service, Codex on-demand container path, scripts, OAuth volume, state volumes, mounted tools, resolved Skill projection, restricted egress, and health checks remain one registered Host Class realization.

The current Claude static service may be registered independently.

No existing service is renamed merely to make the implementation appear generic. The generic planner selects these concrete realizations through descriptors.

### 13.4 Host composition

MoonMind may operate several bounded Host Classes:

- core SDK and subprocess host.
- standard native harness host.
- specialized vendor host.
- approved community-plugin host.
- connected static host for host-owned authentication.

A single large image is not required.

### 13.5 Installation policy

Host-side harness installation is an operator setup or image-building action. An ordinary supported production workflow does not download or install a new harness.

Development and connected-host flows may expose Omnigent setup operations. The resulting host readiness and build attestation are observed and never inferred from an installation request alone.

## 14. Launch policies

### 14.1 Generic policy

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

### 14.2 Existing Codex policies

`codex-on-demand@1` and `codex-static@1` remain valid policy versions. The generic compiler reads their normalized policy fields without relying on the `codex-` prefix.

They may continue to govern Codex for as long as their support and rollback contracts require. A future generic policy may replace them for new selections only after equivalent behavior is proven.

### 14.3 Policy intersection

A policy is class-admissible only when it permits:

- the harness integration mode.
- the selected Host Class.
- every credential materializer.
- the requested workspace mutation.
- required repository and publication operations.
- required control and continuation capabilities.
- the required network and capture posture.

Policy mismatch blocks before lease acquisition.

## 15. Capability negotiation

### 15.1 Pre-host admission decision

The planner computes:

```text
workflow requirements
∩ Agent Profile requirements
∩ pinned harness-catalog declarations
∩ Host Class declarations
∩ credential materializer capabilities
∩ MoonMind bridge capabilities
∩ launch policy
∩ support policy
```

The result is an immutable `ClassAdmissionDecision`. It may prove class-level compatibility. It does not claim that an on-demand exact host is already ready.

The v1 `requiredSatisfied` field remains the exact-host capability set consumed
by every deployed worker. A runtime-mode token such as `omnigent` is admitted
only by the trusted runtime-selection boundary and recorded through the selected
plan authority; it is not added to `requiredSatisfied` or redundantly treated as
a CLI or harness feature the exact host must advertise. Preserving this wire
shape lets a new API safely submit plans to a pre-cutover worker.
Workflow-authored input never supplies the runtime-selection evidence.

The plan also records `runtimeValidationRequirements`, which name every fact that must be proven later. Typical requirements include exact harness implementation, vendor CLI version, restricted-egress attachment, mounted Skills, mounted tools, model availability, and intervention support.

### 15.2 Exact-host validation decision

After the host exists, a verifier computes:

```text
ClassAdmissionDecision
∩ exact HostHarnessAttestation
∩ exact network and mount attestation
∩ live model-option attestation
∩ current bridge and session-control readiness
```

The result is a fenced `ExactHostCapabilityDecision` stored in the runtime binding. Missing, unknown, stale, or mismatched required evidence blocks runner and session creation.

### 15.3 Required, preferred, and unknown

- Missing required class-level capability blocks plan creation.
- Unknown required class-level capability blocks plan creation.
- Missing or unknown required exact-host capability blocks realization before runner or session creation.
- Missing preferred capability may produce an explicit degraded decision.
- Unknown preferred capability is recorded as unknown and may be treated as unavailable.
- A degraded decision never broadens authority.
- No mismatch silently selects another harness, Provider Profile, Host Class, model, policy, or realizer.

### 15.4 Representative capability rules

| Requirement | Admission and runtime evidence |
| --- | --- |
| Active cancellation | Harness declaration, Host Class declaration, exact-host interrupt attestation, bridge support, policy permission |
| Token streaming | Harness declaration plus support evidence for the exact model and realizer |
| Warm continuation | Warm-reattach declaration plus retained and fenced session or host state |
| Cold continuation | Workspace checkpoint plus a supported rebuild or new-session strategy |
| Tool approval | Harness elicitation declaration, exact-host implementation match, and MoonMind approval authority |
| Unattended execution | No unresolved interactive login, trust, install, or permission step on the exact host |
| Subagent fanout | Harness declaration, exact-host support, and MoonMind execution-fanout capability |
| Image input | Harness declaration, exact bridge transport, and model support |
| Reasoning effort | Compatible effort family and exact normalized model configuration |
| Model override | Compatible model family and live model-option attestation |
| Repository mutation | Workspace authority, exact Git/tool readiness, credential capability, and publish policy |
| Restricted egress | Class policy plus enforced network attestation for the exact host |
| Resolved Skills | Plan-pinned Skill refs plus exact-host delivery attestation |
| Native Workflow Chat | Binding-scoped intersection and bridge enforcement |

## 16. Omnigent execution plan

### 16.1 Canonical plan payload

The planner emits a payload that contains only pre-host decisions:

```yaml
schemaVersion: moonmind.omnigent-execution-plan-payload.v1
endpointRef: default
agentProfileSnapshotRef: omnigent-agent-profile:...
harnessCatalogRef: omnigent-harness-catalog:sha256:...
harnessId: opencode-native
harnessImplementationRef: omnigent-harness-implementation:sha256:...

agentSource:
  kind: bundle
  bundleArtifactRef: artifact:...
  bundleDigest: sha256:...
  importReceiptRef: omnigent-agent-import:...
  importedAgentId: moonmind-opencode-default
  importedAgentVersion: "<upstream-version>"
  importedContentDigest: sha256:...

credentialBindingSetRef: omnigent-credential-bindings:opencode-go-primary@3#sha256:...
credentialBindings:
  primary-model:
    providerProfileRef: opencode-go-default
    materializerRef: opencode-auth-json@1

hostClassRef: omnigent-native-standard@3
launchPolicyRef: omnigent-on-demand@1
executionRealizerRef: generic-omnigent-host@1

model:
  qualifiedId: opencode/...
  effort: null
  routeRef: opencode-go
  normalizedOptions: {}
  modelConfigDigest: sha256:...

resolvedSkills:
  resolvedSkillSetRef: artifact:...
  resolvedSkillSetDigest: sha256:...
  skillDeliveryRef: skill-delivery:sha256:...

classAdmissionDecision:
  requiredSatisfied:
    - interrupt
    - repository.read
    - artifact.capture
  preferredSatisfied:
    - streaming
  degraded: []
  unknown: []

runtimeValidationRequirements:
  - exact-harness-implementation
  - exact-vendor-runtime
  - exact-network-egress
  - exact-skill-delivery
  - live-model-option

workspaceIntentRef: workspace-intent:sha256:...
capturePolicyRef: ...
policySnapshotRef: omnigent-policy:sha256:...
supportCombinationKey: omnigent-support:sha256:...
```

The payload intentionally contains no `credentialGeneration`, `providerLeaseRef`, exact host id, exact host readiness result, or `planRef`.

### 16.2 Plan envelope and canonicalization

MoonMind serializes the payload as canonical JSON with:

- UTF-8 encoding.
- sorted object keys.
- no insignificant whitespace.
- normalized enum and null representation.
- no envelope fields.

It hashes those bytes and persists:

```yaml
schemaVersion: moonmind.omnigent-execution-plan-envelope.v1
planRef: omnigent-execution-plan:sha256:<payload-digest>
payload: <the canonical payload>
```

Verification removes no fields and substitutes no placeholder. It canonicalizes only `payload` and compares the resulting digest with `planRef`.

### 16.3 Plan exclusions

The payload excludes:

- secret bodies.
- OAuth or vendor credential files.
- acquired credential generations.
- Provider Profile lease refs.
- exact host ids and exact-host readiness claims.
- Docker volume names.
- Docker socket access.
- arbitrary bind sources.
- resolved worker or daemon paths.
- caller-provided host ids.
- mutable environment-derived authority.
- Skill bodies and mutable Skill source paths.
- unbounded upstream metadata.

## 17. Runtime binding

### 17.1 Binding contract

After the plan is committed and Provider Profile leases are acquired, the realizer creates a separate fenced binding:

```yaml
schemaVersion: moonmind.omnigent-runtime-binding.v1
runtimeBindingRef: omnigent-runtime-binding:sha256:...
executionPlanRef: omnigent-execution-plan:sha256:...
executionScopeRef: mm:<workflow-id>
providerLeases:
  primary-model:
    providerProfileRef: opencode-go-default
    providerLeaseRef: provider-lease:...
    credentialGeneration: 4
    credentialRuntimeRef: credential-runtime:...
hostBindingRef: host-binding:...
hostLeaseRef: host-lease:...
hostLeaseGeneration: 7
omnigentHostId: host_...
hostHarnessAttestationRef: artifact:...
exactHostCapabilityDecisionRef: artifact:...
workspaceResolutionRef: workspace-resolution:...
modelOptionAttestationRef: artifact:...
skillDeliveryAttestationRef: artifact:...
omnigentSessionId: null
cleanupAuthorityRefs: []
```

Mutable lifecycle fields are stored through the fenced control-plane aggregates. The stable aggregate identity is `(executionPlanRef, executionScopeRef)`; each accepted authority transition produces a new digest-addressed binding ref and a monotonic revision. Replaced acquired generations remain historical evidence and cannot authorize a current write.

### 17.2 Credential rotation

The first successful Provider Profile lease acquisition determines the binding generation. A rotation that happens before that acquisition is not a conflict because the plan selected the account and materializer, not a generation.

After the runtime binding exists:

- the recorded generation is mandatory at materialization, host readiness, session creation, execution, and cleanup boundaries.
- an unchanged acquisition reuses the current binding idempotently.
- a newly acquired Provider Profile lease or generation advances the execution-scoped binding revision and fence without changing the plan.
- credential maintenance drains or fences bound consumers before activating replacement state according to the Provider Profile contract.
- a stale activity receives `fencing_conflict` or an equivalent typed result and reconciles.

### 17.3 Exact-host mismatch

If the exact host reports another harness implementation, plugin digest, vendor runtime, image, architecture, capability set, model catalog, network posture, or Skill delivery than the plan permits, realization fails before runner or session creation.

The realizer cleans up only resources it owns, records the mismatch as bounded evidence, and retains the same plan for diagnosis. It does not amend the plan to match the host.

## 18. Control-plane integration

The generic platform uses the canonical Omnigent control-plane aggregates.

`OmnigentSession` owns the immutable Agent Profile snapshot ref, agent-source ref, resolved Skill refs, execution-plan ref, runtime-binding ref, provider session authority, chat binding, desired and observed lifecycle state, revision, and fencing generations.

`OmnigentTurnAttempt` owns request idempotency and attempt delivery. It cannot replace the plan or runtime binding and cannot terminalize the session.

`OmnigentObservation` records bounded catalog, host, model, event, Skill-delivery, and cleanup evidence. Full payloads remain artifact-backed.

`OmnigentCommand` journals host, runner, session, message, interruption, harvest, and cleanup side effects.

Provider Profile, host lease, session supervisor, and cleanup generations fence stale owners. A stale activity result triggers fresh reconciliation. It never blindly repeats a provider side effect.

The generic design does not create a second session authority beside these aggregates.

## 19. Execution lifecycle

A conforming realization preserves this order:

1. Validate workflow and Step authority.
2. Resolve the immutable Agent Profile snapshot.
3. Resolve the pinned harness catalog, implementation identity, and trust state.
4. Resolve the exact upstream or bundle-backed agent source.
5. Resolve Agent Profile Skill intent into an immutable `ResolvedSkillSet` and delivery descriptor.
6. Resolve the exact credential-binding-set version and digest.
7. Resolve credential slots and compatible Provider Profiles.
8. Select compatible materializers.
9. Select a compatible Host Class and launch policy using class-level evidence.
10. Compute the class-level required and preferred capability admission decision.
11. Normalize the exact model configuration and compute `modelConfigDigest`.
12. Select the versioned execution realizer and compute `supportCombinationKey`.
13. Compile, canonicalize, hash, and persist the execution-plan envelope.
14. Acquire Provider Profile leases in deterministic order.
15. Persist the initial runtime binding with every acquired Provider Profile generation.
16. Resolve or create the durable host binding and host lease.
17. Materialize the authoritative workspace.
18. Materialize credential runtime state using the acquired generations.
19. Start or attach the selected host realization.
20. Obtain a fenced exact-host harness-build attestation.
21. Confirm the exact host implementation and vendor runtimes match the plan.
22. Validate exact-host capabilities, network posture, mounted tools, and resolved Skill delivery.
23. Resolve and attest live model options for the selected model configuration.
24. Persist the exact-host capability, model, workspace, Skill, and cleanup refs in the runtime binding.
25. Create or reattach the Omnigent session.
26. Persist the session identity before posting the first message.
27. Prepare and post the idempotent first message.
28. Stream and normalize events.
29. Route approvals, intervention, and control through capability enforcement.
30. Harvest artifacts, repository evidence, capture, and checkpoints.
31. Stop or drain the provider session as required.
32. Clean up run-scoped materializer state.
33. Remove the on-demand host or release the connected host.
34. Persist terminal cleanup evidence.
35. Release Provider Profile leases last.

A retry reuses the same plan, execution-scoped runtime-binding aggregate, resolved Skills, session, command identities, workspace authority, and applicable host authority. If lease acquisition returns replacement live authority, it advances that aggregate through revision-checked fencing; it does not replan against a newer catalog or silently change account, model, harness implementation, bundle content, Host Class, policy, or realizer.

An exact-host validation failure does not invalidate the pre-host plan. It proves that the chosen realization did not satisfy it. A new Host Class, model, binding-set version, or realizer requires a new plan and explicit lineage.

## 20. Session, continuation, branch, and remediation semantics

Harness declarations guide continuation but do not replace MoonMind checkpoint authority.

### 20.1 Warm reattach

Warm reattach is valid only when the same provider session, runtime binding, acquired credential generations, harness implementation, and compatible host state remain authoritative. A newer host implementation or credential generation requires explicit reconciliation and cannot be silently adopted.

### 20.2 Cold continuation

A cold continuation uses immutable workspace checkpoint evidence, prior result refs, resolved Skill refs, and a harness-supported strategy:

- rebuild vendor-native history.
- inject a bounded continuation preamble.
- create a new session with an explicit context package.
- reject when no safe strategy exists.

The planner does not pretend that all harnesses have equivalent resume semantics.

### 20.3 Branches and remediation

Checkpoint branches and remediation preserve:

- Agent Profile snapshot.
- discriminated agent-source identity.
- harness implementation identity.
- resolved Skill and delivery refs.
- credential-binding-set version and digest.
- Provider Profile bindings and acquired generations after binding.
- materializer refs.
- Host Class and launch policy.
- model configuration digest.
- execution realizer and support-combination key.
- execution-plan and runtime-binding lineage.
- workspace checkpoint authority.

A branch or remediation attempt may select a different harness, model, Skill set, binding set, Host Class, or realizer only through a new explicit plan. It is never an implicit recovery fallback.

## 21. Native Workflow Chat and controls

Native Workflow Chat continues to use the binding-scoped facade.

The effective control surface is the intersection of:

```text
upstream session capabilities
∩ exact-host capability decision
∩ Agent Profile snapshot
∩ execution plan
∩ runtime binding
∩ workflow and Step state
∩ caller permission
```

The browser may hide unavailable controls. The bridge remains the enforcement boundary.

Model, effort, terminal, file, approval, interrupt, stop, clear-context, workspace, subagent, and resource controls remain separately gated. An upstream control is technical availability, not authorization.

## 22. Evidence and observability

Every run records safe references for:

- Agent Profile version and digest.
- discriminated agent-source ref and bundle digest when applicable.
- harness catalog ref and trust classification.
- exact harness implementation and vendor runtime identities.
- resolved Skill snapshot and delivery refs.
- credential-binding-set id, version, and digest.
- Provider Profile refs, lease refs, and acquired generations.
- materializer refs and attestations.
- Host Class and immutable image digest.
- exact-host harness attestation.
- launch policy and compiled policy ref.
- model configuration digest and model-option attestation.
- execution realizer ref and support-combination key.
- execution-plan and runtime-binding refs.
- class-admission and exact-host capability decisions.
- host, runner, session, and chat-binding identity.
- workspace resolution.
- capture and repository evidence.
- checkpoint and continuation lineage.
- cleanup claims and results.

Logs and metrics use bounded reason codes and low-cardinality labels. Secret values, raw provider payloads, terminal transcripts, Skill bodies, and unbounded diagnostics remain artifact-backed and redacted.

Objective terminal evidence remains required. Process exit, wrapper completion, assistant prose, or a mutable filesystem path is not completion.

## 23. Cleanup and janitor authority

Every materializer returns a non-secret cleanup ref. Every host realization returns host cleanup authority. Cleanup is revision and generation fenced.

On-demand cleanup removes only plan-owned and binding-owned resources. Static connected-host cleanup drains plan-owned sessions and temporary state without deleting unrelated host authentication.

Provider Profile leases release only after:

- the harness process no longer consumes the acquired credential generation.
- materializer cleanup is complete or durably delegated to the janitor.
- host cleanup is complete or durably delegated.
- terminal evidence records the cleanup state.

A cancellation or ambiguous provider outcome retains enough durable authority for retry or janitor reconciliation. It does not release credentials while a consumer may still be alive.

## 24. Support classification and identity

MoonMind reports one support classification for each exact `supportCombinationKey`.

### 24.1 Support key

The normalized support-key payload includes:

```yaml
omnigentServerBuildRef: ...
omnigentHostBuildRef: ...
harnessImplementationRef: ...
vendorRuntimeRefs: []
agentSourceRef: ...
materializerRefs: []
providerCompatibilityClass: ...
hostClassRef: ...
architecture: linux/amd64
launchPolicyRef: ...
modelConfigDigest: sha256:...
executionRealizerRef: generic-omnigent-host@1
requiredCapabilitiesDigest: sha256:...
```

The key is the digest of canonical payload bytes. Account-specific secret identity is not part of support evidence, but the Provider Profile compatibility class and credential strategy are.

### 24.2 Fully managed

- approved on-demand or managed host.
- MoonMind-managed credential materialization.
- unattended launch.
- exact-host and exact-model validation.
- interruption and capture.
- cleanup and janitor evidence.
- checkpoint and recovery coverage required by the selected capabilities.

### 24.3 Connected host

- approved static host.
- host-owned authentication or device-bound setup.
- MoonMind can select, lease, attest, and drain the host.
- workflow launch is unattended after operator setup.

### 24.4 Experimental

- trusted and launchable.
- bounded smoke validation passes.
- one or more support rows for the exact key lack protected evidence.

### 24.5 Discovered only

- present in the catalog.
- no approved materializer, Host Class, policy, model, or realizer combination.
- visible with actionable setup guidance.
- not launchable.

### 24.6 Quarantined

- plugin or package is not approved.
- receives no provider credentials, workspace mutation authority, or workflow execution authority.

## 25. Conformance

### 25.1 Generic contract suite

Every supported combination proves:

- catalog identity and freshness.
- exact harness implementation trust.
- discriminated Agent Profile source and bundle identity.
- resolved Skill immutability and delivery.
- binding-set version and digest.
- materializer secret containment.
- Provider Profile capacity, acquired generation, rotation fencing, and cooldown.
- Host Class admission.
- exact-host implementation, vendor runtime, capability, mount, and network attestation.
- exact normalized model configuration and model-option behavior.
- exact execution realizer.
- session creation and idempotent first message.
- stream and terminal evidence.
- requested control capabilities.
- workspace and repository boundaries.
- cancellation and ambiguous delivery.
- host and credential cleanup.
- janitor recovery.
- checkpoint behavior required by the profile.
- raw-channel secret scans.

### 25.2 Harness-specific evidence

Harness-specific tests prove claims that cannot be inferred from the generic contract, including:

- vendor login and refresh behavior.
- exact model-option behavior for each qualified model configuration.
- native terminal takeover.
- elicitation behavior.
- fork-history semantics.
- streaming granularity.
- interruption behavior.
- subagent behavior.

### 25.3 Realizer-specific evidence

A realizer proves that it correctly enforces the same plan and runtime-binding contracts. Evidence gathered through one realizer does not qualify another realizer unless the support matrix explicitly defines and proves a shared observation boundary.

### 25.4 Codex regression requirement

The current Codex conformance and support matrices remain required while Codex uses either the current or generic realizer.

A generic refactor is not allowed to reduce:

- OAuth exclusivity and generation fencing.
- exact host binding.
- workspace isolation.
- mounted resolved Skill and tool projection.
- restricted egress.
- capture.
- repository publication.
- checkpoint evidence.
- cancellation.
- cleanup.
- replay and historical-read compatibility.
- rollback behavior.

## 26. Product experience

### 26.1 Settings

Settings exposes:

- Omnigent endpoint and version.
- discovered harnesses and exact implementation identities.
- trust and support classification.
- capability declarations.
- setup steps.
- compatible Host Classes.
- exact connected-host attestations when applicable.
- compatible Provider Profiles.
- versioned credential-binding sets.
- model options and model configuration digest.
- resolved validation and smoke status.
- active lease, acquired generation, and cooldown state.

The normal view shows only essential setup. Advanced host, policy, realizer, materializer, and attestation details use progressive disclosure.

### 26.2 Workflow Create

The normal selection is:

```text
Execution provider: Omnigent
Agent Profile: <profile>
Provider account: <compatible Provider Profile or binding set>
Model: <profile default or explicit selection>
Host policy: <default compatible policy>
```

Raw harness selection may be exposed as an advanced Agent Profile authoring option. Raw host ids, Docker volumes, credential files, credential generations, and environment variables are never authoring controls.

### 26.3 Default behavior

Omnigent may become the default execution provider before every harness is fully managed. The default Agent Profile may remain the proven Codex profile while additional harnesses progress through support classifications.

A new harness, model, Host Class, or realizer becoming available does not change existing workflow defaults or historical plans.

## 27. Codex continuity and preservation contract

### 27.1 Existing assets remain authoritative

The current Codex lane maps into the generic design as follows:

| Existing Codex asset | Generic platform role |
| --- | --- |
| `external/omnigent` | Stable top-level execution identity |
| `codex-native-ui` | Upstream agent identity |
| `codex-native` plus pinned Omnigent build | Harness implementation identity |
| Codex OpenAI OAuth Provider Profile | Provider account and capacity authority |
| `codex_auth_volume` and acquired generation | `codex-oauth-home@1` runtime-binding state |
| `omnigent-host-codex` Compose service | Existing static Host Class realization |
| Current on-demand Codex container path | Existing on-demand Host Class realization |
| `codex-on-demand@1` and `codex-static@1` | Existing launch policy versions |
| `profile_bound_execution.py` | Initial registered execution realizer |
| mounted Skill/tool projection | Existing resolved-Skill delivery realization |
| bridge, checkpoint, publication, cleanup, and janitor code | Shared lifecycle implementation |
| Codex support and cutover matrices | Required support evidence for each realizer |

### 27.2 No big-bang dependency

The generic catalog and planner may be introduced while Codex still uses `codex-profile-bound@1`.

A representative Codex plan payload is:

```yaml
harnessId: codex-native
harnessImplementationRef: omnigent-harness-implementation:sha256:...
agentSource:
  kind: upstream
  upstreamId: codex-native-ui
  upstreamVersion: "<version>"
credentialBindingSetRef: omnigent-credential-bindings:codex-openai-oauth@1#sha256:...
credentialBindings:
  primary-model:
    providerProfileRef: codex_openai_oauth
    materializerRef: codex-oauth-home@1
hostClassRef: omnigent-codex-current@1
launchPolicyRef: codex-on-demand@1
executionRealizerRef: codex-profile-bound@1
model:
  qualifiedId: gpt-...
  modelConfigDigest: sha256:...
resolvedSkills:
  resolvedSkillSetRef: artifact:...
  resolvedSkillSetDigest: sha256:...
  skillDeliveryRef: skill-delivery:sha256:...
```

The acquired OAuth generation remains in the post-lease runtime binding, matching the current Codex authority model.

The realizer delegates to the existing coordinator and scripts. New harnesses may use `generic-omnigent-host@1` at the same time. This coexistence is deterministic planning, not runtime fallback accumulation.

### 27.3 Stable persisted identity

Existing Codex workflow snapshots, Agent Profile versions, Provider Profile ids, policy refs, bridge records, checkpoint refs, Skill refs, and Temporal histories remain readable and replayable.

New generic fields are additive or compiled from existing immutable snapshots. Historical records are not rewritten to claim that they used a generic realizer.

### 27.4 Parity before reassignment

Codex may move from `codex-profile-bound@1` to a generic realizer only when the generic realizer proves the existing Codex support matrix for the same:

- server and host image digests.
- architectures.
- harness and CLI versions.
- Agent Profile and source versions.
- Provider Profile and materializer classes.
- model configuration digest.
- Skill delivery behavior.
- policy versions.
- lifecycle requirements.

Because the realizer is part of `supportCombinationKey`, evidence for `codex-profile-bound@1` never automatically qualifies `generic-omnigent-host@1`.

A reassignment changes only new plans. Existing plans retain their realizer ref.

### 27.5 Rollback

Rollback changes the selected realizer for future eligible Codex plans. It does not mutate existing session, plan, runtime-binding, checkpoint, or support evidence.

The current Codex lane remains available until its existing cutover and retirement contracts permit removal.

## 28. OpenCode Go example

OpenCode Go is one composition of the generic system:

```yaml
agentProfile:
  source:
    kind: upstream
    upstreamId: opencode-native-ui
    upstreamVersion: "<version>"
    upstreamSnapshotDigest: sha256:...
  harness:
    id: opencode-native
    implementationRef: omnigent-harness-implementation:sha256:...
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
  bindingSetId: opencode-go-primary
  version: 3
  digest: sha256:...
  bindings:
    primary-model:
      providerProfileRef: opencode-go-default
      materializerRef: opencode-auth-json@1

model:
  qualifiedId: opencode/<go-model>
  modelConfigDigest: sha256:...

hostClassRef: omnigent-native-standard@3
launchPolicyRef: omnigent-on-demand@1
executionRealizerRef: generic-omnigent-host@1
```

The execution plan selects the Provider Profile but not its generation. After lease acquisition, the runtime binding records the exact OpenCode Go credential generation.

The OpenCode materializer writes a protected, binding-owned auth file. The exact host must attest the pinned OpenCode harness implementation and CLI build. Live model validation proves the selected Go model before session creation. Cleanup removes materializer state before Provider Profile lease release.

This addition requires no new top-level agent, Temporal workflow, or harness-named branch in the generic lifecycle coordinator.

## 29. Extension boundary

### 29.1 Upstream harness metadata

MoonMind consumes upstream capability and setup metadata. When upstream metadata is insufficient for credential, runtime-build, or host selection, MoonMind uses an approved companion descriptor keyed by canonical harness implementation identity.

The companion descriptor may declare:

- credential slots.
- accepted materializer classes.
- host features.
- required binaries, services, versions, and digest rules.
- mutable state paths.
- validation probes.
- known conformance limitations.

It cannot declare secret values, arbitrary mounts, Docker authority, or policy exceptions.

### 29.2 Community plugins

A community plugin is launchable only when:

- its package, version, entry point, and digest are approved.
- its catalog contribution is stable and conflict-free.
- a compatible Host Class pins the plugin artifact.
- the exact selected host attests that same plugin artifact.
- every credential slot uses an approved materializer.
- its required capabilities can be enforced.
- its support classification permits the requested model and realizer combination.

An unapproved plugin remains visible as quarantined.

## 30. Failure taxonomy

The platform uses typed low-cardinality failures, including:

```text
OMNIGENT_HARNESS_CATALOG_UNAVAILABLE
OMNIGENT_HARNESS_CATALOG_STALE
OMNIGENT_HARNESS_UNKNOWN
OMNIGENT_HARNESS_UNTRUSTED
OMNIGENT_HARNESS_BUILD_MISMATCH
OMNIGENT_VENDOR_RUNTIME_MISMATCH
OMNIGENT_AGENT_IDENTITY_UNAVAILABLE
OMNIGENT_AGENT_BUNDLE_IDENTITY_CONFLICT
OMNIGENT_SKILL_SNAPSHOT_UNAVAILABLE
OMNIGENT_SKILL_DELIVERY_MISMATCH
OMNIGENT_CAPABILITY_REQUIRED_UNSUPPORTED
OMNIGENT_CAPABILITY_REQUIRED_UNKNOWN
OMNIGENT_EXACT_HOST_CAPABILITY_MISMATCH
OMNIGENT_CREDENTIAL_BINDING_SET_CONFLICT
OMNIGENT_CREDENTIAL_SLOT_UNBOUND
OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE
OMNIGENT_CREDENTIAL_GENERATION_FENCED
OMNIGENT_CREDENTIAL_MATERIALIZER_UNAVAILABLE
OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED
OMNIGENT_HOST_CLASS_UNAVAILABLE
OMNIGENT_HOST_HARNESS_NOT_READY
OMNIGENT_MODEL_UNAVAILABLE
OMNIGENT_MODEL_CONFIG_UNSUPPORTED
OMNIGENT_LAUNCH_POLICY_INCOMPATIBLE
OMNIGENT_EXECUTION_PLAN_CONFLICT
OMNIGENT_EXECUTION_PLAN_DIGEST_MISMATCH
OMNIGENT_EXECUTION_REALIZER_UNAVAILABLE
OMNIGENT_RUNTIME_BINDING_CONFLICT
OMNIGENT_CLEANUP_DEFERRED
```

Diagnostics name the failed boundary and an actionable remediation. They do not parse vendor log text as authority.

## 31. Rejected alternatives

### 31.1 One MoonMind runtime per harness

Rejected because it duplicates selection, credentials, host lifecycle, recovery, and evidence code and makes Omnigent a transport detail rather than the primary harness provider.

### 31.2 Big-bang replacement of the Codex lane

Rejected because the existing Codex path contains substantial verified authority and recovery behavior. Replacing it before generic parity would increase risk and delay use of new harnesses.

### 31.3 Exact-host claims during pre-host planning

Rejected because an on-demand exact host does not exist yet. Host Class evidence admits the class. A fenced post-realization verifier admits the exact host.

### 31.4 Pre-lease credential generation in the plan

Rejected because rotation can occur before lease acquisition. The plan selects the Provider Profile. The runtime binding records the acquired generation.

### 31.5 One universal host image

Rejected because harness dependencies, release cadence, size, authentication, and architecture support differ. Host Classes provide bounded composition.

### 31.6 Trust upstream declarations alone

Rejected because capability declarations do not prove exact-host implementation, MoonMind policy enforcement, cleanup, secret containment, or live behavior.

### 31.7 Workflow-time software installation

Rejected for supported production execution because mutable installation breaks image authority, reproducibility, egress policy, and conformance evidence.

### 31.8 Silent fallback to another harness or realizer

Rejected because it changes credentials, billing, model behavior, continuation semantics, and evidence authority.

### 31.9 Self-referential plan hashes

Rejected because an object cannot contain the digest of its complete own bytes without a special exclusion rule. The plan envelope stores the payload digest outside the payload.

## 32. Acceptance criteria

The design is realized when:

1. MoonMind projects the selected Omnigent endpoint's harness catalog without a Codex-only allowlist.
2. Every catalog row has an exact implementation identity, trust state, and support classification.
3. Agent Profiles use a discriminated upstream or bundle-backed source.
4. Bundle-backed plans carry the artifact digest, import receipt, imported identity, and imported content digest.
5. Agent Profiles pin a canonical harness implementation and catalog snapshot.
6. Agent Profile Skill intent resolves to immutable Skill and delivery refs before plan commitment.
7. Credential-binding-set refs include stable id, immutable version, and digest.
8. Plans select Provider Profiles and materializers without pre-lease credential generations.
9. Runtime bindings record the exact acquired generations after lease acquisition.
10. Required and preferred class capabilities are negotiated before lease acquisition.
11. On-demand hosts are admitted by Host Class evidence, not fictional exact-host readiness.
12. Every exact host attests the selected harness implementation, vendor runtime, network, mounts, Skills, and required capabilities before runner or session creation.
13. Provider Profiles remain the single account-capacity and cooldown authority.
14. Credential materializers are versioned, allowlisted, secret-safe, generation-aware, and cleanup-aware.
15. Host Classes are immutable and never replace exact-host proof.
16. Launch policies no longer require harness-named runtime branches.
17. Every run persists one canonical secret-free plan payload and a non-self-referential envelope ref.
18. Every realized run persists a separate fenced runtime binding.
19. Support identity includes exact model configuration and execution realizer version.
20. The fenced Omnigent control plane owns the session and side-effect journal.
21. Adding an approved harness does not require a new branch in the generic lifecycle coordinator.
22. Unknown community harnesses receive no credentials or workflow authority.
23. Existing Codex workflows continue through the current realizer without reduced behavior.
24. Existing Codex histories, checkpoints, Skills, and evidence remain readable.
25. The generic realizer can run at least one non-Codex own-auth harness and one different integration class.
26. OpenCode Go can run through `opencode-native` with managed credential materialization and exact-host build attestation.
27. Cancellation, credential rotation, cleanup, and janitor recovery are proven for generic hosts.
28. Codex moves to the generic realizer only after the existing Codex support matrix passes for that exact realizer and model configuration.
29. Omnigent can be the preselected execution provider while Codex remains the default proven Agent Profile.
30. Direct runtimes remain available only according to their existing rollback and retirement contracts.

## 33. Enforced module dependency boundaries

The implemented package map, the allowed dependency directions, the narrow port
inventory, and the bounded boundary exemptions are owned by
[`docs/Omnigent/OmnigentModuleArchitecture.md`](./OmnigentModuleArchitecture.md).
That document is canonical declarative and is enforced by
`tests/unit/omnigent/test_module_architecture.py`; the summary below states only
the invariants this design depends on.

Dependency direction is from outer adapters toward application coordination and
pure contracts. Pure modules never import infrastructure, settings, or the
environment. Generic application coordination receives infrastructure through
narrow ports supplied at the composition boundary and does not branch on
`codex-native`, `opencode-native`, `pi-native`, Provider Profile ids, or model
vendors; approving a harness is registration data in
`harness_platform/harness_registry.py`. Deterministic session and turn
identities have one owner, `control_plane/identities.py`.

The existing Codex realizer is an explicit replay-visible legacy adapter. It and
every other retained component are listed in
`moonmind/omnigent/legacy_retirement.py` with their retirement owner, their
#3835 retirement class, the active/replay/historical-read/rollback dependencies
that must drain before removal, their earliest removal stage, and the guard test
a removal PR must cite — alongside the bounded architecture exemptions that are
still permitted, each of which names the retirement row that retires it.

## 34. Document authority and future promotion

This design owns the target generic harness-platform model.

The current Codex-specific documents remain authoritative for the existing Codex specialization and its support state. This design extends them. It does not silently supersede their current guarantees.

The settled module architecture has been promoted out of this design into
[`docs/Omnigent/OmnigentModuleArchitecture.md`](./OmnigentModuleArchitecture.md),
which is canonical declarative and machine-enforced. The remaining sections here
stay proposed until their target semantics are implemented, at which point they
are promoted the same way and this design is superseded or removed according to
the documentation architecture standard.
