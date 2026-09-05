# Repository Access and Workspace Design

**Document Class:** Canonical declarative  
**Viewpoint:** System / Feature Design View  
**Status:** Proposed  
**Updated:** 2026-09-04  
**Audience:** Workflow and runtime authors, integration authors, security reviewers, operators, and dashboard contributors  
**Authority:** Feature-level target behavior for optional repository access, connection-bound authentication, durable workspace results, and independently authorized publication  
**Owning Surface:** Workflow admission, repository access, workspace materialization, publishing, Secrets System, and runtime integration boundaries  
**Related Docs:** [MoonMind Architecture](MoonMindArchitecture.md), [Workflow Architecture](Workflows/WorkflowArchitecture.md), [Lore VCS Integration](Workflows/LoreVcsIntegrationDesign.md), [Workflow Publishing](Workflows/WorkflowPublishing.md), [Workspace Locators](Workflows/WorkspaceLocators.md), [Secrets System](Security/SecretsSystem.md), [Provider Profiles](Security/ProviderProfiles.md), [Omnigent Harness Platform](Omnigent/OmnigentHarnessPlatformDesign.md), [OpenCode Host](Omnigent/OpenCodeHost.md), [MoonSpec Document Model](Workflows/MoonSpecDocumentModel.md)  
**Related Implementation:** [`repository_contract.py`](../moonmind/workflows/executions/repository_contract.py), [`workspace_intent.py`](../moonmind/omnigent/workspace_intent.py), [`moonmind/auth/`](../moonmind/auth/), [`moonmind/publish/`](../moonmind/publish/), [`moonmind/omnigent/host_services/`](../moonmind/omnigent/host_services/), and [`SecretsService`](../api_service/services/secrets.py)

> This document describes proposed desired state, not implemented API support or deployment evidence. It expresses the consolidated multi-PAT and workspace-decoupling design. Sequencing, current-state findings, migration inventories, and qualification procedures live in the [temporary implementation plan](tmp/RepositoryAccessAndWorkspaceDecouplingPlan.md), which derives from this design rather than defining a competing target.

## Advance organizer

**One sentence:** Repository access is an optional execution capability, while saved work is durable independently of repository credentials and remote publication.

**One paragraph:** A repository is one workspace source, GitHub is one Git hosting service, a PAT is one authentication mechanism, and a pull request is one publication result. None is a prerequisite for agent compute. MoonMind prepares a contained scratch, repository, artifact, checkpoint, or explicitly authorized existing workspace, runs the selected agent, and preserves useful output through its artifact and checkpoint systems. Named `RepositoryConnection` records select repository authority deterministically. Immediate and deferred publication use that same authority boundary. Model Provider Profiles, repository credentials, registry authentication, and artifact permissions remain separate.

## 1. Purpose and ownership

### DOC-REQ-001 Repository-independent work is a complete product path

A supported execution can produce, download, and continue useful work without selecting a repository or configuring a GitHub PAT. Private access and remote mutation require suitable repository authority, but that authority is not necessarily a PAT. Public reads can use explicitly anonymous access. Publication can be requested with execution or independently after results have been saved.

The design establishes one consumer boundary for repository authentication. PATs and expiring installation credentials differ behind acquisition and delivery adapters, not throughout cloning, hosting-service tools, agent launch, publishing, and recovery. Additional providers can require new capabilities without requiring consumers to resume global token discovery.

### CONTRACT-001 Existing modules retain their responsibilities

| Responsibility | Owner and relationship |
| --- | --- |
| Authored and resolved repository targets, endpoint/client policy, connections, and repository capabilities | Existing workflow repository contracts. `provider: git \| lore` retains its VCS meaning. A hosting-service discriminator does not replace it. |
| Source intent and contained workspace ownership | Existing execution/workspace-intent contracts and workspace materializers. Scratch does not introduce a parallel workspace hierarchy. |
| Secret references, encrypted storage, revision lifecycle, and secret-use authorization | Secrets System. Connections reference it and do not copy token values into another store. |
| Model account, model policy, runtime profile, and model capacity | Provider Profiles. Repository identities are not model-provider profiles. |
| Immutable execution bindings, runtime delivery, attestation, and cleanup | Existing managed and Omnigent execution boundaries. Repository bindings extend the existing envelope. |
| Saved content, checkpoints, access control, retention, and restore | Artifact and checkpoint systems. A saved-work manifest indexes their evidence rather than replacing them. |
| Candidate construction, remote mutation, and publication evidence | Existing publisher and repository-provider adapters. Deferred publication is not a second publishing engine. |
| Image acquisition | Deployment registry authentication. Source credentials are not a registry fallback. |

This view owns the feature's desired integration behavior. Formal module interfaces remain with their providing modules. It does not duplicate the Secrets System parser, Lore source-authority rules, Skill semantics, or provider-specific publication evidence schemas.

