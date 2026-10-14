# Repository Access and Workspace Design

**Document Class:** Canonical declarative  
**Viewpoint:** System / Feature Design View  
**Status:** Proposed  
**Audience:** Workflow and runtime authors, integration authors, security reviewers, operators, and dashboard contributors  
**Scope:** Optional repository access, connection-bound authentication, durable workspace results, and independently authorized publication  
**Owning Surface:** Workflow admission, repository access, workspace materialization, publishing, Secrets System, and runtime integration boundaries  
**Related Docs:** [MoonMind Architecture](MoonMindArchitecture.md), [Workflow Architecture](Workflows/WorkflowArchitecture.md), [Lore VCS Integration](Workflows/LoreVcsIntegrationDesign.md), [Workflow Publishing](Workflows/WorkflowPublishing.md), [Workspace Locators](Workflows/WorkspaceLocators.md), [Secrets System](Security/SecretsSystem.md), [Provider Profiles](Security/ProviderProfiles.md), [Omnigent Harness Platform](Omnigent/OmnigentHarnessPlatformDesign.md), [OpenCode Host](Omnigent/OpenCodeHost.md), [MoonSpec Document Model](Workflows/MoonSpecDocumentModel.md), [Create Page](UI/CreatePage.md), [Input Schema Guidance](Steps/InputSchemaGuidance.md), [Single-User Application Design](SingleUserApplicationDesign.md)  
**Related Implementation:** [`repository_contract.py`](../moonmind/workflows/executions/repository_contract.py), [`workspace_intent.py`](../moonmind/omnigent/workspace_intent.py), [`repository_connections.py`](../api_service/services/repository_connections.py), [`moonmind/auth/`](../moonmind/auth/), [`moonmind/publish/`](../moonmind/publish/), [`moonmind/omnigent/host_services/`](../moonmind/omnigent/host_services/), and [`SecretsService`](../api_service/services/secrets.py)

This document describes the intended design for multi-connection repository access and workspace decoupling. Much of it is implemented; it is not by itself evidence that a particular API or deployment supports a behavior. The single-context publication contract is owned by Workflow Publishing, and the Single-User Application Design governs operator admission and instance resources. Execution, repository, and secret-use restrictions apply without a human-account or tenant model.

## Overview

Repository access is an optional execution capability, and saved work is durable independently of repository credentials and remote publication.

A repository, GitHub, a PAT, and a pull request are each one option (workspace source, host, authentication, publication result), and none is a prerequisite for agent compute. MoonMind prepares a contained workspace, runs the agent, and preserves output through artifacts and checkpoints. Named `RepositoryConnection` records select repository authority deterministically for both immediate and deferred publication, separate from model, registry, and artifact authority.

## 1. Purpose and ownership

### DOC-REQ-001 Repository-independent work is a complete product path

A supported execution can produce, download, and continue useful work without selecting a repository or configuring a GitHub PAT. Private access and remote mutation need suitable repository authority, which is not necessarily a PAT; public reads can use explicitly anonymous access; publication can be requested with execution or later from saved results.

There is one consumer boundary for repository authentication. PATs and expiring installation credentials differ behind acquisition and delivery adapters rather than throughout cloning, hosting-service tools, agent launch, publishing, and recovery, so new providers add capabilities without consumers resuming global token discovery.

### CONTRACT-001 Existing modules retain their responsibilities

| Responsibility | Owner |
| --- | --- |
| Repository targets, connections, repository capabilities | Workflow repository contracts (`provider: git \| lore` keeps its VCS meaning) |
| Source intent and workspace ownership | Workspace-intent contracts and materializers |
| Secret storage, revisions, secret-use authorization | Secrets System; connections never copy token values |
| Model account, policy, capacity | Provider Profiles |
| Execution bindings, delivery, cleanup | Managed and Omnigent execution boundaries |
| Saved content, checkpoints, retention, restore | Artifact and checkpoint systems |
| Publication intent, candidates, remote mutation, evidence | UserWorkflow compiler, publisher, and provider adapters |
| Image acquisition | Registry authentication; source credentials are never a fallback |

