# Codex via Omnigent Create-to-host contract

**Document Class:** Canonical declarative  
**Status:** Accepted  
**Owners:** MoonMind Platform  
**Last updated:** 2026-07-22  
**Authority:** Product selection, Workflow Create compilation, and acceptance contract for Codex via Omnigent  
**Traceability:** MoonLadderStudios/MoonMind#3449

## 1. Product identity and authority

The normal `/workflows/new` choice is **Codex via Omnigent**. Workflow Create writes `runtime.mode = "omnigent"`; the trusted API compiler alone converts that selector to:

```text
agentKind = external
agentId   = omnigent
harness   = codex-native
```

`codex-native` is a nested harness choice, never a second top-level agent identity. `omnigent_codex`, caller-authored `session.hostId`, and direct-Codex substitution are invalid.

This document owns the product-to-runtime compilation boundary. Detailed contracts remain owned by [Provider Profiles](../Security/ProviderProfiles.md), [Omnigent Host OAuth](./OmnigentHostOAuth.md), [Omnigent Bridge](./OmnigentBridge.md), [Workspace locators](../Workflows/WorkspaceLocators.md), [Settings System](../Security/SettingsSystem.md), and [Workflow Details Page](../UI/WorkflowDetailsPage.md).

## 2. Profile, host, and policy selection

A selected launch-ready Codex OAuth Provider Profile becomes `executionProfileRef` unchanged. Eligibility is a trusted projection of OpenAI/Codex compatibility, activation, credential readiness, credential generation, and capacity. The UI renders:

| State | Selectable | Meaning |
| --- | --- | --- |
| `available` | yes | Launch-ready with allocatable capacity. |
| `busy` | yes | Eligible; submission may wait for the same profile lease. |
| `unavailable` | no | Disabled, incompatible, or not launch-ready. |
| `stale_generation` | no | Credential generation changed; drain or reconnect. |
| `disconnected` | no | OAuth enrollment is absent, expired, or revoked. |

The compiler rejects a missing or ineligible profile and never chooses another profile.

The form selects versioned `hostProfileRef` and `launchPolicyRef` values. The policy chooses exactly one mode: `static_compose` binds a compatible registered profile host, while `on_demand_docker` launches and later removes one lease-owned host. No host in the chosen mode is a failure; it does not trigger the other mode. `OMNIGENT_CODEX_HOST_LAUNCH_PROFILE` is bootstrap configuration only, never durable product authority.

Before host realization, the trusted launcher persists an immutable `effectiveLaunchSnapshot`: schema version, the selected execution profile and credential generation, resolved mode, profile and policy versions, image digest, machine target, resource limits, network/egress policy, safe mount targets, capture policy, and resolution time. Retries reuse it only while the selected credential generation remains current; a generation change makes the snapshot stale and requires reconciliation.

## 3. Field authority and workspace

| Field | UI-authored intent | Trusted-runtime resolution |
| --- | --- | --- |
| runtime | `mode=omnigent` | `external/omnigent` plus nested `codex-native` |
| profile/policy | eligible versioned refs | eligibility, generations, leases, effective snapshot |
| repository | repository ref and branch/ref | checkout and canonical `WorkspaceLocator` |
| publish | bounded publish mode and GitHub authority ref | credentials and mutation scope |
| inputs | attachment refs, Skill names, instructions, capture options | artifact authorization and immutable resolved Skill set |
| runtime identity | none | endpoint, binding, host, bridge/session, lease and container refs |
| paths/secrets | never | containment-checked daemon mount and boundary-only credentials |

Repository, branch, publish mode, attachments, resolved Skills, and GitHub mutation authority become a canonical `WorkspaceLocator` and trusted daemon-visible mount. Browser-authored absolute paths, bind sources, manual host IDs, credentials, and broad mutation authority are forbidden.

## 4. Versioned wire examples

Product envelopes use `omnigent-create-host/v1`; the canonical `AgentExecutionRequest` example instead follows its model directly. Refs are opaque safe identifiers.

### 4.1 Create input

```json
{
  "schemaVersion": "omnigent-create-host/v1",
  "runtime": {"mode": "omnigent"},
  "executionProfileRef": "provider-profile:codex-primary:v7",
  "hostProfileRef": "omnigent-host-profile:codex-standard:v3",
  "launchPolicyRef": "omnigent-launch-policy:restricted:v5",
  "repository": {"repositoryRef": "github-repository:MoonLadderStudios/MoonMind", "ref": "main"},
  "publishMode": "branch",
  "attachmentRefs": ["artifact:input-1"],
  "skillNames": ["fix-ci"],
  "githubAuthorityRef": "github-authority:repo-branch-write:v2",
  "instructions": "Implement the selected work.",
  "capture": {"transcript": true, "resources": true}
}
```

### 4.2 Persisted workflow parameters