The target explicitly extends repository-required authoring and remote-only recovery assumptions. `none` preserves results without a final repository publication, and verified artifact storage provides the credentialless durability handoff. These are proposed changes to the owning contracts and guidance, not claims that an existing mandatory gate may already be bypassed. Settled architecture is promoted into its owning views under the [Documentation Architecture Standard](DocumentationArchitecture.md).

### INV-001 Authority is never inferred from an available credential

A scratch execution without repository tools or publication has no repository target, repository credential binding, GitHub resolver call, `gh` requirement, or GitHub readiness probe. Local Git history does not create a remote identity or push permission.

Model authentication, repository reads, local workspace mutation, repository mutation, artifact access, and registry access are distinct. Adding a PAT cannot change model billing or data-use policy. Restoring a workspace cannot restore an external authorization grant.

Missing, ambiguous, disabled, revoked, or insufficient authority never causes credential shopping, anonymous downgrade, source substitution, or a broader execution path. The rule is independent of the number of configured connections.

## 2. Product behavior

### DOC-REQ-002 Workspace source and publication are independent choices

| User objective | Workspace and access | Durable result |
| --- | --- | --- |
| Create a report, prototype, or code from scratch | Contained scratch workspace, no repository access | Report and supported saved-content formats |
| Modify an uploaded project | Authorized immutable artifact import | Updated saved content with import provenance |
| Continue previous work | Authorized checkpoint or saved-content restore | New results under a fresh execution owner |
| Analyze or modify a public repository locally | Explicit anonymous repository read | Saved report, content, and applicable delta/history exports |
| Work with a private repository | Selected connection with admitted source operations | Saved work independent of subsequent token availability |
| Publish now or later | Separately admitted destination and mutation operations | Verified branch or PR evidence linked to saved work |
| Use an existing local workspace | Explicit locator and ownership grant | Policy-approved outputs without an arbitrary host-path shortcut |

A task requesting immediate remote publication is not silently converted into save-only work when credentials are missing. The operator can choose that adaptation explicitly. A save-only task is not blocked by hypothetical future publication requirements.

The default experience is a new blank workspace, results saved in MoonMind, no publication, and the existing automatic agent default. Free/anonymous model availability is governed by Section 11, not by repository setup.

## 3. Authoring and capability contracts

### CONTRACT-002 Exactly one workspace source is active

`workspaceSource` is a discriminated union within the existing execution contract:

| Kind | Authority and semantics |
| --- | --- |
| `scratch` | Runtime-generated sandbox ownership. No source repository. Local Git is optional and output-driven. |
| `repository` | One authored repository target with explicit access intent, prepared into a contained checkout. |
| `artifact` | Authorized immutable artifact reference and digest, safely imported into a new sandbox. |
| `checkpoint` | Authorized checkpoint reference and supported restore contract. Workspace restore is separate from provider-session reattachment. |
| `existing_workspace` | Server-issued workspace locator and ownership grant. An advanced capability, not a raw server path. |

The source `repository` is present only for a repository source. Repository provenance within an imported artifact is evidence, not a live source binding. A publication destination is another role using the same repository-target type, not another independently authored alias for the source.

The following shapes express desired authoring semantics. They are not complete wire-schema definitions or claims about today's API:

```yaml
workspaceSource:
  kind: scratch
outputPolicy:
  savedWork: required
  formatProfile: portable
publish:
  mode: none
```

```yaml
workspaceSource:
  kind: repository
repository:
  provider: git
  repository:
    name: MoonLadderStudios/MoonMind
  accessMode: anonymous
outputPolicy:
  savedWork: required
  formatProfile: portable
publish:
  mode: none
```

The top-level authored `repository` compiles to the existing runtime `repositoryTarget` projection. Worker `publishMode` is likewise a compilation result, not another input authority. Conflicting new-write aliases are rejected before external access.

An omitted source branch requests the remote default branch. The trusted preparation boundary records the observed branch and exact revision rather than assuming `main`. Immutable Git/Lore revision selectors retain their provider-specific semantics. Empty remotes, missing branches, and inaccessible repositories remain distinct outcomes.

### CONTRACT-003 Authentication intent precedes acquisition

| `accessMode` | Authored `connectionRef` | Meaning |
| --- | --- | --- |
| `anonymous` | Forbidden | Supported read operations with no credential lookup or injection |
| `routed` | Absent | Deterministic selection among connections the principal may use |
| `explicit` | Required | Validate and use only the named connection |

Anonymous access is not an error fallback. A public-URL import can author it explicitly. A connected repository picker normally authors routed access. Omitted values and documented UI defaults compile to the same intent without inspecting token presence.

The resolved binding records selection origin separately from authored intent. An automatically inserted `repository-connection:git-default` must not become an apparent explicit user choice before routing. Resolved output is not reinterpreted as authored input.

### CONTRACT-004 Capabilities derive from the requested work