This view does not duplicate the Secrets System parser, Lore source-authority rules, Skill semantics, or provider-specific evidence schemas. A deployment lacking a qualified artifact-preservation path rejects an incompatible None/scratch execution before mutation rather than discarding work or granting a recovery push.

### INV-001 Authority is never inferred from an available credential

A scratch execution without repository tools or publication has no repository target, credential binding, GitHub resolver call, `gh` requirement, or GitHub readiness probe. Local Git history does not create a remote identity or push permission.

Model authentication, repository reads, local mutation, repository mutation, artifact access, and registry access are distinct, so adding a PAT cannot change model billing or data-use policy, and restoring a workspace cannot restore an external grant. Missing, ambiguous, disabled, revoked, or insufficient authority never leads to credential shopping, anonymous downgrade, source substitution, or a broader execution path, regardless of how many connections exist.

## 2. Product behavior

### DOC-REQ-002 Workspace source and publication are independent choices

| User objective | Workspace and access |
| --- | --- |
| Create from scratch | Contained scratch workspace, no repository access |
| Modify an uploaded project | Authorized immutable artifact import |
| Continue previous work | Authorized checkpoint or saved-content restore, fresh execution owner |
| Work on a public repository | Explicit anonymous read |
| Work on a private repository | Selected connection; saved work outlives the token |
| Publish now or later | Separately admitted destination and mutation operations |
| Use an existing local workspace | Explicit locator and ownership grant |

An execution requesting immediate publication is not silently converted to save-only when credentials are missing, and a save-only execution is not blocked by future publication requirements. The blank-workspace default is results saved in MoonMind with no publication.

## 3. Authoring and capability contracts

### CONTRACT-002 Exactly one workspace source is active

`workspaceSource` is a discriminated union in the execution contract:

| Kind | Semantics |
| --- | --- |
| `scratch` | Runtime-generated sandbox, no source repository; local Git optional and output-driven. |
| `repository` | One authored target with explicit access intent, prepared into a contained checkout. |
| `artifact` | Authorized immutable artifact ref and digest, imported into a new sandbox. |
| `checkpoint` | Authorized checkpoint ref and restore contract; separate from provider-session reattachment. |
| `existing_workspace` | Server-issued locator and ownership grant; advanced, never a raw server path. |

Existing-workspace materialization checks the recorded owner workflow and step before claiming it or advancing its generation. Cross-workflow reuse needs a server-signed grant even when the source kind is omitted; unsigned historical grants resume only within their owner's workflow. For `sandbox.checkout_repo`, a `file://` URL is a local source that must resolve beneath the workspace root, so URI encoding cannot reach other worker filesystems.

The source `repository` exists only for a repository source; provenance inside an imported artifact is evidence, not a live binding, and a publication destination is another role of the same target type. The authored `repository` compiles to the runtime `repositoryTarget` and worker `publishMode` is a compilation result, so conflicting aliases are rejected before external access. An omitted branch requests the remote default, and preparation records the observed branch and revision rather than assuming `main`.

Same-repository work presents one repository and one branch, and Skill/Preset arguments bind to that context. A managed PR uses the authored base and a generated head; existing-PR work derives head/base from its validated PR target. Discovery cannot replace an explicitly selected base with a repository default.

### CONTRACT-003 Authentication intent precedes acquisition

| `accessMode` | `connectionRef` | Meaning |
| --- | --- | --- |
| `anonymous` | Forbidden | Supported reads with no credential lookup or injection |
| `routed` | Absent | Deterministic selection among connections allowed for the admitted context |
| `explicit` | Required | Validate and use only the named connection |