```json
{
  "schemaVersion": "omnigent-create-host/v1",
  "runtime": {"mode": "omnigent"},
  "executionProfileRef": "provider-profile:codex-primary:v7",
  "workspaceLocator": {"kind": "sandbox", "workspaceId": "ws_01", "subpath": "."},
  "repositoryRef": "github-repository:MoonLadderStudios/MoonMind",
  "repositoryRefName": "main",
  "publishMode": "branch",
  "attachmentRefs": ["artifact:input-1"],
  "resolvedSkillSetRef": "skill-set:sha256:example",
  "githubAuthorityRef": "github-authority:repo-branch-write:v2",
  "omnigent": {"endpointRef": "omnigent-endpoint:local:v1", "agent": {"harnessOverride": "codex-native"}, "hostProfileRef": "omnigent-host-profile:codex-standard:v3", "launchPolicyRef": "omnigent-launch-policy:restricted:v5", "capture": {"transcript": true, "resources": true}}
}
```

### 4.3 AgentExecutionRequest

```json
{
  "agentKind": "external",
  "agentId": "omnigent",
  "executionProfileRef": "provider-profile:codex-primary:v7",
  "correlationId": "workflow:run_01:step_01",
  "idempotencyKey": "workflow:run_01:step_01:attempt_1",
  "workspaceSpec": {"workspaceLocator": {"kind": "sandbox", "workspaceId": "ws_01", "subpath": "."}},
  "parameters": {"omnigent": {"endpointRef": "omnigent-endpoint:local:v1", "agent": {"harnessOverride": "codex-native"}, "hostProfileRef": "omnigent-host-profile:codex-standard:v3", "launchPolicyRef": "omnigent-launch-policy:restricted:v5", "capture": {"transcript": true, "resources": true}}}
}
```

There is deliberately no `session.hostId`; host and session identities are trusted runtime outputs.

### 4.4 Effective launch snapshot

```json
{
  "schemaVersion": "omnigent-create-host/v1",
  "snapshotRef": "omnigent-launch-snapshot:run_01:v1",
  "executionProfileRef": "provider-profile:codex-primary:v7",
  "credentialGeneration": 7,
  "hostProfileRef": "omnigent-host-profile:codex-standard:v3",
  "launchPolicyRef": "omnigent-launch-policy:restricted:v5",
  "mode": "on_demand_docker",
  "imageDigest": "sha256:example",
  "machineTargetRef": "docker-target:local:v1",
  "resources": {"cpuLimit": "2", "memoryLimitMiB": 4096, "pidsLimit": 512, "tmpfsLimitMiB": 1024},
  "network": {"networkPolicyRef": "network-policy:restricted:v4", "egressPolicyRef": "egress-policy:github-openai:v2"},
  "mounts": {"workspaceTarget": "/workspaces/run", "skillsTarget": "/opt/moonmind-skills", "toolsTarget": "/opt/moonmind-tools"},
  "capture": {"transcript": true, "resources": true},
  "resolvedAt": "2026-07-22T00:00:00Z"
}
```

### 4.5 Workflow Detail projection

```json
{
  "schemaVersion": "omnigent-create-host/v1",
  "runtimeLabel": "Codex via Omnigent",
  "identity": {"agentKind": "external", "agentId": "omnigent", "harness": "codex-native"},
  "safeRefs": {"executionProfileRef": "provider-profile:codex-primary:v7", "hostLeaseRef": "host-lease:01", "bridgeSessionRef": "bridge-session:01", "omnigentSessionRef": "omnigent-session:01", "launchSnapshotRef": "omnigent-launch-snapshot:run_01:v1"},
  "stage": {"code": "terminal", "status": "completed"},
  "controls": [],
  "artifactRefs": ["artifact:transcript-01"],
  "resourceRefs": ["omnigent-resource:01"],
  "terminal": {
    "primaryStatus": "completed",
    "failureClass": null,
    "outputRefs": ["artifact:final-snapshot-01"],
    "diagnosticsRef": "artifact:diagnostics-01",
    "captureManifestRef": "artifact:capture-manifest-01",
    "cleanup": {"status": "completed", "janitorRequired": false},
    "profileLease": {"releaseStatus": "released"}
  }
}
```