The shared capability compiler derives requirements from source kind, resolved Skill and tool declarations, output format, and publication policy. Launchers consume that result rather than implement independent permission engines.

Scratch reports need neither Git nor `gh`. Scratch Git exports require local Git but no remote credential. Anonymous Git input requires approved transport and repository-read capability, not an unrelated GitHub user or PR API probe. Branch publication requires branch/write authority without automatically demanding PR or issue-write permission.

`publish.mode=none` disables final repository publication, not all task side effects. An issue-editing Skill still needs its declared issue authority. `auto` uses the resolved Skill's declared operations and terminal evidence contract. Publication mode does not select a native reimplementation of Skill behavior.

Friendly indexing, readiness, publish, and full-PR-automation profiles are presets for versioned capability bundles, not an ordered privilege ladder. Workflow-file writes, issue writes, reviews, checks, and merges are required only where declared behavior needs them.

### INV-002 Explicit publication intent is preserved

The publication modes remain `none`, `branch`, `pr`, and agent-owned `auto`. A new explicit `none` cannot silently become `auto`. An incompatible Skill selection receives a visible correction before submission. Historical decoding does not reopen that normalization for new authoring.

Omitted publication mode follows the selected Skill's documented default. Blank-workspace defaults resolve to `none`; a Skill declaring agent-owned publication can resolve omission to `auto`. The effective objective and required authority are visible before submission.

## 4. Repository connections and routing

### CONTRACT-005 RepositoryConnection is the single connection domain

A persistent `RepositoryConnection` owns its stable identity, display name, VCS provider, hosting service where applicable, trusted endpoint, client policy, scoped principal-use policy, allowed operations, typed credential configuration, and lifecycle metadata. It is not mirrored by an independently writable filesystem record or a separate `SourceControlConnection` domain. **Source Control** is the Settings page name.

Normalized repository assignments support many repositories per connection and several eligible connections per repository. They carry repository identity, operation policy, revision, and routing defaults. The authenticated actor is distinct from the resource owner or installation.

Repository identity is namespaced by hosting endpoint. A provider repository ID is used where observed, with mutable names retained for display and lookup. Generic Git uses normalized endpoint/remote identity without fabricated GitHub identifiers. Verified renames reconcile aliases; resource-owner transfers require policy revalidation.

System/workspace ownership and secret-use authorization apply from the first supported connection. User-owned connections and complex inheritance are not prerequisites. Discovery, probing, selection, secret attachment, rotation, and deletion enforce the same scope. Knowing a secret reference does not authorize using it.

There is at most one default for a scope, repository, and applicable route/capability bundle. Database constraints and transactional writes enforce that rule, including concurrent administrator edits.

### CONTRACT-006 Authentication is a typed acquisition concern

Connection authentication uses discriminated configuration:

| Variant | Durable configuration |
| --- | --- |
| GitHub PAT | Existing typed SecretRef and fine-grained/classic subtype |
| GitHub App installation | App definition reference and installation identity |
| Lore credential | Existing provider-specific credential policy |

The authenticated PAT capability includes fine-grained and classic PATs. Raw values remain in the Secrets System and trusted delivery boundaries. Connections do not introduce another secret parser, user-profile PAT column, or nullable collection of unrelated authentication fields.

Acquisition supports expiring issuance independently of PAT lifetime. Consumers use returned scope and expiry rather than hardcoded token-lifetime assumptions. An App adapter supplies enrollment and acquisition without changing every consumer.

OAuth/device enrollment and SSH can be additional qualified adapters. Enrollment method does not imply token lifetime. SSH repository transport does not grant hosting-service issue or PR API authority. Collaboration operations need their own admitted role or an explicit unsupported-capability result.

GitHub.com is the initial GitHub endpoint. Enterprise and other-host support require administrator-controlled endpoint/TLS policy and qualified adapters, not a free-form credential destination exposed to ordinary connection creators.

### CONTRACT-007 Selection is deterministic for each declared role

Selection receives target identity, requested capabilities, access mode, requesting principal/workspace, role, and policy version. Anonymous access admits only supported read operations and creates no dummy connection.

For connection-backed access, the principal is authorized before candidate identities or discovery results are exposed. An explicit reference is validated without alternatives. Routed access selects the sole metadata-eligible route or the single applicable default; missing or ambiguous authority is actionable failure. Only the selected credential is acquired or probed.

The selected route and policy are persisted before repository access. Exact provider identity and source observations are verified through that selection before mutation. A failing selected route is not removed so another PAT can be tried. Authentication errors, access denial, a hidden-or-missing repository, network failure, and throttling never reroute the execution.

A simple same-repository workflow normally uses one connection for its declared operations. Separate source, collaboration, and destination identities require explicit role policy. Batch children resolve their own repository authority or inherit a verified compatible binding, never a parent's raw PAT.

### QUALITY-001 Validation evidence is not an authorization grant