Anonymous access is an explicit choice, not an error fallback. A connected repository picker normally authors routed access. Omitted values and documented UI defaults compile to the same intent without inspecting token presence. The resolved binding records selection origin separately, so an automatically inserted `repository-connection:git-default` never appears as an explicit user choice.

### CONTRACT-004 Capabilities derive from the requested work

The shared capability compiler derives requirements from source kind, resolved Skill and tool declarations, output format, and publication policy; launchers consume that result rather than implementing their own permission engines. Scratch reports need neither Git nor `gh`; scratch Git exports need local Git but no remote credential; anonymous input needs approved transport and repository read, not a GitHub user or PR probe; branch publication needs branch/write authority without PR or issue permission.

Explicit None forbids repository publication in the scoped tree, not all side effects: an issue-editing Skill still needs issue authority, and None with a Skill requiring push or merge fails before execution. Requirements are derived per role, so a coordinator's inherited PR policy does not give its discovery session destination-write credentials.

### INV-002 Explicit publication intent is preserved

[Workflow Publishing](Workflows/WorkflowPublishing.md) defines one authored selection per workflow or batch: `default`, `none`, `branch`, `pr`, or `pr_with_merge_automation`. Omission and `default` display as Auto. Compiled worker modes are `none`, `branch`, `pr`, and Skill-owned `auto`; PR-and-merge compiles to PR plus automation configuration.

An explicit `none` cannot silently become Auto or authorize descendant push/merge, and historical literal `auto` keeps its recorded Skill-owned meaning. A coordinator may compile its own publication to None while forwarding the frozen scope's intent to children, checked against parent authority at child admission. Retries and helper defaults cannot re-resolve admitted intent.

## 4. Repository connections and routing

### CONTRACT-005 RepositoryConnection is the single connection domain

A persistent `RepositoryConnection` owns identity, display name, VCS provider, hosting service, trusted endpoint, client policy, execution-use policy, allowed operations, typed credential configuration, and lifecycle metadata. No independently writable filesystem record or separate `SourceControlConnection` domain mirrors it; **Source Control** is the Settings page name.

Repository assignments allow many repositories per connection and several connections per repository, carrying repository identity, operation policy, revision, and routing defaults. Repository identity is namespaced by endpoint, using the provider repository ID where observed. Connections are instance resources under the single-user model, and knowing a secret reference does not authorize using it. At most one default exists per repository and route scope, enforced by database constraints and transactional writes.

### CONTRACT-006 Authentication is a typed acquisition concern

| Variant | Durable configuration |
| --- | --- |
| GitHub PAT | Typed SecretRef and fine-grained/classic subtype |
| GitHub App installation | App definition reference and installation identity |
| Lore credential | Provider-specific credential policy |

Raw values stay in the Secrets System and trusted delivery boundaries. Acquisition supports expiring issuance independently of PAT lifetime, and consumers use returned scope and expiry rather than hardcoded lifetimes. OAuth/device enrollment and SSH can be additional adapters; SSH transport does not grant issue or PR authority.

GitHub.com is the default endpoint. Enterprise API hosts are trusted only through the deployment's comma-delimited `GITHUB_TRUSTED_API_HOSTS` setting, never enrollment input. Installation links use `/apps/<slug>/installations/new` on GitHub.com and `/github-apps/<slug>/installations/new` on Enterprise Server, and `allowedOperations` are validated before setup so an invalid operation cannot fail after installation. The Omnigent path verifies the admitted endpoint and repository before acquisition and keeps that host in `gh` config, Git helpers, and probes; attachments carry only non-secret host metadata, so an execution cannot authorize another host through its repository URL.

### CONTRACT-007 Selection is deterministic for each declared role

Selection receives target identity, requested capabilities, access mode, admitted context, role, and policy version. Anonymous access admits only reads and creates no dummy connection. For connection-backed access, policy is checked before candidates are exposed; an explicit reference is validated without alternatives; routed access selects the sole eligible route or single applicable default, and missing or ambiguous authority is an actionable failure. Only the selected credential is acquired or probed.