The terminal payload embeds the canonical bridge `AgentRunResult` fields defined by [Omnigent Bridge §14.2](./OmnigentBridge.md#142-terminal-result): `primaryStatus` is the authoritative workflow outcome, while `failureClass`, `outputRefs`, `diagnosticsRef`, and `captureManifestRef` retain its result and evidence. `cleanup.status`, `cleanup.janitorRequired`, and `profileLease.releaseStatus` independently expose auxiliary lifecycle completion. Cleanup or lease-release failures remain visible there and never replace or obscure `primaryStatus`.

After cleanup, host/container handles are non-actionable historical refs; artifacts, lifecycle evidence, the terminal envelope, and safe identity refs remain available.

## 5. Journey and ownership

```mermaid
sequenceDiagram
    actor U as User
    participant C as Workflow Create
    participant A as API compiler
    participant W as Temporal workflow
    participant P as Profile Manager
    participant X as Workspace owner
    participant H as Host owner
    participant B as Omnigent Bridge
    participant E as Evidence owner
    U->>C: Select runtime, profile, policy, repository
    C->>A: omnigent-create-host/v1 input
    A->>A: Compile external/omnigent + codex-native
    A->>W: Persist canonical refs and parameters
    W->>P: Acquire selected profile lease/generation
    W->>X: Resolve WorkspaceLocator and daemon mount
    W->>H: Persist snapshot; bind or launch exact host mode
    H-->>W: Registration and harness readiness evidence
    W->>B: Authorize bridge and create session
    W->>B: Post first message once
    B-->>E: Stream events and harvest resources
    W->>E: Publish terminal envelope and artifacts
    W->>H: Clean owned runtime state
    H-->>W: Cleanup evidence
    W->>P: Release Provider Profile lease last
    W-->>C: Durable Workflow Detail projection
```

Create owns intent; the compiler owns request construction; Profile Manager owns eligibility, credentials and capacity; the workspace owner owns checkout/mount resolution; the host owner owns policy realization, readiness and cleanup; the bridge owns authorization, session, first-message idempotency and events; the artifact owner owns evidence publication; Temporal owns ordering and retries. Provider capacity is released only after credential-consuming host cleanup or safe reconciliation.

Workflow Detail labels the lane **Codex via Omnigent** and shows request validation, profile resolution/readiness/lease, workspace resolution, launch snapshot, host launch/binding, credential preflight, registration, harness readiness, bridge authorization, session creation, first-message post, running, harvest, evidence publication, cleanup, profile lease release, and terminal stages. Controls are capability-derived and server-authorized. Auxiliary cleanup/publication failures do not erase the primary outcome, but remain visibly unresolved.

## 6. Failure and no-fallback matrix

An explicit Omnigent selection never silently runs through direct Codex, another Provider Profile, another host mode, an arbitrary static host, or a broader network/mount policy.

| Failure | Stable code | Retryable | Remediation | Evidence |
| --- | --- | --- | --- | --- |
| Unsupported runtime | `OMNIGENT_RUNTIME_UNSUPPORTED` | no | Select a supported runtime. | Selector/schema. |
| Missing/ineligible profile | `OMNIGENT_PROFILE_UNAVAILABLE` | after action | Connect/select the exact eligible profile. | Safe profile ref/reason. |
| Busy profile | `OMNIGENT_PROFILE_BUSY` | yes | Wait or retry the same profile. | Capacity/lease state. |
| Stale generation | `OMNIGENT_PROFILE_GENERATION_STALE` | after action | Drain or reconnect. | Expected/observed generation. |
| Invalid policy | `OMNIGENT_LAUNCH_POLICY_INVALID` | no | Select valid versioned refs. | Refs/diagnostics. |
| Workspace failure | `OMNIGENT_WORKSPACE_RESOLUTION_FAILED` | after correction | Correct repo/ref/authority. | Locator/authority decision. |
| Host launch failure | `OMNIGENT_HOST_LAUNCH_FAILED` | bounded | Retry the identical snapshot or remediate policy. | Snapshot/lease/diagnostics. |
| Registration timeout | `OMNIGENT_HOST_REGISTRATION_TIMEOUT` | bounded | Repair registration/network readiness. | Lease/deadline/observations. |
| Bridge authorization | `OMNIGENT_BRIDGE_AUTHORIZATION_FAILED` | after correction | Repair the exact binding. | Binding/host/auth refs. |
| Ambiguous first message | `OMNIGENT_FIRST_MESSAGE_AMBIGUOUS` | no auto-repost | Reconcile idempotency evidence. | Key and request observations. |
| Cleanup failure | `OMNIGENT_CLEANUP_FAILED` | yes | Reconcile only lease-owned resources. | Inventory/attempts. |
| Evidence publication | `OMNIGENT_EVIDENCE_PUBLICATION_FAILED` | yes | Retry idempotent publication. | Manifest attempt/primary result. |

## 7. Dependency map for parent issues 2–8

| Slice | Consumes | Produces |
| --- | --- | --- |
| 2 — Create controls | Label, selector, eligibility states, UI field set. | Valid v1 Create input. |
| 3 — API compilation | Identity, validation, no-host-ID rule. | Request and `WorkspaceLocator`. |
| 4 — Profile/policy | Versioned refs, modes, bootstrap rule. | Effective launch snapshot. |
| 5 — Workspace/authority | Repo, inputs, Skills, publish authority. | Authorized daemon mount. |
| 6 — Host/bridge | Snapshot, profile, readiness and first-message rules. | Session/lifecycle evidence. |
| 7 — Detail/control | Stages, refs, controls and terminal rules. | Operator projection. |
| 8 — Conformance | Failure codes, evidence, no-fallback and sequence. | Static/on-demand end-to-end proof. |

Dependency order is 2 → 3 → (4 and 5) → 6 → 7 → 8. Slices 4 and 5 may proceed independently after compilation; slice 6 requires both. No slice may invent a competing identity or wire field.

## 8. Acceptance invariants

- The only product compilation is `external/omnigent` with nested `codex-native`.
- UI intent and trusted authority are separated by the field table.
- Static/on-demand selection uses versioned product refs, not environment-only authority.
- Every authority handoff emits safe evidence; cleanup precedes final profile lease release.
- Explicit Omnigent selection is fail-closed with no silent fallback.
- The five v1 shapes are sufficient inputs for implementation slices 2–8.