| Dimension | Meaning |
| --- | --- |
| MoonMind authorization | Mandatory policy describing permitted repositories and operations |
| Observed provider capability | Repository/operation evidence with `verified`, `denied`, `unknown`, or `stale` status |
| Authentication condition | Validity, expiry, revocation, approval requirement, or unknown condition |
| Operational condition | Throttling or temporary unavailability without a change of identity |

Evidence is keyed to endpoint/repository, credential revision, binding/policy revision, capability-definition version, and observation time. A repository-specific failure does not invalidate unrelated bindings unless the credential itself is invalid.

Test Connection is non-mutating. Public read success is not proof of private access or mutation permission. Read probes do not label writes verified, and endpoint-required permissions are not treated as granted permissions. An active write test names its side effect and requires explicit approval. Unknown evidence follows a declared policy without silently granting authority or being mislabeled denied.

## 5. Immutable admission and credential lifecycle

### CONTRACT-008 Bindings carry authority, not secret material

The existing versioned credential-binding envelope distinguishes model and repository authorities with a typed union:

```text
ModelAuthorityBinding
  authorityKind: provider_profile
  providerProfileRef
  materializerRef

RepositoryAuthorityBinding
  authorityKind: repository_connection
  connectionRef
  repositoryAccessSnapshotRef
  materializerRef
```

An immutable repository-access snapshot identifies endpoint/repository, declared role, admitted operations, policy/binding revision, selection origin, and principal/workspace scope. Existing binding-set version/digest infrastructure supplies its immutable linkage. Several mutable plan records do not independently own copies of connection policy.

Anonymous access has an access snapshot but no credential binding. Scratch has neither repository entry. A credentialless model still uses its normal Provider Profile and `none@1` materializer. Lease, continuation, child planning, serialization, and cleanup dispatch by authority kind. Repository access does not inherit model-account exclusivity, capacity, or cooldown rules.

Admission/acquisition belong in Activities or trusted services. Temporal workflow code carries compact references and orchestrates deterministic waits and retries; it does not select mutable defaults, inspect credential environments, or fetch model catalogs during replay.

### CONTRACT-009 Connection, revision, and issuance have different lifetimes

| Identity | Meaning |
| --- | --- |
| Connection | Stable authorized account or installation relationship |
| Credential revision | Particular PAT or underlying authentication configuration |
| Runtime issuance | Concrete credential material supplied to an owning attempt |

Acquisition persists the owner, connection, selected revision, binding digest, and issuance metadata before exposing material. Transactional or compare-and-set checks prevent a rotation race from attributing a new PAT to an old revision. Current disable and revocation state are checked at acquisition and controlled remote-operation boundaries.

A candidate PAT is validated against expected actor/resource-owner policy before atomic activation and revision advancement. Failed candidate validation leaves the working credential usable. Successful rotation invalidates affected capability evidence and fences attempts on the old revision. Those attempts preserve their work and can resume through an explicitly authorized new attempt using a validated replacement on the same connection.

The default policy does not promise retrieval of overwritten PATs. Any bounded old-revision retention belongs inside Managed Secrets, not a source-control-only version store. Direct secret edits use the same revision/invalidation contract. Secret-use tracking and deletion protection include connection consumers and metadata-only audit events.

### INV-003 Refresh preserves authority and revocation remains effective

An expiring issuance can refresh for the same admitted connection, revision, installation, repositories, and operations. Refresh is not credential rotation or permission to broaden scope. Caches include scope and revision; expiry margins, bounded retry, and single-flight refresh prevent issuance storms.

Changed defaults never reroute active work. Explicit disable or revocation denies new acquisition and controlled remote operations even for an immutable plan. Runtime handles require independently authenticated execution/service ownership and are not bearer capabilities.

A locally disabled connection cannot retract a broad PAT already copied by arbitrary agent code. Local disable, provider-side revocation, and proven mediated confinement are distinct guarantees. The UI and audit trail do not imply otherwise.

### QUALITY-002 Throttling preserves identity

Rate-limit coordination uses endpoint, resource bucket, and the appropriate actor, installation, or anonymous shared-IP identity. Several PAT connections for the same actor are not assumed to provide independent budgets. Retry/reset evidence drives bounded Temporal waits or retryable outcomes, not credential invalidation or substitution.

## 6. Runtime delivery and security

### CONTRACT-010 Repository consumers use bound access clients

Transport and hosting-service API adapters consume admitted access plus authenticated execution ownership. They do not accept an optional PAT and independently discover credentials when it is absent. A hosting capability unavailable through the chosen adapter yields an explicit capability error, not an irrelevant PAT prompt.

The local managed broker and Omnigent lease-owned materializer share admission/acquisition semantics while retaining delivery mechanisms appropriate to their runtime. Registry authentication stays within image acquisition. A source PAT is never reused as a GHCR fallback.

### INV-004 Ambient identity cannot override admitted identity