The selected route is persisted before repository access and verified before mutation. A failing route is never removed so another PAT can be tried: authentication errors, denial, hidden-or-missing repositories, network failures, and throttling never reroute. Batch children resolve their own permitted authority or inherit a verified compatible binding, never a parent's raw PAT, and cannot choose another repository or connection to escape the frozen publication scope.

### CONTRACT-007a Isolated multi-repository fan-out

One operation may apply a bounded task to an explicit list (or immutable manifest) of repositories, each with stable host-qualified identity, revision intent, operation, and its own resolved connection; token-accessible repositories are never discovered by wildcard. The set is preflighted fail-before-dispatch unless partial-batch behavior is requested, and approval covers the exact targets, snapshot, publication modes, and maximum spend. Each target becomes a child with its own workspace, artifacts, and cleanup owner; the parent publishes nothing and forwards no credential bundle, and child admission rechecks permission. Dependent phases use explicit DAG edges with immutable upstream results. One agent editing several repositories in one workspace is out of scope.

### QUALITY-001 Validation evidence is not an authorization grant

MoonMind authorization, observed provider capability (verified, denied, unknown, stale), authentication condition, and throttling are separate dimensions, and evidence is keyed to repository, credential revision, and time, so a repository-specific failure does not invalidate unrelated bindings. Test Connection is non-mutating; public read success is not proof of private or write access; an active write test names its side effect and needs explicit approval.

## 5. Immutable admission and credential lifecycle

### CONTRACT-008 Bindings carry authority, not secret material

The versioned credential-binding envelope distinguishes a `ModelAuthorityBinding` (`provider_profile`, profile ref, materializer) from a `RepositoryAuthorityBinding` (`repository_connection`, connection ref, repository-access snapshot ref, materializer). The immutable access snapshot identifies endpoint/repository, role, admitted operations, policy revision, selection origin, and scope, linked through the existing binding-set digest.

Anonymous access has a snapshot but no credential binding; scratch has neither. A credentialless model still uses its Provider Profile and `none@1` materializer. Repository access does not inherit model-account exclusivity, capacity, or cooldown. Admission and acquisition run in Activities or trusted services; workflow code carries compact references and does not select mutable defaults or inspect credentials during replay.

### CONTRACT-009 Connection, revision, and issuance have different lifetimes

A **connection** is the stable account or installation relationship, a **credential revision** a particular PAT or configuration, and a **runtime issuance** the material supplied to one attempt. Acquisition persists owner, connection, revision, binding digest, and issuance metadata before exposing material, with compare-and-set checks so a rotation race cannot attribute a new PAT to an old revision. Disable and revocation are checked at acquisition and controlled remote-operation boundaries.

A candidate PAT is validated against expected actor/owner policy before atomic activation; a failed validation leaves the working credential usable. Successful rotation invalidates affected evidence and fences old-revision attempts, which preserve their work and resume through a new attempt. Overwritten PATs are not retained outside Managed Secrets.

### INV-003 Refresh preserves authority and revocation remains effective

An expiring issuance refreshes for the same connection, revision, installation, repositories, and operations; refresh is not rotation or scope broadening. Caches include scope and revision, and expiry margins plus single-flight refresh prevent issuance storms. Changed defaults never reroute active work, while disable or revocation denies new acquisition even for an immutable plan. A locally disabled connection cannot retract a broad PAT already read by agent code; local disable, provider revocation, and mediated confinement are distinct guarantees, and the UI does not imply otherwise.

### QUALITY-002 Throttling preserves identity

Rate limiting uses endpoint, resource bucket, and the actual actor, installation, or anonymous shared-IP identity; several PATs for one actor are not assumed to have independent budgets. Reset evidence drives bounded waits, never credential invalidation or substitution.

## 6. Runtime delivery and security

