# Omnigent Execution Configurations

Status: **Desired-State Design**  
Document Class: System / Feature Design View
Owners: MoonMind Engineering  
Issue: MoonLadderStudios/MoonMind#3517  
Last updated: 2026-09-30

## Implementation status

This document defines the target contract. The repository currently implements the persistent profile/version/audit/usage records, lifecycle API, bounded upstream projection, metadata and archive-content validation, an explicit snapshot-resolution API, an operator-triggered bounded smoke-validation endpoint with lease cleanup and secret-scanned diagnostics, and durable bootstrap materialization. The observed stock `codex-native-ui` identity is seeded as an explicit active default bootstrap profile after it passes structural readiness. Unless generic Claude admission is explicitly stopped, the deployment also seeds a compatible managed Claude configuration and launch policy. `OMNIGENT_DEFAULT_AGENT_NAME` remains an optional first-start override when durable state is absent; its use is recorded and durable conflicts fail closed.

Dashboard profile management, readiness-aware workflow and schedule selectors, transactional immutable snapshots, bounded bundle import, smoke validation, and durable bootstrap authority are implemented. Checkpoint-branch and remediation authoring preserve the originating immutable agent-profile and Provider Profile selection so continuation cannot silently substitute runtime authority.

The adopted [Harness, Provider Profile, and Backend design](../UI/HarnessProfileBackendSelection.md) replaces the former visible Runtime authoring requirement below. This is a desired product contract, not a claim that the terminology, controls, or list projection have already shipped. Existing serialized fields and historical snapshots keep their meaning.