Git and hosting CLI execution isolate home/configuration state and scrub token variables, inherited authorization headers, credential helpers, `.netrc`, and login caches unless explicitly supplied by the admitted adapter. Helpers validate protocol, host, and repository path and use path-sensitive matching where required. Environment credentials cannot override selected `gh` configuration.

Credential-free remotes are the only remotes persisted in workspaces. Raw material is excluded from arguments, Docker metadata, labels, durable environments, Temporal payloads/heartbeats, serialized errors, plan digests, logs, checkpoints, and exported artifacts. Secret-carrying types remain inside trusted boundaries and are redacted/non-serializable by default.

Delivery objects belong to an attempt and use generation-fenced cleanup. Stale cleanup cannot remove a newer attempt's projection or issuance. Anonymous access creates no token file, login, or credential materializer.

Where a CLI requires authentication for a declared operation, the selected runtime either provides an explicitly supported portable unauthenticated interface or rejects the combination. It does not fabricate credentials or move a Skill's semantic behavior into a parallel native implementation.

### QUALITY-003 Routing and confinement are separate guarantees

The routing guarantee covers MoonMind-controlled operations: they use the admitted repository, operation, and identity. A raw PAT exposed to agent code retains its provider-granted scope, even if MoonMind's routing allowlist is narrower.

Confinement against arbitrary code requires credentials limited to the admitted scope or mediation that exposes no broader credential and has no credential/network bypass. A token-returning broker or agent-readable `gh` configuration alone does not prove confinement. `publish.mode=none` is not a sandbox when the agent can access a broad write token.

MoonMind-managed publication keeps destination write credentials outside the agent and acquires them at the publisher boundary. Agent-owned `auto` execution needs a qualified mediated path or an explicitly accepted credential-exposing policy. High-security mode rejects unsupported confinement rather than reducing isolation silently.

### INV-005 Anonymous access does not remove source-safety controls

Endpoint/egress policy governs authenticated and anonymous access. Embedded credentials, lookalike hosts, unsafe protocols, unapproved private-network destinations, and unvalidated redirects are rejected. Redirect targets are revalidated and credentials are not forwarded across hosts.

Repository-controlled hooks, filters, submodules, LFS endpoints, and additional fetches do not create implicit authority. Dependencies are separately admitted or reported unsupported/incomplete before execution. Required content cannot be silently skipped while claiming a complete workspace.

An anonymous failure is not always an authentication requirement. Diagnostics distinguish a known challenge, inaccessible-or-missing repository, missing branch, empty remote, transport failure, and unsafe source. Ambiguous provider responses remain explicitly uncertain.

## 7. Workspace preparation and restoration

### CONTRACT-011 Every source uses the same contained workspace lifecycle

All source kinds use server-generated locators, authoritative workspace storage, ownership handoff, quotas, cleanup authority, and supported remote-Docker path translation. Preparation is idempotent: staged content and ownership are verified before completion is recorded. An existing directory is not evidence of a finished clone/import, and partially prepared content is not launched.

Scratch code initializes local Git only when its output/checkpoint profile needs it, with a neutral local identity, empty baseline, and no remote. Report-only work can remain an ordinary directory. Non-Git checkpoints do not fabricate commits or repository IDs to satisfy a Git-shaped schema.

Artifact and checkpoint import verify authorization and digest, bound expanded size/file count, and reject traversal, escaping links, device files, and privileged metadata. Imported executable content and Git configuration remain untrusted. Runtime credential paths, hooks, helpers, and old session authority are not restored.

### INV-006 Content restoration does not restore external authority

A self-contained saved result restores under artifact authorization without its original repository PAT. Thin bundles, missing LFS objects, and unmaterialized dependencies are labeled incomplete. Required dependencies need fresh explicit authorization or the claimed portable restore is rejected.

Once an exact source snapshot is prepared, local compute and save-only finalization do not reacquire source credentials unless a declared operation needs them. Source-token expiry alone does not discard authorized local work. Current execution policy still governs whether a disabled execution can continue.

Continuation creates a fresh execution owner and re-admits requested external operations. Workspace restoration and provider-session reattachment are separately qualified capabilities. Restored content never revives leases, approvals, or permission to repeat external side effects.

## 8. Saved work and finalization

### CONTRACT-012 Saved work is an immutable artifact-backed result

The saved-work manifest is a compact index of verified artifact/checkpoint evidence, not a second storage service. A suitable existing checkpoint archive is reused rather than duplicated. The manifest records:

| Evidence | Contents |
| --- | --- |
| Identity and provenance | Schema version/digest, workflow/run/step/attempt, source kind, immutable source identity |
| Content | Snapshot/checkpoint ref, capture-contract version, file manifest, content digest, report ref |
| Optional Git exports | Meaningful baseline/head, binary-safe delta, selected history/bundle refs |
| Completeness and safety | Available/inapplicable/incomplete formats, exclusions, validation/scan evidence |
| Ownership and retention | Artifact scope, retention policy, required baseline/dependency references |