### CONTRACT-010 Repository consumers use bound access clients

Transport and hosting-service adapters consume admitted access plus authenticated execution ownership; they do not accept an optional PAT and discover credentials when it is absent. An unavailable hosting capability yields an explicit capability error, not an irrelevant PAT prompt. The local managed broker and Omnigent lease-owned materializer share admission semantics with runtime-appropriate delivery.

The Omnigent path verifies the stored execution plan before linking inputs to a Step Execution, and snapshot reads carry that execution's admitted scope, so a known artifact ref does not grant unrelated reads. Branch and PR publication acquire the selected `destination_write` binding before remote access and validate the workspace origin; missing or revoked authority fails before publication without falling back to ambient credentials. Plans without repository snapshots keep their existing credential path.

Server-side readers and PR operations in `GitHubService` (pull-request and target reads, ancestry checks, merges and retargets, pr-resolver and merge-automation readiness, review requests) use the connection admitted for the owning run: its recorded `connectionRef`, or for routed runs the collaboration (else source) binding of its frozen plan. Child workflows act for the nearest recorded run on their Temporal parent chain. When that root is a Temporal-started scheduled workflow with no API source row, its authority is recovered from that exact run's immutable start input after validating the workflow type and user/system ownership; Visibility parameters and the schedule's current target never authorize repository access, and no canonical row is backfilled. A chain with no verifiable admitted root fails, and an omitted reference means `repository-connection:git-default`. The endpoint is trusted before any credential is read, a PR URL is read only through the connection serving its host, and App connections acquire exactly the operation needed (`merge_request`, `review_request`, or `read`). Runs with frozen access verify the snapshot and current assignment before resolving a credential. A failed selection reports the operation unavailable and sends no request.

Issue collaboration (claims, labels, comments, search) uses the default connection's `issues` operation through `GitHubService.resolve_github_token`; Source Control grants `issues` with the other publishing operations, and a denied operation reports unavailable without substituting another account's token.

### INV-004 Ambient identity cannot override admitted identity

Git and hosting CLIs run with isolated home/configuration and scrubbed token variables, authorization headers, credential helpers, `.netrc`, and login caches unless the admitted adapter supplies them. Workspaces persist only credential-free remotes, and raw material never enters arguments, Docker metadata, durable environments, Temporal payloads, errors, digests, logs, checkpoints, or exports. Delivery objects belong to an attempt with generation-fenced cleanup. Anonymous access creates no token file or materializer. Where a CLI needs authentication for a declared operation, the runtime provides a supported unauthenticated interface or rejects the combination.

In Omnigent delivery, realizers clone with a Git environment that keeps only proxy and CA settings, and agent-side `gh` reads an attempt-owned `hosts.yml` written atomically by the shared projection writer rather than a container variable (on the profile-bound host, only when the step declares `gh`). Expiring App tokens are re-issued ten minutes before expiry with a reserve/acquire/publish order so an older writer cannot replace a newer projection; re-issue keeps the same binding and fails if the connection was disabled or its revision changed. PATs are not re-issued.

### QUALITY-003 Routing and confinement are separate guarantees

Routing guarantees that MoonMind-controlled operations use the admitted repository, operation, and identity. A raw PAT exposed to agent code keeps its provider-granted scope, so confinement against arbitrary code needs scope-limited credentials or mediation without bypass; a None selection is not a sandbox when the agent can read a broad write token. Managed publication keeps destination credentials outside the agent; Skill-owned `auto` needs a mediated path or an explicitly accepted exposing policy, and high-security mode rejects unsupported confinement. `githubCredentialsIsolated` is a routing guarantee; the profile-bound host reports a projected `gh` credential as `githubCredentialExposure: agent_readable_unconfined` (otherwise `not_projected`) in preflight and `credential_preflight` metadata.

### INV-005 Anonymous access does not remove source-safety controls

