# Normal Codex-through-Omnigent product-path reconciliation

**Document Class:** Canonical declarative
**Status:** Accepted
**Owners:** MoonMind Platform
**Last updated:** 2026-10-14
**Authority:** Reconciliation index that pins one internally consistent normal
Codex-through-Omnigent product path across its canonical owners. It does not
redefine any owned contract; each stage names the document that owns it.
**Traceability:** MoonLadderStudios/MoonMind#3565 (Omnigent Milestone 1, item 1.0
declarative reconciliation)

This document reconciles the **Codex via Omnigent** journey across its existing
owners. Ordinary admission is certificate independent (#4560); explicitly
selected strict certification retains the evidence requirements in
[Conformance and Live Smoke](ConformanceAndLiveSmoke.md#deployed-release-support-evidence).
New-work selection uses the shared rollout boundary without the retired Codex
phase inputs (#3931), as described in [Runtime Provider Rollout](RuntimeProviderRollout.md#12-configuration).
The v1 cutover evidence and phase examples below describe the retained diagnostic
projection and historical interpretation, not ordinary startup instructions.
Where a detail is owned elsewhere, that owner
governs it and this reconciliation must stay current.

## 1. Universal product identity

Every canonical document that participates in this path names the same identity.
The normal `/workflows/new` choice is **Codex via Omnigent**. Workflow Create
writes `runtime.mode = "omnigent"`, and trusted runtime compilation — never any
browser input — converts that selector to the canonical external-agent identity.
The API compiler persists the immutable workflow parameters and workspace intent;
the Temporal workflow's trusted compiler
(`MoonMindRunWorkflow._build_agent_execution_request`) then derives `agentKind`,
`agentId`, and the nested harness from the plan node's `runtime.mode` and
constructs the durable `AgentExecutionRequest`:

```text
agentKind = external
agentId   = omnigent
harness   = codex-native
```

`codex-native` is a nested harness choice, never a second top-level agent
identity. `omnigent_codex`, caller-authored `session.hostId`, and direct-Codex
substitution are invalid. Omnigent-specific authored values live under
`parameters.omnigent`. This identity is owned by
[CodexCreateToHostContract.md §1](./CodexCreateToHostContract.md#1-product-identity-and-authority)
and [OmnigentAdapter.md](./OmnigentAdapter.md); every other document in the path —
[WorkspaceLocators.md](../Workflows/WorkspaceLocators.md),
[ProviderProfiles.md](../Security/ProviderProfiles.md),
[SettingsSystem.md](../Security/SettingsSystem.md),
[WorkflowDetailsPage.md](../UI/WorkflowDetailsPage.md),
[ManagedAndExternalAgentExecutionModel.md](../Temporal/ManagedAndExternalAgentExecutionModel.md),
[OmnigentBridge.md](./OmnigentBridge.md),
[OmnigentHostOAuth.md](./OmnigentHostOAuth.md),
[CombinedStackValidationAndRollback.md](./CombinedStackValidationAndRollback.md),
and [CodexSupportAndCutover.md](./CodexSupportAndCutover.md) — refers to this
identity and never invents a competing one.

## 2. Field authority: browser-authored versus trusted-runtime-resolved

The browser authors intent only. Every runtime identity, credential, path, host,
session, lease, and snapshot is resolved by trusted runtime owners. This is the
single reconciled enumeration; the owned detail table is
[CodexCreateToHostContract.md §3](./CodexCreateToHostContract.md#3-field-authority-and-workspace).

| Field | Browser-authored intent | Trusted-runtime resolution | Resolving owner |
| --- | --- | --- | --- |
| runtime | `mode=omnigent` | `external/omnigent` + nested `codex-native` | Trusted runtime compiler (API persists params/intent; Temporal workflow derives the `AgentExecutionRequest` identity) |
| profile/policy | eligible versioned refs | eligibility, generations, leases, effective launch snapshot | Provider Profile Manager, launcher |
| repository | repository ref and branch/ref | checkout and canonical `WorkspaceLocator` | Workspace owner |
| publish | bounded publish mode + GitHub authority ref | credentials and bounded mutation scope | Workspace/publication owner |
| inputs | attachment refs, Skill names, instructions, capture options | artifact authorization and immutable resolved Skill set | Artifact + Skill resolution |
| runtime identity | none | endpoint, binding, host, bridge/session, lease and container refs | Host owner + Bridge |
| paths/secrets | never | containment-checked daemon mount and boundary-only credentials | Workspace owner + Secrets |

Browser-authored absolute paths, bind sources, manual host IDs, credential
bodies, and broad mutation authority are forbidden inputs and are rejected before
compilation.

## 3. The normative end-to-end authority sequence

This is the one normative sequence for the normal product path. Every stage names
its trusted owner, the immutable refs/snapshots it carries, its retry/idempotency
boundary, its failure behavior, its evidence, and the caller authority it
prohibits. Actual admission/effect dependencies remain with those owners;
the historical certification stage is not an ordinary execution prerequisite.
Provider Profile capacity is acquired before
any workspace side effect — matching the owning Create-to-host sequence in
[CodexCreateToHostContract.md §5](./CodexCreateToHostContract.md#5-journey-and-ownership),
so no workspace materialization runs for a request that cannot acquire capacity.
Repository publication or a durable saved-work handoff precedes cleanup that
releases the authoritative workspace. Provider Profile capacity and credentials
are released only after owned cleanup and confirmed consumer shutdown under the
existing owner. Reporting or non-sensitive retention already independent of that
workspace does not require keeping capacity occupied.

| # | Stage | Trusted owner | Immutable refs / snapshots | Retry / idempotency boundary | Failure behavior | Evidence | Prohibited caller authority |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Browser-authored normal Workflow request | Workflow Create UI | `omnigent-create-host/v1` create input | Re-submit authors a new request; no side effects yet | Client validation error; nothing persisted | Create input envelope | No runtime identity, host ID, path, or credential authoring |
| 2 | Immutable input + workspace-intent compilation | API compiler | Persisted workflow parameters (including `runtime.mode`), `WorkspaceLocator`, resolved Skill set ref | Deterministic recompile of the same input yields the same refs | Fail-closed rejection with a stable code; no Temporal submission | Persisted parameters + locator refs | Cannot author `external/omnigent`, `session.hostId`, or raw JSON |
| 3 | Policy / profile / effective-launch resolution | Trusted launcher | Immutable `effectiveLaunchSnapshot` (schema version, profile, generation, mode, digest, limits, network/egress, mounts, capture) | Retries reuse the snapshot only while the credential generation is current | `OMNIGENT_LAUNCH_POLICY_INVALID` / `OMNIGENT_PROFILE_GENERATION_STALE`; stale generation forces reconciliation | Effective launch snapshot | No environment-only host authority; `OMNIGENT_CODEX_HOST_LAUNCH_PROFILE` is bootstrap only |
| 4 | Provider Profile capacity + host lease acquisition | Provider Profile Manager | `executionProfileRef`, credential generation, host lease ref | Lease acquisition is idempotent per correlation; a busy profile waits on the same lease | `OMNIGENT_PROFILE_UNAVAILABLE` / `OMNIGENT_PROFILE_BUSY`; never selects another profile | Safe profile/lease/capacity refs | No profile substitution; no concurrent consumption of the mutable OAuth identity |
| 5 | Owning-worker authorization + workspace materialization | Workspace owner | `WorkspaceLocator`, daemon-visible mount at `/workspaces/run` | Only the owning worker resolves a locator; resolution is idempotent under containment checks | `OMNIGENT_WORKSPACE_RESOLUTION_FAILED` after correction | Locator/authority decision | No cross-worker resolution, absolute path, or bind source |
| 6 | Static or on-demand stock host realization | Host owner | Effective launch snapshot, image digest, host lease | Bounded launch retry reuses the identical snapshot; one lease-owned host | `OMNIGENT_HOST_LAUNCH_FAILED` bounded; the unchosen mode is never used as fallback | Snapshot/lease/diagnostics | No arbitrary static host binding; no caller-selected host mode override |
| 7 | `codex-native` readiness, bridge authorization, session creation, first-message commitment | Host owner + Omnigent Bridge | Registration evidence, `bridgeSessionRef`, `omnigentSessionRef`, idempotency key | Registration is bounded; the first message is posted exactly once by idempotency key | `OMNIGENT_HOST_REGISTRATION_TIMEOUT` / `OMNIGENT_BRIDGE_AUTHORIZATION_FAILED` / `OMNIGENT_FIRST_MESSAGE_AMBIGUOUS` (no auto-repost) | Binding/host/auth refs + key observations | No caller-authored session/host binding or duplicate first-message post |
| 8 | Live + replayable event / resource / artifact projection | Omnigent Bridge | Durable event journal, cursor/page/SSE projection, resource refs | Replay from the durable journal is deterministic; cursors are idempotent | Projection continues from the journal; gaps are reported, never silently filled | Event journal + resource refs | No second-source runtime dashboard; no caller-authored event stream |
| 9 | Repository candidate, publication, or terminal saved-work handling | Workspace / publication owner | `WorkspaceLocator`, `githubAuthorityRef`, bounded publish mode, publication result ref | Publication is idempotent within the bounded mutation scope; failed publication preserves saved work | `OMNIGENT_REPOSITORY_PUBLICATION_FAILED`; confirmed saves remain recoverable | Publication result + saved-work refs | No broad mutation authority beyond the authored bounded scope |
| 10 | Terminal harvest, cleanup / janitor reconciliation, Provider Profile release | Artifact owner + Host owner + Provider Profile Manager | Terminal envelope, capture manifest ref, cleanup evidence, lease release status | Harvest and cleanup are idempotent; janitor reconciles only lease-owned resources | `OMNIGENT_CLEANUP_FAILED` / `OMNIGENT_EVIDENCE_PUBLICATION_FAILED` retryable; auxiliary failure never overwrites `primaryStatus` | Terminal envelope + cleanup/janitor evidence | No reconciliation of resources the lease does not own; no release before cleanup |
| 11 | Optional observations, explicit strict certification, and retained v1 cutover diagnostics/evidence | Existing evidence resolver + conformance/cutover readers | Actual report subject/digests; phase fields still feed the catalog's `cutover` projection and retain historical meaning | Ordinary admission can record an uncertified tier; explicit strict certification validates its required evidence | Invalid required strict evidence fails closed; unavailable optional observations do not veto ordinary admission | Provenance-bound evidence for the scope actually observed | No caller-driven certification downgrade or fabricated support claim |

Owners: Create owns intent; the API compiler owns immutable input and
workspace-intent construction while the Temporal workflow compiles the
`AgentExecutionRequest` identity; the Workspace owner owns checkout/mount; the
Provider Profile Manager owns eligibility,
credentials, and capacity; the Host owner owns policy realization, readiness, and
cleanup; the Bridge owns authorization, session, first-message idempotency, and
events; the artifact owner owns evidence; the conformance/evidence owners own
certification and retained phase diagnostics; Temporal owns ordering and retries. This
reconciles the ownership prose in
[CodexCreateToHostContract.md §5](./CodexCreateToHostContract.md#5-journey-and-ownership)
and the cutover authority in
[CodexSupportAndCutover.md](./CodexSupportAndCutover.md).

## 4. Wire-example index and the reconciled additions

Versioned, redacted examples for stages 1–8 are owned by
[CodexCreateToHostContract.md §4](./CodexCreateToHostContract.md#4-versioned-wire-examples)
(Create input, persisted workflow parameters, `AgentExecutionRequest`, effective
launch snapshot, Workflow Detail projection). This section adds the reconciled
examples for the stages that previously carried only prose: host/session/bridge
authorization, repository/publication result, per-row and combined protected
evidence, and readiness/cutover status. All refs are opaque safe identifiers; no
credential body, path, or provider payload appears.

### 4.1 Host / session / bridge authorization

```json
{
  "schemaVersion": "omnigent-create-host/v1",
  "hostLeaseRef": "host-lease:01",
  "mode": "on_demand_docker",
  "registration": {"status": "ready", "harness": "codex-native"},
  "bridgeSessionRef": "bridge-session:01",
  "omnigentSessionRef": "omnigent-session:01",
  "firstMessage": {"idempotencyKey": "workflow:run_01:step_01:attempt_1", "committed": true}
}
```

### 4.2 Repository / publication result

```json
{
  "schemaVersion": "omnigent-create-host/v1",
  "workspaceLocator": {"kind": "sandbox", "workspaceId": "ws_01", "subpath": "."},
  "publishMode": "branch",
  "githubAuthorityRef": "github-authority:repo-branch-write:v2",
  "publication": {"status": "published", "resultRef": "publication-result:01", "mutationScope": "repo-branch-write"},
  "savedWork": {"outputRefs": ["artifact:final-snapshot-01"], "released": false}
}
```

### 4.3 Per-row and combined protected evidence

This retained v1 example describes historical protected cutover evidence, not a
certificate every ordinary workflow must obtain. Its original subject and digest
semantics remain with the strict/historical readers.

```json
{
  "perRow": {
    "schemaVersion": "moonmind.codex-omnigent-cutover-artifact/v1",
    "kind": "submissionMatrix",
    "matrixRows": ["submission.create"],
    "passed": true,
    "observations": {"createSubmissionAccepted": true, "firstMessageCommitted": true}
  },
  "combined": {
    "contractVersion": "moonmind.codex-omnigent-cutover/v1",
    "matrixDigest": "sha256:example-matrix-digest",
    "requiredRowsCovered": true,
    "thresholds": {"withinLimits": true}
  }
}
```

### 4.4 Readiness and cutover status

```json
{
  "policyVersion": "moonmind.codex-omnigent-cutover/v1",
  "configuredPhase": "opt_in",
  "deployedPhase": "opt_in",
  "phase": "opt_in",
  "promotionAllowed": false,
  "directLaunchAllowed": true,
  "blockers": ["protected_live_matrix_pending"],
  "evidenceRefs": []
}
```

The example above illustrates the retained v1 phase projection. The existing
`/api/omnigent/codex-catalog-readiness` handler still calls `effective_phase()` and
exposes its result under `cutover`; the complete response also reports current
readiness. Its configured/deployed phase inputs and evidence validation remain
owned by [Codex Support and Cutover](CodexSupportAndCutover.md#versioned-rollout).
They do not select current Create/schedule defaults; that boundary belongs to
[Runtime Provider Rollout](RuntimeProviderRollout.md). Do not rewrite old phase
observations to imply certification or a deployed cutover that did not occur.

## 5. Complete failure and evidence matrix

An explicit Omnigent selection never silently runs through direct Codex, another Provider Profile, another host mode, an arbitrary static host, or a broader network/mount policy. This is the single reconciled matrix covering every owned
failure across the end-to-end path, including the profile busy/stale and
unsupported-runtime cases named by the owning sequence. The `OMNIGENT_*` codes for the pre-support stages are owned by
[CodexCreateToHostContract.md §6](./CodexCreateToHostContract.md#6-failure-and-no-fallback-matrix);
strict certification and historical cutover failures are owned by
[CodexSupportAndCutover.md](./CodexSupportAndCutover.md).

| Stage | Stable code | Retryable | Remediation | Evidence requirement |
| --- | --- | --- | --- | --- |
| Unsupported runtime | `OMNIGENT_RUNTIME_UNSUPPORTED` | no | Select a supported runtime | Selector/schema |
| Profile / policy denial | `OMNIGENT_PROFILE_UNAVAILABLE` / `OMNIGENT_LAUNCH_POLICY_INVALID` | after action | Connect/select the exact eligible profile or valid refs | Safe profile ref/reason or refs/diagnostics |
| Busy profile | `OMNIGENT_PROFILE_BUSY` | yes | Wait or retry the same profile | Capacity/lease state |
| Stale credential generation | `OMNIGENT_PROFILE_GENERATION_STALE` | after action | Drain or reconnect the profile | Expected/observed generation |
| Workspace denial | `OMNIGENT_WORKSPACE_RESOLUTION_FAILED` | after correction | Correct repo/ref/authority | Locator/authority decision |
| Host launch | `OMNIGENT_HOST_LAUNCH_FAILED` | bounded | Retry the identical snapshot or remediate policy | Snapshot/lease/diagnostics |
| Registration / readiness | `OMNIGENT_HOST_REGISTRATION_TIMEOUT` | bounded | Repair registration/network readiness | Lease/deadline/observations |
| Bridge / session | `OMNIGENT_BRIDGE_AUTHORIZATION_FAILED` | after correction | Repair the exact binding | Binding/host/auth refs |
| First message | `OMNIGENT_FIRST_MESSAGE_AMBIGUOUS` | no auto-repost | Reconcile idempotency evidence | Key and request observations |
| Repository / publication | `OMNIGENT_REPOSITORY_PUBLICATION_FAILED` | yes | Retry idempotent publication within the bounded scope | Publication attempt + saved-work refs |
| Terminal harvest | `OMNIGENT_EVIDENCE_PUBLICATION_FAILED` | yes | Retry idempotent publication | Manifest attempt/primary result |
| Cleanup / janitor | `OMNIGENT_CLEANUP_FAILED` | yes | Reconcile only lease-owned resources | Inventory/attempts |
| Evidence validation | `OMNIGENT_SUPPORT_EVIDENCE_INVALID` | after action | Regenerate the protected artifact for the exact row/kind | Provenance-bound manifest with matching SHA-256 |
| Support classification | `OMNIGENT_SUPPORT_ROW_UNPROVEN` | no | Publish the missing protected row before claiming support | Passing per-row artifact for the exact combination |
| Cutover promotion | `OMNIGENT_CUTOVER_PROMOTION_BLOCKED` | no | Supply fresh, version-matched, complete evidence | One-step promotion document + digest-bound manifest |

A denied or failed explicit Omnigent selection is an error at every stage; it
never invokes direct Codex or a broader authority.

## 6. Evidence-qualified support language

This reconciliation names the evidence-qualified vocabulary the path uses. The
owning support matrix's descriptive status cells map onto these states rather than
repeating these exact words, and no weaker state is written as a stronger one:

- **designed** — target state described; no implementation asserted.
- **implemented foundation** — code exists as substrate; not proof of behavior.
- **repository-verifiable or hermetically verified** — a hermetic test or
  repository artifact proves the row without external credentials.
- **protected-live verified** — an independently resolvable, secret-scanned,
  protected live-run artifact proves the exact combination.
- **supported** — the support matrix links passing evidence (hermetic where the
  row allows it, protected-live where the row requires it) for that exact
  combination.
- **default** — the surviving rollout-selection policy selects this path for new work; selection is not certification.
- **deprecated-disabled** — retained for compatibility, scheduling disabled.
- **retired** — removed after history, rollback, and release gates pass.

The original Milestone 1 protected browser-to-host matrix (#3564/#3508) proves
only the subject and operations it observed. It does not establish another
credential protocol, recovery journey, or deployed migration. Current support
evidence and optional remediation observations remain with
[CodexSupportAndCutover.md](CodexSupportAndCutover.md) and the conformance owner.
Preserve strict v1 decoding and evidence-qualified claims without turning the old
matrix into ordinary admission authority or rebuilding removed native retrieval.

## 7. Reconciliation and cutover state

Create and schedules use `resolve_new_work_selection` and the shared
`resolve_runtime_target_selection` boundary. They do not read
`MOONMIND_CODEX_OMNIGENT_CUTOVER_PHASE` or
`MOONMIND_CODEX_OMNIGENT_DEPLOYED_PHASE`. Existing enablement/promotion, explicit
rollback stops, direct-retirement cutoff, and class-based admission checks keep
their real consumers; see [Runtime Provider Rollout](RuntimeProviderRollout.md).

Omitted, blank, or `MOONMIND_OMNIGENT_EVIDENCE_POLICY=either` is ordinary admission
and may record an uncertified tier. Explicit `protected` or `deployment` retains
strict validation. The retained `opt_in` diagnostic phase and phase-advancement examples
are not ordinary setup steps. Actual protected-live evidence remains necessary
for the live certification it claims; this documentation change neither certifies
a deployment nor closes separately owned live-evidence work.

## 8. Acceptance invariants

- The enforced identity set — this reconciliation,
  [CodexCreateToHostContract.md](./CodexCreateToHostContract.md),
  [WorkspaceLocators.md](../Workflows/WorkspaceLocators.md),
  [ProviderProfiles.md](../Security/ProviderProfiles.md),
  [SettingsSystem.md](../Security/SettingsSystem.md), and
  [WorkflowDetailsPage.md](../UI/WorkflowDetailsPage.md) — names the identical
  `external/omnigent` + nested `codex-native` identity; every other linked path
  document refers to this canonical identity and never invents a competing one.
- No caller authors host, daemon, session, path, or credential authority.
- Explicit Omnigent selection is fail-closed with no silent fallback.
- Publication or durable saved-work handoff precedes authoritative workspace
  cleanup. Provider Profile capacity is released after owned cleanup and confirmed
  credential-consumer shutdown; auxiliary failure never overwrites `primaryStatus`.
- Support language is evidence-qualified; no row is promoted to **supported**
  without the protected evidence its row requires.
- Explicit strict certification retains its evidence validation and integrity
  checks; historical phase records keep their meaning without gating ordinary admission.