No live token, connection handle, session credential, or executable approval is present. A source connection ID may be audit provenance without granting access. Publication records reference the immutable saved-work digest instead of editing the original result.

### QUALITY-004 Portable outputs are useful, bounded, and honest

An output policy selects a small supported format profile instead of independent booleans that allow unusable combinations. A portable workspace-producing result includes verified content and a manifest. Its report uses actual agent output or a factual execution summary. A required-format failure blocks successful save finalization; an inapplicable optional format is not a broken download.

Patches express binary-safe changes against an exact meaningful baseline, including additions, modifications, deletions, and renames. Git bundles preserve only approved necessary refs/history, are validated, and state whether they are self-contained. Neither Git nor a bundle is required for every result.

Capture includes meaningful untracked output and records exclusions. It does not unconditionally stage the live workspace, bundle every ref, or rewrite agent history merely to provide a download. Approved capture-owned snapshots/indexes are used when commits are needed. Reachable history, binaries, and external object dependencies are part of export safety, not only the current worktree.

MoonMind-issued credentials are forbidden in exports. Required outbound controls cover reports, snapshots, deltas, bundles, and publication. Unsafe recoverable content is quarantined/restricted with safe diagnostics, not exposed or silently discarded. Source confidentiality continues to govern artifact access and model data-use policy.

### INV-007 Verified saving precedes destructive cleanup

The finalization handoff is:

```text
quiesced workspace
  -> approved captured snapshot
  -> verified required artifact objects
  -> committed immutable manifest/checkpoint references
  -> recorded save outcome
  -> optional admitted publication
  -> remaining workspace/cleanup release
```

This is a durability dependency, not a second publisher. Agent-owned `auto` mutations remain owned by the Skill during execution; finalization validates their canonical evidence and preserves results without replaying the Skill's decisions.

Compute, save, and publication outcomes remain separate. Failed/cancelled compute can still save useful work without becoming successful compute. Publication failure does not overwrite verified compute/save evidence. Process exit, prose, or a dashboard projection is not proof of durable saving or remote publication.

Save failure retries the same capture with stable idempotency and retains the authoritative workspace under bounded recovery. Ordinary cleanup cannot destroy the only copy because a report or status projection failed. Credentials no longer needed are released independently of workspace retention.

A credentialless execution completes its durability handoff through verified artifact storage. A remote recovery branch requires an already admitted destination and mutation authority. Missing GitHub credentials are not a reason to skip saving or acquire an unrequested remote identity.

### QUALITY-005 Retention and access follow saved-work ownership

Download, preview, signed-URL, restore, and publication authorization derive from artifact ownership and explicit policy, not possession of an old PAT. Manifest-reachable objects and required baselines remain retained for the declared saved-work lifetime. Expired or deleted results are reported honestly.

Quotas, bounded failed-work retention, and cleanup ownership prevent unbounded storage growth. Recovery state distinguishes a retained live workspace from verified saved artifacts. A failed save is never shown as durable merely because a local path still exists.

## 9. Independently authorized publication

### CONTRACT-013 Publish Saved Work uses the existing publisher

Publication-only execution accepts an immutable `savedWorkRef`, an authorized destination target, supported existing publication mode, and explicit application policy. It does not rerun the agent by default and does not introduce a separate publication token setting.

The saved result's authorization, digest, and completeness are verified. Destination repository/branch, connection, operations, client/policy snapshot, and remote expectation are newly admitted. A clean contained workspace receives content without old credentials or approvals. Candidate construction is deterministic, provenance-preserving, and scanned under current destination policy.

Mutation uses the existing protected-branch and compare-and-set/lease controls. The exact remote revision is verified, and a PR is created or adopted only when requested and supported. Canonical provider-aware publication evidence links the saved-work digest and destination authority. A self-contained result requires no original source PAT.

### INV-008 Destination relationships determine safe application

| Relationship | Application rule |
| --- | --- |
| Same repository, known baseline | Apply the recorded delta against the admitted destination base; expose conflicts and changed remote tips. |
| Existing unrelated repository | Previewed additive/path-mapped import by default, without inferred deletions or whole-tree replacement. |
| Explicitly selected empty repository | Compatible saved history may be pushed only after verifying emptiness and permitted branch policy. |
| Lore-authoritative destination | Lore performs authoritative publication; GitHub remains a review projection. |

Absence of a file in scratch output is not an instruction to delete it from an existing repository. Deletions require baseline evidence or an explicitly approved import manifest. Conflicting paths, case collisions, ambiguous renames, executable-bit changes, and binary conflicts are surfaced. Unrelated root histories are not merged automatically.

Repository creation is a separate authorization capability. Separate source/destination roles do not revive conflicting branch aliases within one role. PR head generation and protected branches retain the owning publisher's rules.

### QUALITY-006 Publication recovery reconciles exact remote evidence