Endpoint and egress policy apply to anonymous access too: embedded credentials, lookalike hosts, unsafe protocols, private-network destinations, and unvalidated redirects are rejected, and credentials are not forwarded across hosts. Repository hooks, filters, submodules, LFS endpoints, and extra fetches create no implicit authority, and required content is not silently skipped. Diagnostics distinguish an auth challenge, inaccessible-or-missing repository, missing branch, empty remote, transport failure, and unsafe source.

## 7. Workspace preparation and restoration

### CONTRACT-011 Every source uses the same contained workspace lifecycle

All sources use server-generated locators, authoritative storage, ownership handoff, quotas, cleanup authority, and remote-Docker path translation. Preparation is idempotent and verifies content before recording completion; an existing directory is not evidence of a finished clone, and partial content is not launched. Scratch initializes local Git only when its output profile needs it, with a neutral identity and no remote; non-Git checkpoints do not fabricate commits.

A materialized repository carries the deployment's commit identity in repository-local config, because Skills that publish commit inside a host whose image supplies no identity; reconciliation reapplies it after checkpoint projection, and an undeclared identity resolves to the documented default rather than blocking.

Imports verify authorization and digest, bound size, and reject traversal, escaping links, devices, and privileged metadata; credential paths, hooks, helpers, and session authority are not restored.

### INV-006 Content restoration does not restore external authority

A self-contained saved result restores under artifact authorization without its original PAT; thin bundles, missing LFS objects, and unmaterialized dependencies are labeled incomplete. Once a source snapshot is prepared, save-only finalization does not reacquire source credentials, so token expiry alone does not discard local work. Continuation creates a fresh execution owner and re-admits external operations; restored content never revives leases, approvals, or permission to repeat side effects. A dependency's completion does not move its candidate into another workspace; a dependent batch names a verified merge, checkpoint transfer, or shared-branch handoff.

## 8. Saved work and finalization

### CONTRACT-012 Saved work is an immutable artifact-backed result

The saved-work manifest is a compact index of verified artifact and checkpoint evidence, reusing an existing checkpoint archive where suitable. It records identity and provenance (schema, workflow/run/step/attempt, source identity), content (snapshot ref, file manifest, digest, report), optional Git exports (baseline/head, binary-safe delta, history/bundle refs), completeness and scan evidence, and ownership and retention. It holds no token, connection handle, or approval; publication records reference its digest rather than editing it.

After capture commits, each saved-work object is linked as `output.checkpoint` to the owning run in that execution's Temporal namespace, the manifest last. Uploads from a failed capture stay retained and unlinked for recovery, so `COMPLETE` upload status alone does not expose a result. The manifest's listing metadata carries a path-free `saved_work_summary` (formats with part IDs, exclusion counts, limitations, scan disposition, retention handle).

### QUALITY-004 Portable outputs are useful, bounded, and honest

An output policy selects a supported format profile; a required-format failure blocks save finalization, while an inapplicable optional format is simply absent. Patches are binary-safe against an exact baseline, bundles state whether they are self-contained, and capture records exclusions without rewriting agent history.

MoonMind-issued credentials never appear in exports. Checkpoint exports follow `MOONMIND_HIGH_SECURITY_MODE`: omitted or `false` records `not_scanned` with no clean-scan claim; `true` scans export bytes and metadata and rejects detected content before upload, with no repository-file exceptions. Credential-path exclusions and containment apply in both modes. Reused captures keep their recorded scan evidence, and later publication applies current destination policy.

### INV-007 Verified saving precedes destructive cleanup

```text
quiesced workspace
  -> approved captured snapshot
  -> verified required artifact objects
  -> committed immutable manifest/checkpoint references
  -> recorded save outcome
  -> optional admitted publication
  -> remaining workspace/cleanup release
```

