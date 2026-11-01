# Omnigent Execution Configurations

Status: **Current**  
Document Class: System / Feature Design View
Owners: MoonMind Engineering  
Last updated: 2026-10-16

## Purpose and identities

An Omnigent Agent Profile is MoonMind-owned reusable configuration. It does not create a new runtime identity: dispatch remains `agentKind=external`, `agentId=omnigent`. An upstream agent id and version, or an immutable bundle artifact and digest, identify provider content. A Provider Profile identifies credential and capacity materialization. An execution profile and launch policy identify host realization. These identities are separate and a display name is never identity.

Every execution resolves these references into a secret-free immutable snapshot before launch. The snapshot, rather than the mutable active profile pointer or current upstream inventory, remains the authority for retries, history, native Workflow Chat, checkpoint branches, remediation, and evidence, so continuation cannot silently substitute runtime authority. A saved configuration is not exact-host, native-chat, recovery, or live qualification; those use their evidence owners and the [product outcomes](PrimaryRuntimeProviderStrategy.md#11-seven-required-product-outcomes).

### One account Profile, subordinate execution configuration

Workflow, schedule, checkpoint-branch, and remediation authoring pair **Harness** with one **Provider Profile**; the selected/default profile establishes the compatible initial harness. **Backend** is Omnigent, not the profile's owning harness, and is not an ordinary control: a supported direct compatibility Backend appears only under Advanced and stays summarized when Advanced collapses ([selection design](../UI/HarnessProfileBackendSelection.md)). The internal Agent Profile supplies reusable execution configuration, not a second account, credential enrollment, or required authoring choice. Distinct persisted identities are not instructions to create distinct editable controls.

| Selected account Profile | Corresponding Omnigent harness | Credential materializer |
| --- | --- | --- |
| Codex OAuth, `codex_cli` / `openai` | `codex-native` | `codex-oauth-home@1` |
| Codex API key, `codex_cli` / `openai` | `codex-native` | `omnigent-provider-config@1` |
| Claude Code OAuth, `claude_code` / `anthropic` | `claude-native` | `claude-oauth-home@1` |
| Claude Code API key, `claude_code` / `anthropic` | `claude-native` | `omnigent-provider-config@1` |
| Keyed OpenCode Go | `opencode-native` | `opencode-auth-json@1` |
| Credentialless OpenCode Zen | `opencode-native` | `none@1` |

An API-key Codex or Claude Profile uses `omnigent-provider-config@1` when the shared host can carry its declared launch settings. Claude supports one key SecretRef, its endpoint/model pins and declared non-secret environment tuning, including MiniMax's environment bundle. Codex supports `OPENAI_API_KEY` with optional `OPENAI_BASE_URL`, or one Responses provider in a private `CODEX_HOME/config.toml`, authenticated by a declared key SecretRef. The file route preserves the selected config profile's model/reasoning settings and explicit request, stream-retry and idle limits after Omnigent generates its native auth table. Inline authentication, extra files and unrelated configuration stay on their direct route with a concrete reason. Every endpoint must satisfy the existing egress policy. Managed API-key, Claude environment-bundle and Codex file-config configurations resolve through the existing reconciler. Only the unchanged obsolete managed MiniMax recipe is rendered with Responses wire and explicit model/provider selection; authored configurations are never rewritten. Routing is decided at admission, so queued work retains its recorded route and stored snapshots keep their original bytes.

These are compatibility relationships, not a blanket support claim. The selected configuration, materializer, Host Class, launch policy, model and exact support evidence still have to admit the execution. A shared upstream provider name alone cannot establish harness or backend compatibility.

Codex and Claude OAuth Profiles retain their underlying runtime IDs, account identities, enrollment-owned credential homes, generations and capacity authority when execution uses Omnigent. The stored profile `runtime_id` expresses harness ownership in the adopted vocabulary. Do not rewrite those Profiles to `runtime_id=omnigent`, create duplicate Omnigent-only accounts, copy OAuth homes, or start a second interactive login. Direct compatibility and Omnigent consumers must honor the same credential-resource lease owner. Supporting both paths does not authorize concurrent writers to a mutable OAuth home. Credentialless OpenCode creates no dummy secret and cannot inherit the keyed route's account or billing authority.

Compatible configuration resolves automatically from an explicit Profile pin, an authorized compatible default, or the sole compatible option under the authored harness/backend constraints. A genuine ambiguity has an actionable Provider Profile Settings remedy, and neither control silently changes the selected account to repair an incompatible pair. Normal account setup must not require understanding Host Classes, materializers, realizer versions, or multiple Profile types, and optional pins and policy overrides must not become mandatory setup.

For explicit Codex `reuse_session_same_epoch`, Temporal carries the completed context's RuntimeBinding reference. The successor verifies its workflow, harness, endpoint and credential authority, then forks the native conversation through Omnigent. Omnigent can clone a retained local transcript or rebuild it from durable conversation items after host loss. The successor owns a new canonical session, host allocation and credential runtime; source history and cleanup evidence stay immutable. Interrupted setup reconciles the attached fork before dispatch; an attempt that creates no session retains its admitted context for retry. The native executable refuses a fresh start when the requested context was not restored. `fresh_agent_run` starts a new context epoch, and missing or unverified history produces a visible diagnostic.

Adding a supported harness extends the existing Harness choice and compatible-profile projection, not another ordinary selector or frontend selection authority. The final admission boundary independently verifies the same selection.

## Persistence and lifecycle

A stable `profileId` owns monotonically numbered immutable versions. Each version stores canonical JSON, a SHA-256 digest, parent/clone/supersedes lineage, upstream metadata at selection time, validation results, rollout metadata, actor, and timestamp. Editing always creates a version. Activation only moves the stable profile's active pointer. Disablement and deprecation block new selection without deleting versions or historical snapshots. Deletion is permitted only for an unused draft; referenced profiles and versions are retained.

The version document includes endpoint and bridge-mode refs; stable upstream or artifact-backed bundle identity; harness and capabilities; execution and allowed launch policies; credential-free Provider Profile compatibility requirements; legacy model and effort settings (new Profile launches resolve these from Profile tiers and explicit overrides); workspace mutation and capability constraints; Skills and tools; capture, retention, and evidence defaults and ceilings; continuation compatibility; publish default; and versioned policy ref.

MoonMind does not provide native retrieval. An older recorded version may carry a `rag` section; it stays readable with its recorded digest but is never compiled into launch parameters. New authored versions drop an empty `rag` section and reject explicit retrieval values, and their write schemas allow only an absent or null `rag`. Clones and automatic policy-cutover successors omit historical retrieval settings and recompute their own digests; the source versions and in-flight usages remain unchanged. Selection rejects an explicit `rag` override.

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

A Provider Profile owns account, model tiers, and capacity, and may pin an
immutable execution configuration; configuration versions are an advanced
implementation contract, not a second profile selector. A pinned version remains selected after its active version changes. Existing
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
required setup chain.

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

Custom bundles are immutable MoonMind artifacts. Planning verifies the selected stored profile version before granting its bundle to the current Step Execution, including retained schedule occurrences; creator provenance and raw-content restrictions remain intact. Validation rejects traversal, secrets, executable setup, Dockerfiles, privileged assumptions, and unsupported host capabilities. Endpoint publication retains the profile-to-artifact relationship.

Smoke validation is an operator-triggered bounded preflight. It checks endpoint, exact source, capabilities, Provider Profile readiness/capacity, compiled policy, host mode, image/network/workspace constraints, Skills/tools, capture settings, and the strongest safe session-start check. Diagnostics are bounded and secret-scanned. Cancellation, failure, and timeout release only validation-owned leases and resources. A pass is readiness evidence, not a workflow-success guarantee.

Native Workflow Chat validation also proves that the binding-scoped facade can project and enforce the immutable capabilities rather than exposing unfiltered upstream controls.

## Bootstrap

The synchronized stock `codex-native-ui` identity is materialized as an explicit active bootstrap profile version after structural readiness passes. `OMNIGENT_DEFAULT_AGENT_NAME` may override that first-start selector only when durable state is absent in bootstrap/local development; its use is recorded. Durable state wins, and conflicts fail closed.

Unless `MOONMIND_OMNIGENT_GENERIC_CLAUDE_QUALIFIED=false` explicitly stops it (the default is true), policy reconciliation uses the deployment-resolved shared host image digest to activate `claude-on-demand`. Inventory reconciliation then binds an observed, structurally ready `claude-native-ui` identity to `omnigent-claude-default` with `claude_code` / `anthropic` OAuth requirements. This managed configuration serves a compatible Provider Profile that pins none; an explicit pin or compatible operator-authored configuration keeps selection authority. Image, policy, inventory, provider readiness, and rollout gates still apply independently. A connected Anthropic OAuth Profile needs no manual rollout toggle, but it does not bypass those checks or exact-host attestation.

## Deployment-managed default authority

Exactly one profile holds `default_for_runtime`, and one boundary decides which MoonMind-managed profile that is. The default workflow Backend is Omnigent with OpenCode as the default Harness, so the built-in OpenCode profile `omnigent-opencode-default` holds the deployment default whenever its active version is launch ready and its observed upstream identity satisfies the document contract. The Codex bootstrap profile `omnigent-bootstrap-default` is the fallback and holds the default only while the OpenCode built-in cannot launch — for example when `MOONMIND_OMNIGENT_OPENCODE_ENABLED=false`.

This describes deployment-managed configuration default selection, not recovery permission. It applies only where no authored account/configuration constrains the choice and the existing admission policy authorizes the resulting combination. It cannot replace an explicitly selected Provider Profile, pinned configuration, model or billing route, rescue a failed admitted execution, or change an active plan. An unavailable selected route receives the appropriate setup, support or waiting result. Default repair is not permission for runtime or credential fallback.

Explicit authority is never displaced:

- an operator-authored profile that holds the default keeps it;
- an operator `make_default` selection on a managed profile keeps it; and
- `OMNIGENT_DEFAULT_AGENT_NAME` preserves the current default, because it selects the agent identity itself.

Every transfer is recorded as a `managed_default_selected` audit event carrying the previous holder.

A default launch resolves its Provider Profile from the default profile's own contract. A v2 profile declares accepted providers through its credential slots, so the default is the highest-ranked accepted Provider Profile the selected harness can materialize under every launch policy the document allows. On the default deployment path that is the credentialless `opencode-zen-free` seed, which holds the `opencode` runtime default (see [OpenCode Host](OpenCodeHost.md)). A v1 profile keeps pinning one credential contract through `providerRequirements`.