Publication idempotency includes saved-work digest, admitted destination, application strategy, and intended branch. Candidate identity and observed remote expectation are persisted before mutation. A lost push or PR-create response leads to exact remote reconciliation, not blind repetition.

A stale baseline or conflict blocks publication while preserving original saved work. Replanning onto a newer base produces a new candidate/attempt and required approval. Changing connection or destination requires re-admission. The original saved artifact and compute outcome are immutable.

Ordinary `auto` execution remains Skill-owned. Publication-only recovery does not rerun or replace its semantic decisions, accept another attempt's stale evidence, or claim that an independently authorized branch publication completes unrelated Skill side effects.

## 10. Compatibility and observability

### CONTRACT-014 Historical interpretation is explicit and bounded

Changed contracts are versioned at their actual owning boundary, not duplicated as a parallel `RepositoryTargetV2` domain. Historical binding loaders verify original parsing and digest rules before interpretation. New-write producers use one canonical contract and reject historical aliases.

Recorded workflows retain compatible deterministic decisions and worker support. Versioned execution cannot fall back to a singleton resolver. `repository-connection:git-default` remains the compatibility identity for an explicitly bound effective legacy source, not a live chain of guessed credentials. Detailed migration and retirement mechanics belong in the temporary plan.

### QUALITY-007 Failures identify the responsible boundary

Diagnostics identify model eligibility, workspace preparation, repository routing, credential acquisition, capability evidence, artifact saving, or publication as the failing boundary. Expired credentials, organization approval, unknown write evidence, ambiguous routing, throttling, unsafe content, and incomplete restore are not compressed into one Disconnected state.

Audit evidence correlates execution/attempt, principal/workspace, role, connection and credential revision, policy/binding digest, issuance lifecycle, source/candidate identity, and outcome. Values remain secret-free. UI projections consume authoritative records and cannot replace primary success with auxiliary projection lag.

## 11. Credential-independent model compute

### DOC-REQ-003 The model default remains independent of repository setup

The credentialless OpenCode route retains the `opencode-zen-free` identity and `none@1` materializer, separate from keyed profiles. Explicit operator disable/default choices are preserved. `OPENCODE_API_KEY` is not permission to turn an anonymous execution into a paid/keyed one. A new anonymous auth-state enum or duplicate free profile is not required by this design.

Readiness belongs to the qualified profile, runtime pack, Host Class, and exact image. A shared image or seeded database row alone is not evidence that the complete execution path works.

### INV-009 Free-model selection cannot change cost or privacy implicitly

Automatic free selection uses the exact runtime's observed, qualified catalog. Eligible candidates have known zero cost across relevant billing dimensions, required capabilities, availability, and acceptable data-use terms. Names are not pricing evidence. Unknown price or terms are ineligible.

Model ID, catalog digest, runtime/host identity, qualification time, pricing/data-use evidence, and policy version are preserved. A small approved set uses deterministic ranking, without inventing quality evidence absent from its inputs. Contributor/training terms require explicit acceptance, not inferred historical consent.

New runs may choose newly qualified models. An active attempt cannot silently switch. Any permitted model change is an audited new attempt under expressly admitted policy with saved workspace preserved. Paid or different-data-use alternatives are never automatic fallbacks.

### QUALITY-008 Zero-config availability is stated honestly

Startup requires no `.env`, source PAT, or model key beyond documented infrastructure prerequisites. Useful scratch compute is available when an eligible anonymous model exists. Public runtime image acquisition does not depend on source credentials; private registry access is explicit deployment authority.

When the provider is unavailable or no model satisfies policy, the application remains usable for settings, artifacts, and diagnostics. `no_eligible_free_model` explains the actual availability/pricing/privacy problem and offers a configured-provider path without paid fallback or an irrelevant PAT requirement. Third-party free availability is not a permanent product guarantee.

## 12. Operator experience

### DOC-REQ-004 Setup exposes choices without exposing credential plumbing

The normal creation view presents workspace source, saved results, publication, and agent selection. Scratch and anonymous public reads have no mandatory GitHub connection dialog. Repository/artifact/checkpoint input and advanced local-workspace access are alternative sources, not hidden prerequisites.

The Source Control wizard accepts a name and token, verifies the actor, selects repositories, and saves/tests the connection. It creates a Managed Secret internally. Authorized reuse of an existing secret is an advanced action. Actor, resource owner, repositories, health, and expiry are prominent; revisions, materializers, detailed probes, and per-operation routing are progressively disclosed.

Repository discovery uses a connection the principal may use, deduplicates by endpoint/provider identity, and never depends on a global token. Public URL entry does not require authenticated discovery. Simple routing presents one default; separate read/publication identities remain explicit advanced choices.

Creation and Workflow Detail always show resolved identity, even when no selector is necessary. Anonymous source plus no publication is labeled as such. Connected work identifies the named connection and authenticated actor, with separate roles when they differ.