This is a durability dependency, not a second publisher; Skill-owned `auto` mutations stay with the Skill. Compute, save, and publication outcomes are separate: failed compute can still save useful work, and publication failure never overwrites save evidence. Save failure retries the same capture idempotently and retains the workspace, so cleanup cannot destroy the only copy. A None or credentialless execution completes its handoff through artifact storage; a push cannot be relabeled a checkpoint to bypass None, and a retained local path is not a saved result. Download, restore, and publication authorization derive from artifact ownership, not an old PAT, and expired results are reported honestly.

## 9. Independently authorized publication

### CONTRACT-013 Publish Saved Work uses the existing publisher

Publication-only execution takes an immutable `savedWorkRef`, an authorized destination, a managed objective, and application policy, without rerunning the agent or adding a separate token setting. The saved result's authorization, digest, and completeness are verified; destination, connection, operations, and remote expectation are newly admitted; content enters a clean workspace without old credentials; and the candidate is scanned under current policy.

Branch publication needs read, write, and branch-write authority; a PR also needs review-request. The persisted authority records the connection's policy and credential revisions (including a managed `db://` SecretRef's own), so retries, push, and PR creation reject a rotation with `PUBLICATION_AUTHORITY_CHANGED` while preserving the saved result and confirmed effects. Tokens and hashes never enter history. Mutation uses existing protected-branch and compare-and-set controls with exact remote verification.

### INV-008 Destination relationships determine safe application

| Relationship | Application rule |
| --- | --- |
| Same repository, known baseline | Apply the delta to the admitted base; expose conflicts and moved tips. |
| Unrelated existing repository | Previewed additive/path-mapped import, no inferred deletions or tree replacement. |
| Selected empty repository | Push compatible history only after verifying emptiness and branch policy. |
| Lore-authoritative destination | Lore publishes; GitHub is a review projection. |

A file absent from scratch output is not an instruction to delete it; deletions need baseline evidence or an approved manifest. Conflicts, case collisions, ambiguous renames, mode changes, and binary conflicts are surfaced, and unrelated histories are not merged automatically. Repository creation is a separate capability.

### QUALITY-006 Publication recovery reconciles exact remote evidence

Idempotency covers saved-work digest, destination, strategy, and branch; candidate identity and remote expectation are persisted before mutation, so a lost push or PR response leads to reconciliation, not repetition. An explicit admission generation separates re-admission from retry: Saved Results authoring persists it before submission, reuses it across ambiguous acknowledgments and reloads, and rotates it only on a new submission after the prior operation is terminal. A stale baseline blocks publication while preserving saved work; replanning yields a new candidate and approval, and changing connection or destination requires re-admission.

## 10. Compatibility and observability

### CONTRACT-014 Historical interpretation is explicit and bounded

Changed contracts are versioned at their owning boundary; historical loaders verify original parsing and digests, new writes use one canonical contract, and historical authority never permits reacquiring a revoked credential. Legacy credential mapping runs through the versioned migrations and [deployment controller](Steps/DockerComposeUpdateSystem.md).

`repository-connection:git-default` identifies a proven effective legacy binding, not a fallback chain. Because that credential predates assignments, a migrated default without assignments serves the named GitHub repository with its own operations when explicitly selected, but is never routed authority; an operator-recorded or assignment-scoped default follows its assignments. An empty or unknown legacy allowlist never becomes wildcard access.

### QUALITY-007 Failures identify the responsible boundary

Diagnostics name the failing boundary: model eligibility, workspace preparation, routing, credential acquisition, capability evidence, saving, or publication. Expiry, organization approval, unknown write evidence, ambiguous routing, throttling, unsafe content, and incomplete restore are not collapsed into one Disconnected state. Secret-free audit evidence correlates attempt, access context, role, connection and revision, binding digest, issuance, candidate, and outcome, and UI projections cannot replace primary success with projection lag.

## 11. Credential-independent model compute

### DOC-REQ-003 The model default remains independent of repository setup