Configuration support and source implementation do not establish exact-host, native-chat, recovery, or protected-live qualification. The [primary-backend product outcomes](PrimaryRuntimeProviderStrategy.md#11-seven-required-product-outcomes) govern the complete journey. Qualification and default promotion use their existing evidence owners, not the existence of a saved configuration or a closed implementation issue.

## Purpose and identities

An Omnigent Agent Profile is MoonMind-owned reusable configuration. It does not create a new runtime identity: dispatch remains `agentKind=external`, `agentId=omnigent`. An upstream agent id and version, or an immutable bundle artifact and digest, identify provider content. A Provider Profile identifies credential and capacity materialization. An execution profile and launch policy identify host realization. These identities are separate and a display name is never identity.

Every execution resolves these references into a secret-free immutable snapshot before launch. The snapshot, rather than the mutable active profile pointer or current upstream inventory, remains the authority for retries, history, native Workflow Chat, checkpoint branches, and evidence.

### One account Profile, subordinate execution configuration

Ordinary authoring pairs **Harness** with one **Provider Profile** control selecting the existing Provider Profile. **Backend** identifies Omnigent, not the profile's owning harness. The internal Agent Profile supplies reusable execution configuration, not a second account, credential enrollment, or required workflow-authoring choice. Distinct persisted identities are not instructions to create distinct editable controls.

| Selected account Profile | Corresponding Omnigent harness | Credential materializer |
| --- | --- | --- |
| Codex OAuth, `codex_cli` / `openai` | `codex-native` | `codex-oauth-home@1` |
| Claude Code OAuth, `claude_code` / `anthropic` | `claude-native` | `claude-oauth-home@1` |
| Keyed OpenCode Go | `opencode-native` | `opencode-auth-json@1` |
| Credentialless OpenCode Zen | `opencode-native` | `none@1` |

These are compatibility relationships, not a blanket support claim. The selected configuration, materializer, Host Class, launch policy, model and exact support evidence still have to admit the execution. A shared upstream provider name alone cannot establish harness or backend compatibility.

Codex and Claude OAuth Profiles retain their underlying runtime IDs, account identities, enrollment-owned credential homes, generations and capacity authority when execution uses Omnigent. The stored profile `runtime_id` expresses harness ownership in the adopted vocabulary. Do not rewrite those Profiles to `runtime_id=omnigent`, create duplicate Omnigent-only accounts, copy OAuth homes, or start a second interactive login. Direct compatibility and Omnigent consumers must honor the same credential-resource lease owner. Supporting both paths does not authorize concurrent writers to a mutable OAuth home. Credentialless OpenCode creates no dummy secret and cannot inherit the keyed route's account or billing authority.

Compatible configuration resolves automatically from an explicit Profile pin, an authorized compatible default, or the sole compatible option through the existing resolver. A genuine ambiguity has an actionable Profile Settings remedy. Normal account setup must not require understanding Host Classes, materializers, realizer versions, or multiple Profile types. Optional advanced pins and policy overrides must not turn into mandatory setup for every ordinary account.

The execution description is read-only and comes from the resolved configuration, for example `Uses Claude Code through Omnigent`. Adding a supported harness extends the existing Harness choice and compatible-profile projection, not another ordinary selector or frontend selection authority. The final admission boundary independently verifies the same selection.

## Persistence and lifecycle

A stable `profileId` owns monotonically numbered immutable versions. Each version stores canonical JSON, a SHA-256 digest, parent/clone/supersedes lineage, upstream metadata at selection time, validation results, rollout metadata, actor, and timestamp. Editing always creates a version. Activation only moves the stable profile's active pointer. Disablement and deprecation block new selection without deleting versions or historical snapshots. Deletion is permitted only for an unused draft; referenced profiles and versions are retained.

The version document includes endpoint and bridge-mode refs; stable upstream or artifact-backed bundle identity; harness and capabilities; execution and allowed launch policies; credential-free Provider Profile compatibility requirements; legacy model and effort settings (new Profile launches resolve these from Profile tiers and explicit overrides); workspace mutation and capability constraints; Skills and tools; capture, retention, and evidence defaults and ceilings; continuation compatibility; publish default; and versioned policy ref.

MoonMind no longer provides native retrieval (MoonLadderStudios/MoonMind#4103). A version recorded earlier may carry a `rag` section; it stays readable with its recorded digest but is never compiled into launch parameters. New authored versions drop an empty `rag` section and reject explicit retrieval values, and their write schemas allow only an absent or null `rag`. Clones and automatic policy-cutover successors omit historical retrieval settings and recompute their own digests; the source versions and in-flight usages remain unchanged. Selection rejects an explicit `rag` override.

Profiles never contain credentials, OAuth homes, registration secrets, Dockerfiles, host paths, volume names, host ids, or privileged launch settings.

## Discovery and launch security

MoonMind synchronizes the stock `/v1/agents` built-in catalog through its authenticated bridge boundary into a bounded last-known projection keyed by endpoint plus stable upstream id and version. The stock catalog's session bindability is projected as the canonical `session.start` capability. MoonMind records harness, capabilities, health, provenance, compatibility, successful-sync time, attempt time, and redacted error state. An outage retains the prior snapshot but marks it stale. Missing or incompatible agents block new launches; historical snapshots remain readable.

The API refreshes discovery every two minutes independently of credential leases,
provider validation, and deployment qualification. Each refresh has a fifteen-second
deadline covering HTTP pagination and persistence. Authoring refreshes stale or
missing discovery once before admission, then revalidates the exact selected
upstream identity and version. Refresh commits only discovery in its own transaction;
it cannot commit partial workflow authoring, change a selected profile, or replace
an unavailable version. Failed refreshes remain actionable admission failures.

The upstream harness picker omits native wrappers, so an authenticated stock
agent observation supplies each wrapper's harness record in the same persisted
catalog snapshot: `codex-native-ui` supplies `codex-native`, and, unless generic
Claude admission is explicitly stopped, `claude-native-ui` supplies
`claude-native`. The picker's `codex` row is the separate CLI-subprocess harness
and never stands in for `codex-native`. Each wrapper record is present only
while its stock agent is observed.

Workflow, schedule, checkpoint-branch, and remediation authoring expose
**Harness** and one **Provider Profile** selection. The selected/default profile
establishes the compatible initial harness. **Backend** is normally Omnigent and
is not an ordinary required selector. A genuine supported alternative appears
under Advanced, remains disclosed when selected, and is not reset by collapsing
Advanced. With only one supported backend, omit the selector and show recorded
Backend in execution details. Container and Host remain diagnostic resource terms.

The existing Provider Profile identity owns account, model tiers and capacity,
and optionally pins an immutable execution configuration. Configuration versions
remain an advanced implementation contract, not a second required profile
selector. Automatic resolution selects a compatible deployment default or the
sole compatible configuration under the authored harness/backend constraints.
Ambiguity has an actionable Provider Profile Settings remedy. Neither control
silently changes the selected account to repair an incompatible pair. Execution
configuration and technical target remain subordinate resolved values.

A pinned version remains selected after its active version changes. Existing
`task.runtime.executionConfiguration` id/version/digest expectations keep their
meaning at their schema owner. The final admission boundary composes the same
selection and independently rejects meaningful harness/profile/backend conflicts
and changed pinned references before effects. Preserve authored, defaulted, and
inherited Backend intent separately from the resolved configuration. A hidden
normal default is not an explicit override, and omission must not accidentally
select the legacy direct path. Clients that omit an expectation still use the
same resolver. Do not extend expectation checks into image-equality or
advisory-preview freshness gates. Historical snapshots remain authoritative for
existing runs.

Model and effort authoring follows the shared Tier/Custom contract, not a second
set of hard-override or Tier fallback controls under Advanced. Supported
host-policy specialization stays with its existing owner rather than becoming a
required setup chain. The Workflows list presents recorded Provider Profile with
optional secondary Harness instead of exposing the Backend as its primary identity.

Discovery freshness never filters Profile inventory. Submission persists the
profile id/version/digest, upstream snapshot, Provider Profile id, execution and
policy refs, and effective model/workspace/capture values. Overrides are
accepted only after policy validation.

## Native Workflow Chat capability authority

The native Omnigent UI may present a session bound to a MoonMind Workflow Execution, but the upstream UI is not an independent source of authority.

For each native Workflow Chat binding, MoonMind derives an effective capability projection as the intersection of:

```text
upstream agent and session capabilities
∩ immutable Agent Profile snapshot
∩ Provider Profile and effective launch policy
∩ Workflow and Step state
∩ caller permission
```

The native client uses that projection to hide or disable unavailable controls. The bridge independently recomputes and enforces the same intersection for every HTTP, SSE, WebSocket, message, approval, resource, terminal, and control request. Client-side filtering is never the security boundary.

Profile and policy invariants include:

- a pinned model cannot be replaced from the native model selector,
- a pinned reasoning effort cannot be changed from the native effort selector,
- session, terminal, browser, file-write, workspace-mutation, tool, Skill, network, publish, and resource capabilities cannot exceed the immutable snapshot,
- approval or elicitation resolution requires both the profile's approval policy and the caller's MoonMind approval authority,
- clear/reset, interrupt, stop, cancel, cleanup, reconnect, model, effort, goal, terminal, and workspace controls remain separately capability-gated,
- upstream support for a control is evidence of technical availability, not permission to use it,
- a stale profile generation, Provider Profile generation, or effective launch snapshot fails closed rather than silently adopting current upstream defaults.

Every mutating native control must retain:

- actor identity,
- MoonMind idempotency key,
- expected workflow, run, Step Execution, bridge session, provider session, session epoch, and active turn as applicable,
- immutable Agent Profile and policy refs,
- normalized outcome and upstream correlation,
- durable audit reference.

An approval or control observation without this evidence is diagnostic input only and cannot become authoritative workflow or side-effect evidence.

## Bundle and validation boundary

Custom bundles are MoonMind artifacts with immutable content digest, content type, size, provenance, optional license, creator, schema version, and declared capabilities. Validation rejects traversal and forbidden paths, secrets, executable setup, Dockerfiles, privileged assumptions, and unsupported host capabilities. Publishing to an endpoint records its result without changing the profile-to-artifact relationship.

Smoke validation is an operator-triggered bounded preflight. It checks endpoint, exact source, capabilities, Provider Profile readiness/capacity, compiled policy, host mode, image/network/workspace constraints, Skills/tools, capture settings, and the strongest safe session-start check. Diagnostics are bounded and secret-scanned. Cancellation, failure, and timeout release only validation-owned leases and resources. A pass is readiness evidence, not a workflow-success guarantee.

Native Workflow Chat validation also proves that the binding-scoped facade can project and enforce the immutable capabilities rather than exposing unfiltered upstream controls.

## Bootstrap

The synchronized stock `codex-native-ui` identity is materialized as an explicit active bootstrap profile version after structural readiness passes. `OMNIGENT_DEFAULT_AGENT_NAME` may override that first-start selector only when durable state is absent in bootstrap/local development; its use is recorded. Durable state wins, and conflicts fail closed.

Unless `MOONMIND_OMNIGENT_GENERIC_CLAUDE_QUALIFIED=false` explicitly stops it (the default is true), policy reconciliation uses the deployment-resolved shared host image digest to activate `claude-on-demand`. Inventory reconciliation then binds an observed, structurally ready `claude-native-ui` identity to `omnigent-claude-default` with `claude_code` / `anthropic` OAuth requirements. This managed configuration is a fallback for a compatible Provider Profile; an explicit pin or compatible operator-authored configuration keeps selection authority. Image, policy, inventory, provider readiness, and rollout gates still apply independently. A connected Anthropic OAuth Profile needs no manual rollout toggle, but it does not bypass those checks or exact-host attestation.

## Deployment-managed default authority

Exactly one profile holds `default_for_runtime`, and one boundary decides which MoonMind-managed profile that is. The default workflow Backend is Omnigent with OpenCode as the default Harness, so the built-in OpenCode profile `omnigent-opencode-default` holds the deployment default whenever its active version is launch ready and its observed upstream identity satisfies the document contract. The Codex bootstrap profile `omnigent-bootstrap-default` is the fallback and holds the default only while the OpenCode built-in cannot launch — for example when `MOONMIND_OMNIGENT_OPENCODE_ENABLED=false`.

This describes deployment-managed configuration default selection, not recovery permission. It applies only where no authored account/configuration constrains the choice and the existing admission policy authorizes the resulting combination. It cannot replace an explicitly selected Provider Profile, pinned configuration, model or billing route, rescue a failed admitted execution, or change an active plan. An unavailable selected route receives the appropriate setup, support or waiting result. Default repair is not permission for runtime or credential fallback.

Explicit authority is never displaced:

- an operator-authored profile that holds the default keeps it;
- an operator `make_default` selection on a managed profile keeps it; and
- `OMNIGENT_DEFAULT_AGENT_NAME` preserves the current default, because it selects the agent identity itself.

Every transfer is recorded as a `managed_default_selected` audit event carrying the previous holder.

A default launch resolves its Provider Profile from the default profile's own contract. A v2 profile declares accepted providers through its credential slots, so the default is the highest-ranked accepted Provider Profile the selected harness can materialize under every launch policy the document allows. On the default deployment path that is the credentialless `opencode-zen-free` seed, which holds the `opencode` runtime default (see [OpenCode Host](OpenCodeHost.md)). A v1 profile keeps pinning one credential contract through `providerRequirements`.