### DOC-REQ-005 Results distinguish saving from publishing

Result views expose actual reports, available downloads, completeness, retention, Continue working, and Publish Saved Work. Inapplicable formats do not appear as broken buttons. Saved-but-not-published is a successful save state, not a credential failure.

Compute, save, and publication status remain separately inspectable. Blocked publication offers publication-only recovery. Failed saving reports preserved-workspace and bounded retry state without claiming durable artifacts exist. A change from requested publication to save-only requires an explicit operator choice.

## 13. Conformance obligations

### TEST-001 Capability qualification covers real authority handoffs

Every advertised runtime × source × access mode × output/publication combination has evidence through its actual admission, workspace, delivery, result, and cleanup boundaries. Unsupported combinations fail before execution. Session reattachment and workspace restoration have separate evidence.

Required CI is hermetic and exercises production boundaries with local remotes, controlled identity/expiry endpoints, clocks, and artifact/database services as appropriate. Live provider qualification is separately labeled. A mocked catalog or profile-seeding test alone cannot prove the no-account product journey.

### TEST-002 Credential isolation survives concurrency and lifecycle changes

Conformance proves that explicit connection B wins over ambient PAT A across clone, API, CLI, publication, and recovery, or fails closed. Concurrent identities cannot share configuration or cleanup authority. Conflicting defaults fail transactionally, and reducing connection count never restores ambient fallback.

Rotation races preserve correct revision attribution; a failed replacement leaves the active credential usable. Expiring-issuance conformance demonstrates scope-preserving refresh, bounded concurrent renewal, and revocation through the same consumer boundary used for PATs. This evidence does not wait for live App enrollment support.

Unauthorized discovery/probing/secret attachment leaks neither metadata nor secrets. Public GET success cannot falsely verify write/private access. High-security policy rejects unproven confinement. Historical histories retain original meaning and digests while new writes reject superseded aliases.

### TEST-003 Saved work survives host and credential loss

Scratch/report and anonymous-repository journeys produce verified required outputs without repository credential resolution. A host can be removed and a self-contained result downloaded/restored without its source PAT. Unsafe imports, exports, redirects, and undeclared dependencies do not escape their boundaries.

Preparation crashes cannot promote partial directories. Failed/cancelled compute retains its primary outcome while capture is attempted. Save failures preserve bounded recoverable state without ordinary cleanup destroying the sole copy. Save success remains valid when publication fails.

Deferred publication covers known-baseline deltas, unrelated-repository imports, conflicts, empty-target races, and lost push/PR responses. Destination files absent from scratch input are preserved by default. Recovery neither reruns the agent nor mutates the original saved result.

### TEST-004 The ordinary default path proves the product promise

Under controlled eligible-model availability, a clean documented deployment with no GitHub/model credentials or login caches starts, acquires public images, admits the qualified free route, submits the default scratch workflow, saves a useful result, and restores it after host removal. The same admission path supports anonymous public input and later connection-backed publication without agent rerun.

With no eligible model, the app stays usable and reports the correct boundary without spending money, accepting privacy terms, or requesting a PAT. Separate live smoke evidence records actual third-party availability and exact runtime behavior. Upgrade evidence preserves operator defaults, saved-draft intent, and active-history interpretation.

## 14. Rationale and scope boundaries

### NON-GOAL-001 Decoupling is not a universal integration platform

This design does not require arbitrary host mounts, all repository providers, user-scope inheritance, a free-model marketplace, a new blob store, a second publisher, a general credential microservice, or another always-on container. GitHub Apps, OAuth, SSH, enterprise endpoints, and other hosts are explicit capabilities with their own enrollment/qualification, not unimplemented options shown as ready.

### QUALITY-009 Existing authorities keep the overhaul maintainable

| Choice | Rationale and rejected alternative |
| --- | --- |
| Extend `RepositoryConnection` | Avoid duplicate connection identity, policy lookup, and synchronization with a new Source Control domain. |
| Bind clients to admitted roles | Avoid per-call PAT plumbing, probing every token, and accidental source/destination identity changes. |
| Separate revision from issuance | Support PAT rotation and short-lived token refresh without conflating them or retaining old secrets indefinitely. |
| Artifact-first saved work | Preserve output without a GitHub dependency or unrequested recovery push. |
| Conditional local Git exports | Support reports and ordinary files without fake Git identities or unsafe whole-history export. |
| One immediate/deferred publisher | Keep destination policy, remote verification, and recovery semantics consistent. |
| Runtime-specific credential delivery | Reuse qualified brokers/materializers instead of imposing one token-file mechanism everywhere. |
| Explicit free-model policy | Avoid silent cost/privacy changes and impossible promises about external availability. |

The completion property is architectural: repository-independent work never enters repository credential resolution; authenticated operations follow one admitted authority chain; saved work outlives hosts and credentials; and new authentication adapters can satisfy the same execution/publication boundary without PAT-specific consumer changes.