The credentialless OpenCode route keeps the `opencode-zen-free` identity and `none@1` materializer, separate from keyed profiles, and explicit operator choices are preserved. `OPENCODE_API_KEY` does not turn an anonymous execution into a paid one. Readiness proves the selected profile and runtime's capabilities; a shared image or seeded row alone is not evidence the path works.

### INV-009 Free-model selection cannot change cost or privacy implicitly

Automatic free selection uses the runtime's observed, qualified catalog; candidates need known zero cost, required capabilities, availability, and acceptable data-use terms, and unknown price or terms are ineligible. Model, catalog digest, runtime identity, and pricing evidence are recorded. An active attempt never silently switches models, and paid or different-data-use alternatives are never automatic fallbacks.

### QUALITY-008 Zero-config availability is stated honestly

Startup needs no `.env`, source PAT, or model key beyond documented infrastructure prerequisites, and public image acquisition does not depend on source credentials. When no model satisfies policy, the app stays usable for settings, artifacts, and diagnostics, and `no_eligible_free_model` explains the actual problem and offers a configured-provider path without paid fallback or a PAT requirement.

## 12. Operator experience

### DOC-REQ-004 Setup exposes choices without exposing credential plumbing

The creation view presents workspace source, saved results, one publication selection, Runtime, and one Profile; scratch and anonymous reads have no mandatory GitHub dialog. For repository work, Skill/Preset settings do not repeat repository, branch, or publish-mode controls. Creation and Workflow Detail always show resolved identity, label anonymous source and no publication as such, and ignore stale lookup responses.

Settings → Providers & Secrets → Source Control supports a named PAT connection or verified App installation through one lifecycle ([Settings Configuration Pages](UI/SettingsPage.md) §7.1.0). PAT setup creates a Managed Secret in the connection's own transaction, and App signing material also stays in the Secrets System. Actor, owner, repositories, health, and expiry are prominent; revisions and per-operation routing are progressively disclosed.

App enrollment ([`repository_connections.py`](../api_service/api/routers/repository_connections.py)) binds App, signing key, endpoint, account, repositories, and operations at `POST /api/v1/repository-connections/github-app/begin`, which requires a nonempty `permittedRepositories` list so enrollment never grants installation-wide authority. The `.../callback` accepts only `state`, `installationId`, and `connectionId`; the server validates state, expiry, and caller before touching secrets or GitHub, then verifies each repository through the JWT-authenticated installation endpoint and persists only confirmed names. Pending state is process-local, so a restart needs a new begin.

### DOC-REQ-005 Results distinguish saving from publishing

One result section in Workflow Detail exposes reports, downloads, completeness, retention, Continue working, and Publish Saved Work. Saved-but-not-published is a successful save state, not a credential failure. Compute, save, publication, and cleanup status are separately inspectable, and blocked publication offers publication-only recovery without another model run. The displayed authored selection is not replaced by a coordinator's derived None or a resolver's internal Auto.

## 13. Scope boundaries and rationale

### NON-GOAL-001 Decoupling is not a universal integration platform

This design does not add arbitrary host mounts, all repository providers, user-scope inheritance, a free-model marketplace, a new blob store, a second publisher, a credential microservice, or another always-on container. Apps, OAuth, SSH, and enterprise endpoints are explicit capabilities with their own enrollment, not options shown as ready before they work.

### QUALITY-009 Existing authorities keep the design maintainable

Extending `RepositoryConnection` avoids a duplicate Source Control domain; binding clients to admitted roles avoids per-call PAT plumbing and token probing; separating revision from issuance supports both rotation and short-lived refresh; artifact-first saved work preserves output without GitHub; one publisher for immediate and deferred publication keeps verification and recovery consistent; and runtime-specific delivery reuses qualified brokers instead of one token-file mechanism.

Repository-independent work never enters credential resolution, authenticated operations follow one admitted authority chain, saved work outlives hosts and credentials, and new authentication adapters satisfy the same boundary without PAT-specific consumer changes.
