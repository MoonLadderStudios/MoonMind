# Repository Access and Workspace Decoupling Plan

**Status:** Proposed consolidated implementation plan. Not implementation or deployment evidence.  
**Updated:** 2026-09-04  
**Reviewed baseline:** [`63bce9852ffa33e33cb0b416bc24a654b1b6f92b`](https://github.com/MoonLadderStudios/MoonMind/commit/63bce9852ffa33e33cb0b416bc24a654b1b6f92b)  
**Document class:** Temporary implementation and migration plan  
**Scope:** Repository connections, GitHub PATs and future authentication adapters, workspace inputs, saved-work outputs, optional publication, and credential-independent agent execution.

This plan consolidates the two proposals supplied on September 4, 2026: the multiple-GitHub-PAT architecture and the repository-independent, zero-config execution proposal. It incorporates the architecture review and explicitly revises claims that do not match the reviewed repository. Relative implementation links identify the components inspected at the baseline above. Neither the proposals nor this review constitute a successful full-stack test.

Implementation checklists remain here under `docs/tmp/`. Accepted desired-state rules must be incorporated into their existing canonical documents as the corresponding contracts are implemented. Do not create a second permanent specification that competes with repository, publishing, workspace, Secrets System, or Provider Profile authority.

## 1. Recommendation

**One sentence:** Make repository access an optional, explicitly admitted capability of an execution, backed by the existing `RepositoryConnection` domain, while making saved work independent of repository authentication and remote publication.

**One paragraph:** A repository is one possible workspace source. GitHub is one hosting service for Git repositories. A PAT is one authentication mechanism. A pull request is one publication result. None is a prerequisite for agent compute. MoonMind should run against a durable scratch, imported, restored, or repository-backed workspace, preserve its useful results as authorized artifacts, and publish those results only when a separately declared destination has suitable authority. Named repository connections provide deterministic multi-PAT routing now and an unchanged consumer boundary for GitHub Apps later. Model Provider Profiles, repository access, registry authentication, and artifact access remain independent.

The overhaul should establish the final consumer boundary once. Later authentication adapters should implement acquisition behind that boundary, not force another rewrite of cloning, GitHub tools, Omnigent, publication, or recovery. This is a bounded extensibility goal, not a promise that future providers will never require additional capabilities.

### Decisions that replace the earlier proposals

| Earlier proposal | Consolidated decision |
| --- | --- |
| Introduce `SourceControlConnection` alongside repository contracts | Persist and extend `RepositoryConnection`. **Source Control** is a Settings label, not a competing domain. |
| Every run selects one GitHub connection before launch | Admit repository access only for declared repository roles. Scratch needs none. Anonymous reads have explicit non-credentialed access. |
| Missing token means try anonymous clone | Authentication mode is chosen before execution. Neither token discovery nor a failed request may silently change it. |
| Retry forever on the same credential generation | Pin a credential revision per attempt. Rotation fences it. Authorized recovery can adopt a replacement revision on the same connection. Token refresh is a different operation. |
| A binding confines any raw PAT to the bound repository | Bindings enforce MoonMind-controlled operations. Raw credential exposure has the provider credential's scope unless a stronger mediated execution boundary is proven. |
| Rename the free profile and newly add `none@1` | Preserve the existing `opencode-zen-free` identity and documented `none@1` path. Qualify and repair actual gaps rather than duplicate already-delivered work. |
| Always run `git add -A`, export `--all`, and push later | Capture a policy-approved snapshot. Git exports are conditional and bounded. Deferred publication applies an explicit delta to an admitted destination. |
| Strict routing depends on having multiple connections | Strict routing is part of the new execution contract regardless of connection count. |
| Every no-publish run must still save a remote Git checkpoint | Artifact-backed saved work is sufficient for credentialless execution. Remote recovery publication needs separately admitted authority. |

## 2. Current foundation and verified gaps

The following observations are source inspection, not proof that every deployed runtime path is working.

| Area | Reviewed state | Consequence for this plan |
| --- | --- | --- |
| Repository authority | [`repository_contract.py`](../../moonmind/workflows/executions/repository_contract.py) already defines authored and resolved Git/Lore targets, `RepositoryConnection`, endpoint and operation policy, credential selection, and readiness. | Extend these contracts and their persistence. Do not introduce a parallel repository-connection owner. |
| Connection selection | The same compiler currently fills an omitted Git connection with `repository-connection:git-default`. [`workspace_intent.py`](../../moonmind/omnigent/workspace_intent.py) reads `repositoryTarget`, nested repository, and historical top-level connection forms. | Preserve authored versus automatic selection intent until routing. Normalize new requests once. Retain old decoding only for persisted histories. |
| Global credentials | [`github_credentials.py`](../../moonmind/auth/github_credentials.py) accepts `repo`, but selects credentials through a global precedence chain. [`GitHubService`](../../moonmind/workflows/adapters/github_service.py) calls that resolver. | Replace new-execution global discovery with bound access clients. A repository argument alone is not routing. |
| Generic workspace preparation | [`host_services/workspace.py`](../../moonmind/omnigent/host_services/workspace.py) requires a GitHub source, branch, and token when preparing a fresh sandbox. | Add scratch/import/restore and anonymous read behavior at this shared boundary. |
| Clone transport | The current generic clone path already sends the token over stdin to a temporary helper and uses a clean URL. | The secondary proposal's claim that this particular path still embeds the token in the URL is stale. Preserve this improvement and audit remaining compatibility paths. |
| Omnigent CLI credentials | [`host_services/github_credentials.py`](../../moonmind/omnigent/host_services/github_credentials.py) resolves a global credential and materializes a lease-owned `gh` configuration volume. | Keep ownership and cleanup, but consume an admitted binding instead of global discovery. |
| Secret lifecycle | [`SecretsService`](../../api_service/services/secrets.py) overwrites the secret value on update/rotation. Rotation sets `ROTATED`, while the normal getter selects `ACTIVE`. | A connection generation counter alone cannot retrieve an overwritten old secret. Define and implement atomic revision semantics. |
| Plan binding | [`credential_bindings.py`](../../moonmind/omnigent/harness_platform/credential_bindings.py) and [`provider_leases.py`](../../moonmind/omnigent/provider_leases.py) currently assume Provider Profiles. | Generalize the envelope through a typed union and dispatch by authority kind. Do not apply model capacity rules to every credential. |
| Free OpenCode route | [`OpenCodeHost.md`](../Omnigent/OpenCodeHost.md) documents credentialless `opencode-zen-free`, `none@1`, and separation from keyed Go. [`main.py`](../../api_service/main.py) preserves that separation. [`test_default_omnigent_launch_authority.py`](../../tests/unit/services/test_default_omnigent_launch_authority.py) tests seeded default admission. | Do not recreate or rename the profile merely to match the older proposal. Full default journey qualification and safe model-selection policy are still required. |
| Saved-work substrate | [`managed_checkpoint_models.py`](../../moonmind/schemas/managed_checkpoint_models.py), [`checkpoint_policy.py`](../../moonmind/workflows/temporal/checkpoint_policy.py), and [`authority_chain.py`](../../moonmind/omnigent/authority_chain.py) already carry checkpoint and saved-work concepts. | Extend artifact/checkpoint authority rather than add a new storage service. Qualify capture and restore independently for each supported runtime/workspace combination. |
| Publication semantics | [`WorkflowPublishing.md`](../Workflows/WorkflowPublishing.md) defines `none`, `branch`, `pr`, and agent-owned `auto`, including non-repository side effects. [`LoreVcsIntegrationDesign.md`](../Workflows/LoreVcsIntegrationDesign.md) defines provider-authoritative publication. | Do not replace these modes with a second enum or interpret `none` as proof that all task behavior is read-only. |
| Operator instructions | [`README.md`](../../README.md) still asks for a PAT and model authentication in Quick Start. | Update onboarding only when the supported no-key journey has executable evidence. |

The existing repository contract uses `provider: git | lore`. A GitHub/GitLab hosting-service discriminator must not silently replace that meaning. Existing documents also contain transitional publication and authoring forms. Slice 0 below records the actual new-write contract and its legacy readers before changing either.

## 3. Separation of responsibilities

```text
Model Provider Profile ------------------------------+
                                                    |
Workspace source -> durable workspace -> agent compute -> saved-work artifacts
       |                                            |             |
       +-- optional repository source access -------+             |
                                                                  v
                                                    optional publication request
                                                                  |
                                                    destination repository access
                                                                  |
                                                    verified publication evidence

Deployment registry access -> image acquisition only
Artifact authorization     -> import, save, download, restore only
```

Reuse existing API services, Activities, brokers, runtime adapters, and artifact storage. This feature does not justify another always-on container or a general-purpose credential microservice.

### Invariants

1. A scratch execution with no repository tools and `publish.mode=none` does not call a GitHub credential resolver, create a repository connection, require `gh`, or probe GitHub readiness.
2. Local Git history does not imply a remote repository, remote identity, or permission to push.
3. Repository read authority, local workspace mutation, repository mutation, and publication are distinct capabilities.
4. Model selection cannot select a repository identity. A repository identity cannot select a model account. Adding a PAT cannot change a free model's billing or data-use policy.
5. Repository source, collaboration tools, and publication destination are declared roles. The simple same-repository workflow uses one admitted connection unless the operator explicitly configures separate roles.
6. Missing, disabled, revoked, ambiguous, or insufficient authority never triggers credential shopping, anonymous downgrade, source substitution, or a broader fallback.
7. Results are saved before destructive cleanup. Remote publication failure does not erase successful compute or successfully saved artifacts.
8. Restoring saved work does not restore live credentials, old leases, old approvals, or permission to replay external side effects.
9. New executions use one canonical contract. Frozen historical readers preserve original digests and meaning without reopening legacy authoring paths.
10. Every advertised runtime/source/output combination has qualification evidence or is rejected before execution with an actionable capability error.

## 4. One authoring and admission contract

Extend the existing execution and workspace-intent contracts. Do not introduce `RepositoryTargetV2` as a parallel domain beside the existing authored/resolved target hierarchy. Version the actual contracts that change, and replace their new-write producers together.

### 4.1 Workspace input, preservation, and publication

The conceptual authoring shape is:

```yaml
workspaceSource:
  kind: scratch
outputPolicy:
  savedWork: required
  formatProfile: portable
publish:
  mode: none
```

For repository-backed input, retain the existing top-level `repository` authority and compile it to the existing runtime `repositoryTarget` projection:

```yaml
workspaceSource:
  kind: repository
repository:
  provider: git
  repository:
    name: MoonLadderStudios/MoonMind
  accessMode: anonymous
  # Omitted branch requests the remote default branch.
outputPolicy:
  savedWork: required
  formatProfile: portable
publish:
  mode: none
```

These are proposed additions to the existing authoring schema, not claims that today's API accepts these examples. `publish.mode` keeps `none | branch | pr | auto`. Worker `publishMode` remains a compilation result, not another independently authored authority.

Define `workspaceSource` as a discriminated union:

| Kind | Required authority | Rules |
| --- | --- | --- |
| `scratch` | Runtime-generated sandbox ownership | No repository target. Code-oriented work can initialize local Git. Report-only work need not do so. |
| `repository` | One authored repository target and admitted access | Resolve branch/revision and prepare a contained checkout. |
| `artifact` | Authorized immutable artifact reference and digest | Safe extraction/import into a new sandbox. Never accept an arbitrary server path. |
| `checkpoint` | Authorized checkpoint reference and supported restore contract | Restore workspace content under a new execution owner. Session reattachment is separate. |
| `existing_workspace` | Server-issued workspace locator and ownership grant | Explicit advanced/local-development capability, not a raw host-path escape hatch. |

Exactly one source kind is active. The source repository target is present only for `repository`. Source provenance retained in an imported artifact is evidence, not a live source binding. A later publication destination is a different role and reuses the same typed repository-target contract.

`outputPolicy` should select a small number of supported format profiles rather than four independent booleans that allow unusable combinations. For example, `portable` requires a verified content snapshot and manifest, with a report and applicable Git exports. An unavailable optional format is represented explicitly. A required format failure blocks successful finalization without discarding recoverable data.

### 4.2 Explicit authentication intent

Add an access discriminator to the versioned repository draft:

| `accessMode` | Authored `connectionRef` | Behavior |
| --- | --- | --- |
| `anonymous` | Forbidden | Read-only network access with no credential lookup or credential injection. |
| `routed` | Absent | Deterministic selection among connections the principal may use. |
| `explicit` | Required | Validate and use only that connection. |

After routing, the resolved binding records the selected connection and selection origin. It is not fed back through the authored-input validator. Existing `connectionRef` remains the connection identity rather than becoming a PAT reference.

A blank-workspace UI chooses `scratch`. A public-URL import can explicitly author `anonymous`. Selecting a repository from a connected repository picker normally authors `routed`. There is no token-exists heuristic. Omission and the documented UI default must compile to the same intent.

Do not insert `repository-connection:git-default` before selection and then mistake it for an explicit choice. For migrated saved drafts, preserve effective legacy intent through an explicit migration. For recorded Temporal histories, keep the frozen legacy decoder. New submissions with conflicting repository or connection aliases are rejected before any external access.

An omitted source branch resolves the remote default branch at the trusted preparation boundary and records its observed name and revision. Do not assume `main`. Empty repositories, missing branches, inaccessible remotes, and authentication failures are different outcomes. Provider-specific immutable revision selection remains supported.

### 4.3 Capabilities are compiled, not inferred from token presence

Extend the existing capability compiler and readiness registry. Derive requirements from workspace source, resolved Skill/tool declarations, requested output formats, and publication policy. Do not build a second permission engine inside each host launcher.

Examples:

- Scratch report: sandbox and artifact-output capabilities, no `git` or `gh` requirement.
- Scratch code with Git exports: local Git plus artifact-output capabilities, no repository credential.
- Anonymous Git input: approved Git transport plus repository-read capability, no GitHub API unless a declared operation actually needs it.
- Branch publishing: repository write/branch authority, not automatically PR or issue-write authority.
- Issue-editing Skill with `publish.mode=none`: issue-mutation authority from the Skill, despite no final repository publication.
- `auto`: the resolved Skill's declared external operations and evidence contract. Preserve agent-owned semantics rather than substituting a native implementation.

Friendly profiles such as indexing, readiness, publish, and full PR automation remain UI presets for versioned capability bundles. They are not an ordered privilege ladder or a complete permission model. `Workflows` writes, issue writes, checks, reviews, and merges must be derived only when needed.

New explicit `none` must not silently become `auto`. An incompatible Skill selection should produce a visible correction before submission. Historical normalization can remain only in the relevant frozen decoder.

## 5. Extend the existing RepositoryConnection domain

### 5.1 Persistence and ownership

Persist the existing `RepositoryConnection` contract and normalize its repository assignments. Keep one authoritative endpoint, client policy, allowed-operation policy, scope, and credential configuration. Do not retain a filesystem connection record and a new database connection row as independent writable authorities.

The persistent model needs:

- Stable connection ID using the existing `repository-connection:` identity family, display name, VCS provider, hosting service where applicable, and trusted endpoint identity.
- System/workspace ownership and principal-use authorization. Start with the existing local workspace model. User-owned connections and complex inheritance can follow without weakening initial isolation.
- A typed authentication variant referencing the Secrets System or an App installation.
- Enabled state, authentication state, credential revision, actor identity, and separately identified resource owner/installation.
- Versioned policy and metadata-only validation evidence.

Keep GitHub.com as the initial GitHub endpoint. Adding enterprise or other Git endpoints is an administrator-controlled adapter capability with endpoint validation, TLS policy, and qualification. Do not expose a free-form credential destination URL to ordinary connection creators.

Repository assignments use hosting endpoint plus provider repository ID when one can be observed, retaining names for display and lookup. Generic Git sources can use a normalized endpoint/remote identity without inventing a GitHub ID. Reconcile aliases after verified renames. A resource-owner transfer triggers policy revalidation instead of silently inheriting old authorization.

Use database constraints and transactional writes for routing uniqueness. There is at most one default for a given scope, repository, and route/capability bundle. Avoid unconstrained `default_for_permission_profiles` arrays that allow concurrent administrators to create conflicting defaults.

### 5.2 Authentication variants

Implement fine-grained and classic PATs in the initial authenticated release. Recommend fine-grained PATs where they support the task, but do not remove classic PAT compatibility needed for some collaborator scenarios. GitHub documents those limitations in its [PAT guidance](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens).

Use discriminated configuration rather than nullable PAT/App/OAuth/SSH fields on every row:

```text
RepositoryConnection.credential
  GitHub PAT             -> typed Managed Secret reference + token subtype
  GitHub App installation -> App definition reference + installation identity
  existing Lore variant  -> existing provider-specific credential policy
```

Use the existing typed `SecretRef` and its resolver. Do not invent another secret-reference parser. Attaching an existing secret requires authorization to use it, not merely knowledge of its identifier. Do not revive an old user-profile PAT column or duplicate the token into connection metadata.

The acquisition interface must support expiring issuance now. GitHub Apps can be delivered in a later slice without changing repository consumers. [Installation tokens](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/authenticating-as-a-github-app-installation) expire after one hour and can be requested for selected repositories and permissions. Use the returned expiry and scope rather than hardcoding assumptions in consumers.

OAuth/device enrollment and SSH deploy keys are future authentication adapters, not prerequisites for this project. Device flow is an enrollment mechanism, not a guarantee of short-lived credentials. SSH transport authority does not grant GitHub issue/PR API authority. Either declare an additional collaboration role or reject an unsupported combination.

### 5.3 Separate policy, capability evidence, and health

| Dimension | Example | Effect |
| --- | --- | --- |
| MoonMind authorization | May publish to repository A, not B | Mandatory local policy check. |
| Observed provider capability | Reads verified, writes unknown | Evidence for readiness, not a grant of additional authority. |
| Authentication condition | Valid, expired, revoked, approval required, unknown | Determines whether acquisition or revalidation can proceed. |
| Operational condition | Rate-limited or temporarily unreachable | Bounded retry/wait without identity replacement. |

Track per-repository and per-capability evidence as `verified`, `denied`, `unknown`, or `stale`, keyed by credential revision, binding/policy revision, capability-definition version, endpoint, and time. Failed evidence for one repository must not disable all other bindings unless the credential itself is invalid.

Read-only probes must not report write permission as verified. Public reads also do not prove that a token has private-resource or mutation authority. GitHub permits pending fine-grained tokens to read public resources, as described in its [organization approval policy](https://docs.github.com/en/organizations/managing-programmatic-access-to-your-organization/setting-a-personal-access-token-policy-for-your-organization).

Normal Test Connection is non-mutating. An optional active write test names its exact side effect and requires explicit approval. Unknown capability evidence follows an explicit deployment policy, such as administrator acknowledgment or a bounded active test. It does not silently become denied or granted.

Keep endpoint requirements distinct from granted token permissions. Bind provider API-version policy to the adapter and test the actual endpoints consumed by the declared capabilities.

## 6. Deterministic selection and immutable bindings

### 6.1 Select once per declared role

Selection receives the canonical target, requested capabilities, access mode, principal/workspace, role, and policy version.

For anonymous access, admit only supported read operations and record anonymous access evidence. Do not query secret storage or construct a dummy connection.

For connection-backed access:

1. Authorize the principal before exposing candidate identities or repository discovery results.
2. Validate an explicit reference or enumerate metadata-eligible routes within the authorized scope.
3. Select the sole eligible route, or the single applicable default. Otherwise return missing/ambiguous authority.
4. Validate the selected route's repository and operation policy, then acquire or probe only its credential at a trusted boundary as required.
5. Persist the selection and policy snapshot before repository access. Freeze exact repository observations before mutation.

Do not probe every PAT until one succeeds. A selected connection that fails validation remains a failure for that choice, not permission to try the next candidate. `401`, `403`, `404`, timeout, or throttling never reroute the execution.

One same-repository workflow normally admits one connection for its declared operations. Separate read and publish identities require explicit role policy. Source, destination, and collaboration bindings can differ for cross-repository workflows, but those differences are visible and immutable. Batch children resolve authority for their own repository or inherit a verified compatible binding, never the parent's raw PAT.

A source read may occur before an agent host is launched. Future destination authority is admitted only when publication was requested. A save-only workflow is not blocked merely because publication might be useful later. Conversely, an explicitly requested immediate publication with missing authority is rejected before compute unless the user explicitly chooses save-only instead.

### 6.2 Version the existing plan envelope

Generalize `CredentialBinding` using an actual typed union:

```text
ModelAuthorityBinding
  authorityKind = provider_profile
  providerProfileRef
  materializerRef

RepositoryAuthorityBinding
  authorityKind = repository_connection
  connectionRef
  repositoryAccessSnapshotRef
  materializerRef
```

The referenced immutable access snapshot contains endpoint/repository identity, declared role, admitted operations, policy/binding revision, selection origin, and principal/workspace scope. Reuse the current versioned binding-set/digest infrastructure. Do not copy mutable connection fields into several independently editable plan records.

Anonymous access is represented in repository-access snapshots, not in the credential-binding union. A scratch run has no repository-access entry. A credentialless model still has its normal Provider Profile binding and `none@1` materializer.

Audit all consumers that assume every binding has `providerProfileRef`, including lease acquisition, continuation, child planning, runtime-binding serialization, and cleanup. Dispatch by authority kind. Repository access does not inherit Provider Profile concurrency, OAuth exclusivity, or model cooldown semantics.

Registry credentials remain in the existing deployment-owned image acquisition boundary. They do not need to become a general third authority framework to complete this feature. No source PAT fallback to GHCR is allowed in the new path.

### 6.3 Temporal compatibility and revocation

Admission and acquisition run in Activities/services. Workflow code carries compact references and orchestrates deterministic waits, retries, and child operations. It does not resolve secrets, inspect the environment, fetch catalogs, or select a mutable default during replay.

Version-one binding loaders retain their original parsing and digest calculation. Never transform a historical plan to version two before verifying its original digest. Use the established Temporal versioning/cutover mechanism for changed workflow decisions and pin in-flight work to compatible workers where necessary.

Changed defaults do not reroute an active execution. Revocation and explicit disable do stop new acquisition and controlled remote operations. An immutable plan is not a perpetual authorization grant.

## 7. Credential revision, issuance, and rotation

Keep three identities distinct:

| Identity | Meaning | Change behavior |
| --- | --- | --- |
| Connection | Stable authorized account/installation relationship | Changing actor or destination authority requires explicit admission. |
| Credential revision | Version of the PAT or underlying authentication configuration | Rotation fences attempts that pinned the prior revision. |
| Runtime issuance | Concrete materialized credential/token for an attempt | Expiry/refresh can change issuance while keeping the admitted connection and revision. |

At acquisition, persist attempt owner, connection, selected credential revision, binding digest, and issuance metadata before making the material available. Use transaction/compare-and-set checks so rotation between reading metadata and resolving the secret cannot attribute a new PAT to an old revision. Recheck current disable/revocation state.

### PAT rotation policy

The initial policy does not promise access to old PATs after replacement:

1. Accept the candidate through the Secrets System's restricted secret-write boundary.
2. Validate it and verify expected actor/resource-owner policy before activation.
3. Atomically activate the candidate and increment the shared secret/connection revision relationship, recording a secret-free audit event.
4. Invalidate affected capability evidence and deny new acquisition of the old revision.
5. Fence affected attempts at their next controlled remote-operation boundary. Preserve their workspace and saved work.
6. Allow an explicitly authorized resume to use the replacement revision on the same connection after revalidation. Record the change as a new attempt, not an invisible retry.

A failed candidate leaves the existing credential usable. Direct updates through Managed Secrets must follow the same revision/invalidation contract. Add reference-use tracking and deletion protection for connection consumers, with actor, scope, request ID, and audit events for create/update/rotate/attach/detach/disable.

When uninterrupted old-revision use is a real requirement, implement bounded immutable secret revisions inside Managed Secrets. Do not add a source-control-only version store. Until then, attempts needing unavailable old material return a typed fenced/unavailable result.

### Expiring issuance

Refresh a GitHub App token for the same installation, admitted repository set, and permitted operations through the acquisition boundary. Refresh does not select another connection or broaden scope. Use expiry/skew margins, bounded retry, and single-flight coordination to prevent refresh storms. Cache by the full scope and revision, not merely by connection ID. Clear credential caches on revocation.

Runtime handles are not bearer capabilities. A caller must independently authenticate as the owning execution/service before a handle can resolve material or execute an operation. Public audit records contain metadata only.

Disabling a connection cannot revoke a raw PAT already copied by arbitrary agent code. The interface must distinguish local disable from provider-side revocation, and high-security execution must not rely on local disable alone for that guarantee.

## 8. Runtime delivery and security boundaries

### 8.1 Bound clients, not token plumbing

Replace new-execution calls that accept optional PATs and discover ambient credentials with a factory bound to admitted access:

```python
client = await repository_clients.for_access(access_snapshot, execution_owner)
await client.read_repository(...)
```

The repository transport and hosting-service API adapters can be separate implementations beneath that boundary. A Git clone must not require a GitHub `/user` or PR API probe merely to discover an anonymous public default branch. Unsupported hosting features return a capability error, not a request to supply an irrelevant PAT.

No secret values enter Temporal inputs/results, heartbeat details, exception serialization, plan digests, audit payloads, Docker labels, or persisted runtime metadata. Value-carrying return types stay inside the trusted runtime delivery boundary and must be non-serializable/redacted by default.

### 8.2 Reuse delivery adapters

Reuse the existing local broker and the existing Omnigent lease-owned credential materialization rather than replace both with a universal token file. Share admission and acquisition semantics. Keep delivery mechanisms appropriate to each runtime's authority and cleanup model.

Git and `gh` delivery must:

- Use credential-free remote URLs and avoid tokens in command arguments or Docker configuration/environment metadata.
- Isolate `HOME`, Git config, and `GH_CONFIG_DIR` per execution. Scrub token variables, inherited headers, credential helpers, `.netrc`, and host login caches unless explicitly part of the admitted adapter.
- Reset inherited credential-helper configuration, validate exact protocol/host/repository path, and use path-sensitive credential matching where required. Git's HTTP credential matching otherwise omits the path by default. See [gitcredentials](https://git-scm.com/docs/gitcredentials).
- Prevent environment credentials from overriding a selected `gh` configuration. The [GitHub CLI environment contract](https://cli.github.com/manual/gh_help_environment) gives token environment variables precedence over stored credentials.
- Use attempt-owned delivery objects and generation-fenced cleanup. A stale attempt cannot remove another run's credential volume or refreshed issuance.
- Keep material outside captured workspaces, checkpoints, reports, and downloadable bundles. Test crashes, cancellation, stale retries, and cleanup sweeps, not only normal release.

Anonymous access creates no token file, no GitHub login, and no credential materializer. Where the installed `gh` CLI requires authentication for a requested operation, either use an explicitly supported portable unauthenticated tool interface or reject that tool/runtime combination. Do not fabricate a token or silently reimplement Skill semantics in native workflow code.

### 8.3 State the exact security guarantee

**Routing guarantee:** All MoonMind-controlled repository operations use the admitted repository, operation, and identity. A broadly scoped PAT is not intentionally offered to unrelated targets.

**Confinement guarantee:** Arbitrary code cannot use authority outside its admitted scope. This requires provider-limited credentials matching the admitted scope, or an operation-mediating service that never exposes the broader credential and has no network/credential bypass.

A raw PAT in an agent-readable `gh` configuration or a broker that returns the raw PAT does not establish confinement to MoonMind's narrower binding. Display this limitation for credential-exposing execution. Do not advertise `publish.mode=none` as a security sandbox when the process can read a broad write token.

For MoonMind-managed publication, prefer keeping destination write credentials entirely outside the agent and acquiring them only in the trusted publisher. Agent-owned `auto` Skills that need direct remote access require a separately qualified mediated path or an explicitly accepted credential-exposing policy. High-security mode rejects unsupported confinement rather than silently reducing isolation.

### 8.4 Network and source safety

Anonymous means no authentication, not unrestricted network access. Reuse endpoint/egress policy. Reject embedded URL credentials, lookalike hosts, unsafe protocols, unapproved private-network destinations, and unvalidated redirect targets. Revalidate on redirects and before credential delivery. Never forward authorization to another host.

Clone/import must use trusted Git configuration and bounded subprocesses. Repository-controlled hooks, filters, submodules, LFS endpoints, and external fetches are not implicit new authority. Treat additional repositories/endpoints as declared dependencies with separate validation, or report an unsupported/incomplete-source condition before agent execution. Do not silently skip required private submodules or LFS content and report a complete workspace.

Do not classify every anonymous clone failure as `repository_auth_required`. A `404` can conceal lack of access or indicate a missing repository. Distinguish known authentication challenge, inaccessible-or-missing repository, missing branch, empty remote, network failure, and unsafe source. Show actionable uncertainty where the provider does not disambiguate.

## 9. Durable workspace creation and restore

Implement source preparation behind the existing workspace materialization boundary. All source kinds use server-generated locators, authoritative workspace storage, normal UID/GID handoff, quotas, and cleanup ownership. Do not create a second scratch-workspace directory hierarchy or bypass remote Docker daemon path translation.

Preparation is idempotent. Populate a staging location, verify content and ownership, then record the completed materialization. An existing directory alone is not proof of a completed clone/import. Retries reconcile partial state and never expose a half-prepared workspace to the agent.

For scratch code, initialize an empty local repository only when the output/checkpoint profile needs it. Use a neutral MoonMind-local commit identity, an empty baseline commit, and no remote. Report/document tasks can use ordinary directories. Extend checkpoint schemas that currently require a Git base commit instead of filling them with fake commits or repository IDs.

Artifact/checkpoint import must verify authorization and digest before extraction, bound decompressed size and file count, and reject path traversal, escaping symlinks/hardlinks, device files, and privileged metadata. Treat imported executable content and Git configuration as untrusted. Never restore runtime credential directories, hooks, helper configuration, or old session authority.

Once an exact source snapshot is materialized, local compute and save-only finalization do not reacquire repository credentials unless an explicitly declared tool operation needs them. Source-token expiry is not by itself a reason to discard already-authorized local work. Current execution policy still controls whether a disabled run may continue.

Continue/restore creates a fresh execution owner and re-admits any requested external operations. Workspace restoration and provider-session reattachment remain separate capabilities. A portable saved archive must not secretly require access to its original private repository. Thin bundles or missing LFS/submodule objects must be labeled incomplete, and restoration either obtains separately authorized dependencies or refuses the claimed portable mode.

## 10. Saved work is the primary durable product result

### 10.1 Reuse artifacts and checkpoints

Extend the existing checkpoint/artifact contracts with a compact saved-work manifest. It is an artifact-backed index of verified outputs, not another blob store or mutable source of truth. Reuse an existing checkpoint archive when it satisfies the output contract rather than upload the same bytes repeatedly.

The manifest identifies:

```text
schema version and digest
workflow/run/step/attempt identity
source kind and immutable source provenance
snapshot/checkpoint reference and capture contract version
file manifest and content digest
report reference
optional Git baseline/head, patch, and bundle references
format availability/completeness and exclusion reasons
security-scan/validation evidence references
retention policy and ownership scope
```

It contains no live connection handles, tokens, session credentials, or executable approvals. Source connection identity may appear as audit provenance but grants no authority on restore. Publication records reference the immutable saved-work artifact and do not mutate it.

### 10.2 Formats are conditional

| Output | Purpose | Contract |
| --- | --- | --- |
| Worktree snapshot/archive | Downloadable and restorable content | Required for workspace-producing portable results, with manifest, digest, exclusions, and completeness. |
| Report | Human-facing result | Use agent output where available, otherwise a factual execution summary. Do not invent successful tests. |
| Binary-safe patch/file delta | Apply changes against a known baseline | Record the exact baseline and add/modify/delete/rename semantics. Omit or mark inapplicable when no meaningful baseline exists. |
| Git bundle | Preserve selected Git history | Include only approved necessary refs/history, validate it, and state whether it is self-contained. Not universally required. |
| MoonMind checkpoint | Continue within MoonMind | Use the workspace authority's supported capture/restore contract, not an assumed local-sandbox contract for all runtimes. |

Avoid unconditional `git add -A` on the active workspace and unconditional `git bundle --all`. They can include credentials, ignored build products, unrelated refs, or imported history the user did not intend to export. Prefer a capture-owned, policy-approved snapshot/index when local commits are needed. Do not rewrite agent history merely to produce a download.

Capture untracked meaningful outputs as well as tracked changes. Record exclusions explicitly so a truncated archive is not presented as complete. Git exports must account for their reachable history, binary content, and external object dependencies. Do not assume a clean current worktree means a safe Git bundle.

MoonMind-issued runtime credentials are forbidden in all exports. Apply the Secrets System's required outbound controls to snapshots, reports, patches, bundles, and publication. If capture discovers unsafe content, quarantine/restrict recoverable work and return a safe diagnostic. Do not silently discard the entire workspace or leak detected values into errors. Source-derived confidential data remains governed by artifact scope and data-use policy.

### 10.3 Finalization and retention

Keep compute outcome, save outcome, and publication outcome separately visible within the existing result/finalization model. Do not infer all three from a single process exit or a dashboard projection.

```text
quiesce writers
  -> capture approved snapshot
  -> upload/verify required artifact objects
  -> commit the immutable manifest/checkpoint references
  -> record saved-work outcome
  -> perform optional admitted publication
  -> release remaining workspace/cleanup authority
```

A failed or cancelled run still performs bounded capture where possible, without changing its primary outcome to success. A save failure retries the same capture with stable idempotency and retains the authoritative workspace under a bounded recovery policy. Cleanup cannot destroy the only copy because an auxiliary report or status projection failed.

Credentials no longer needed for compute should be released independently of workspace retention. Saving scratch work must not keep a repository lease alive or require a GitHub call. A publication failure retains the saved result and offers publication-only recovery.

Publish/download/restore authorization comes from artifact ownership and explicit policy, not the ability to reacquire an old PAT. Enforce this in API, previews, signed URLs, and retention management. Retain manifest-reachable objects and baseline dependencies for the saved-work retention period. Report expiry/deletion honestly. Apply quotas and bounded retention so preserving failed work does not cause unbounded disk growth.

Update recovery contracts that currently require remote Git checkpoint publication: credentialless execution completes its durability handoff through verified artifact storage. A recovery branch may be published only when the destination and mutation authority were admitted. Artifact durability must not be conditional on GitHub availability.

## 11. Publish Saved Work

Introduce publication-only execution through the existing publishing and repository adapter boundaries. It accepts an immutable `savedWorkRef`, an authorized destination target, existing publication mode, and explicit application policy. It does not rerun the agent by default.

The destination uses the same `RepositoryConnection` selection, authorization, capability compilation, client policy, and bound clients as immediate publication. Never create a separate GitHub publishing token setting.

### Workflow

1. Authorize access to the saved result and verify its digest/completeness.
2. Admit the destination repository, branch, connection, operations, policy, and expected remote state as a new publication execution.
3. Restore the saved content into a clean contained workspace without restoring old credentials or side-effect approvals.
4. Fetch and freeze the destination baseline through its admitted access.
5. Build a deterministic candidate using the selected application strategy, preserving source/run provenance.
6. Scan and validate the candidate under current destination policy.
7. Publish using the existing protected-branch and compare-and-set/lease rules.
8. Verify the exact remote revision and create/adopt a PR only when requested and supported.
9. Emit the canonical provider-aware publication evidence linked to the saved-work digest.

No step should need the original source PAT when the saved result is declared self-contained. Different source and destination identities are explicit, not a hidden switch inside the original run.

### Application strategies

| Destination relationship | Default behavior |
| --- | --- |
| Same repository with known baseline | Apply the recorded delta against the admitted destination base. Reconcile conflicts and changed remote tips explicitly. |
| Existing unrelated repository | Default to a previewed, additive/path-mapped import. No whole-tree replacement or inferred deletions. |
| Explicitly selected empty repository | A compatible saved Git history may be pushed after verifying it is still empty and policy permits the target branch. |
| Lore-authoritative destination | Publish through the Lore adapter. GitHub review projection is not repository-content authority. |

For an existing repository, never copy a scratch tree over it and delete every destination file absent from the scratch output. Deletions require a source baseline or an explicitly approved import manifest. Surface conflicting paths, case collisions, rename ambiguity, executable-bit changes, and binary conflicts. Do not automatically merge unrelated root histories.

Remote repository creation is a separately authorized capability, not an implied part of publishing to an empty repository. Preserve existing branch/PR rules, including generated PR head branches and protected-branch policy. A source branch and a separate publication destination are different roles, not a revival of conflicting source/target aliases on one role.

Use an idempotency key tied to saved-work digest, admitted destination, strategy, and intended branch. Persist the candidate and observed remote expectation before mutation. After a lost push or PR-create response, reconcile exact remote state before retrying. Do not repeat a mutation merely because the local response was lost.

A stale baseline or conflict returns a blocked publication result with the original saved work intact. Replanning onto a newer base produces a new candidate/attempt and the required approval. Changing to another connection or destination requires explicit re-admission. Publication success/failure never mutates the original saved-work artifact.

`auto` remains agent/Skill-owned in ordinary runs. Publication-only recovery consumes verified saved output and canonical publish evidence rather than rerunning a Skill's semantic decisions or accepting stale evidence from another attempt.

## 12. Credential-independent model compute

### Preserve current identities

Keep the existing `opencode-zen-free` profile and its separation from keyed profiles. Preserve explicit operator disable/default choices and do not reinterpret `OPENCODE_API_KEY` as permission to convert anonymous runs into a paid/keyed mode. A new `anonymous` auth-state enum is not required merely to complete this feature. Reuse the current profile readiness abstraction and qualify `none@1` through the actual host.

The current documentation already advertises this path. Verify the deployed Host Class, runtime pack, exact image digest, admission, and registration before creating migrations. Correct any real mismatch, but do not add a second free-profile identity or duplicate startup logic.

### Catalog and privacy policy

Select only from the exact runtime's observed and qualified catalog. A model's free-looking name is not pricing evidence. Require known zero cost for all relevant billing dimensions, compatible tool capabilities, availability, and acceptable data-use terms. Unknown prices or terms are ineligible for the automatic free route.

[OpenCode's Zen documentation](https://opencode.ai/docs/zen/) describes changing free offerings and model-specific privacy exceptions. Anonymous availability in a pinned runtime must therefore be verified, not presented as a permanent external guarantee.

Persist model ID, catalog digest, host/runtime identity, qualification time, pricing/data-use evidence, and selection-policy version. Rank a small approved candidate set with deterministic tie-breaking. Do not claim to infer coding reliability from catalog fields that contain no such evidence.

Eligibility and privacy are filters, not lower-ranked preferences. Contributor/training terms require explicit operator acceptance. Removing an implicit acceptance default must not fabricate historical consent. An existing explicit choice can be preserved with provenance.

An automatic policy may select a new qualified free model for a new run. It must not silently replace the model halfway through an active attempt. Any permitted continuation onto another model is an audited new attempt under an expressly admitted fallback policy, with the saved workspace preserved. Never cross into paid models or a different data-use class automatically.

### Honest zero-config promise

MoonMind should start without a `.env`, GitHub PAT, or model key and offer useful scratch execution when an eligible anonymous model is available. Startup assumes documented Docker/resources/submodule/image/network prerequisites. The application must remain usable for settings, artifact access, and diagnostics if the external free provider is unavailable.

When no eligible anonymous model exists, show `no_eligible_free_model` with the precise reason and an optional configured-provider path. Do not request a GitHub PAT to fix a model problem, report a disabled profile as ready, silently accept contributor terms, or spend money to preserve the appearance of zero-config success.

Default public runtime image acquisition must also work without a source PAT. Private registry credentials are explicit deployment authority. Verify this dependency in the clean-install journey.

## 13. Product UX

### First execution

The streamlined default is:

```text
Workspace: New blank workspace
Results: Save in MoonMind
Publication: Do not publish
Agent: Existing automatic default, clearly labeled free/anonymous when applicable
```

Repository selection, artifact import, checkpoint continuation, and advanced local-workspace access are alternative source choices. Do not show a mandatory GitHub connection dialog for scratch or anonymous public reads. Do not force users to understand credential slots or secret references.

A request for remote mutation remains an authority-sensitive choice. When it lacks credentials, the UI offers an explicit change to save-only or connection setup. It does not silently alter an already submitted task's publication objective.

### Source Control Settings

Use one page for existing `RepositoryConnection` entities. The PAT wizard is:

```text
Name -> Paste token -> Verify actor -> Choose repositories -> Save and test
```

Create the Managed Secret internally. An authorized advanced action can reuse an existing secret. Separate authenticated actor from resource owner. Show display name, actor, repositories, health, and expiry prominently. Put revisions, materializers, raw validation details, and advanced per-operation routes in an expandable detail view.

Repository routing starts with one visible default. Advanced users can configure separate read/publication roles. Discovery is scoped to connections the principal can use and deduplicated by endpoint/provider identity. Discovery itself must use a selected connection, not a global token. Public URL entry must not require authenticated repository discovery.

Show the resolved identity on creation and Workflow Detail even when only one connection exists:

```text
Source: Anonymous read
Publication: None
```

or:

```text
Source and publication: Moon Ladder GitHub
Authenticated actor: moonladder-bot
```

Distinguish expired/revoked credentials, approval requirements, denied operations, unknown write evidence, throttling, unavailable endpoints, and ambiguous routing. Avoid a single misleading Disconnected flag.

### Results and recovery

Completed or partially completed runs expose the report, available downloads, save completeness, retention, Continue working, and Publish Saved Work. Optional inapplicable formats are not broken buttons. Saved-but-not-published is a successful save state, not a credential failure.

Show compute, save, and publication outcomes separately. A blocked publication offers publication-only recovery. A failed save names the preserved workspace and bounded retry state without claiming the data is already durable.

## 14. Implementation sequence

The slices share one contract. They are not independent designs to be merged after each implements its own credential logic. Do not enable the new public submission path until all supported consumers enforce the same bindings. Internal staging may use explicit version gates, never connection-count heuristics.

| Slice | Deliverables and dependency | Exit evidence |
| --- | --- | --- |
| **0. Contract reconciliation and consumer inventory** | Confirm current write/read schemas, runtime support matrix, existing free-profile behavior, workspace/capture authority, and every global-token caller. Specify versioned access intent, source union, saved-output contract, and publication roles. Identify canonical doc changes. | A reviewed contract and traceability matrix covering anonymous and authenticated paths, with no duplicate connection domain. |
| **1. Connection persistence and lifecycle** | Extend `RepositoryConnection`, normalized routes, scope checks, secret-reference authorization, atomic revision lifecycle, non-mutating probes, and minimal Settings wizard. Preserve the current default connection identity. | Transactional routing/rotation tests and working per-connection diagnostics. |
| **2. Bound access and plan cutover** | Typed credential-binding union, immutable access snapshots, bound transport/API clients, acquisition/refresh interface, isolated runtime materialization, and separate registry auth. Update all supported consumers below. | PAT A/B isolation, no ambient fallback, version-one replay, and expiring-issuance contract tests. Unsupported lanes fail admission. |
| **3. Workspace source decoupling** | Scratch, anonymous Git, artifact/checkpoint input, branch discovery, safe partial-preparation recovery, and capability-based launch without `gh`. Build on Slice 2's explicit anonymous access contract. | Real workspace preparation with no GitHub resolver calls and no hidden login/config dependency. |
| **4. Saved-work finalization** | Verified snapshots/manifests, applicable portable exports, failed/cancelled capture, retention, restore, and save-before-cleanup. Update remote-checkpoint assumptions. | A saved result survives host removal and can be downloaded/restored without the original source token. |
| **5. Publish Saved Work** | Destination admission, safe delta/import policy, canonical publisher integration, exact remote verification, conflicts, and publication-only recovery. | Same-repository and unrelated scratch-to-existing-repository journeys, lost-response reconciliation, and intact original artifacts. |
| **6. Default experience and retirement** | Qualify free-model policy, privacy/default behavior, public image pulls, UI create/detail/results, no-key README path, and end-to-end default execution. Retire superseded internal new-write paths. | Clean-install and upgrade journeys, full supported-runtime matrix, documentation reflecting actual shipped behavior. |
| **7. GitHub App acquisition adapter** | Installation enrollment/configuration, scoped token issuance/refresh/revocation, endpoint capability validation, and UI. Reuse every existing consumer. | The same execution and publication journeys pass with expiring installation tokens without PAT-specific consumer changes. |

A fake expiring-credential adapter belongs in Slice 2 conformance before the PAT overhaul is enabled. Live GitHub App support can arrive in Slice 7, but the boundary must already demonstrate refresh, scope pinning, and revocation. OAuth, SSH, enterprise endpoints, and additional hosts remain capability additions, not unimplemented choices exposed as working UI.

### Required consumer cutover

| Boundary | Starting points | Required change |
| --- | --- | --- |
| Authoring/admission | [`repository_contract.py`](../../moonmind/workflows/executions/repository_contract.py), [`workspace_intent.py`](../../moonmind/omnigent/workspace_intent.py) | Preserve access intent, optional repository source, canonical aliases, and role-specific capability derivation. |
| Secrets/settings | [`secrets.py`](../../api_service/services/secrets.py), [`settings.py`](../../api_service/api/routers/settings.py), [`models.py`](../../api_service/db/models.py) | Connection persistence, protected secret use, revision lifecycle, scoped discovery/probes, and route constraints. |
| GitHub API consumers | [`github_service.py`](../../moonmind/workflows/adapters/github_service.py), [`jules_client.py`](../../moonmind/workflows/adapters/jules_client.py), [`story_output_tools.py`](../../moonmind/workflows/temporal/story_output_tools.py) | Bound clients for PR/issue/readiness/review/story operations. Preserve portable Skill semantics. |
| Managed launch | [`managed_api_key_resolve.py`](../../moonmind/workflows/temporal/runtime/managed_api_key_resolve.py), [`launcher.py`](../../moonmind/workflows/temporal/runtime/launcher.py), [`managed_session_models.py`](../../moonmind/schemas/managed_session_models.py) | Remove new-run ambient discovery, carry non-sensitive authority, separate registry auth, and support no repository credentials. |
| Omnigent planning/acquisition | [`harness_platform/`](../../moonmind/omnigent/harness_platform/), [`provider_leases.py`](../../moonmind/omnigent/provider_leases.py), [`credential_materializers.py`](../../moonmind/omnigent/credential_materializers.py) | Dispatch typed authorities without assuming every slot is a Provider Profile. |
| Omnigent host/workspace | [`host_services/workspace.py`](../../moonmind/omnigent/host_services/workspace.py), [`host_services/github_credentials.py`](../../moonmind/omnigent/host_services/github_credentials.py), [`realizers/generic_host.py`](../../moonmind/omnigent/realizers/generic_host.py), [`profile_bound_execution.py`](../../moonmind/omnigent/profile_bound_execution.py) | Source union, bound materialization, qualified anonymous execution, ownership, cleanup, and compatibility-lane rejection where unsupported. |
| Publication/recovery | [`publish/service.py`](../../moonmind/publish/service.py), [`workspace_publication.py`](../../moonmind/omnigent/workspace_publication.py), [`omnigent_activities.py`](../../moonmind/workflows/temporal/activities/omnigent_activities.py) | Role-specific authority, durable save-only finalization, publication-only recovery, and child/retry binding preservation. |
| Checkpoint/results | [`managed_checkpoint_models.py`](../../moonmind/schemas/managed_checkpoint_models.py), [`checkpoint_policy.py`](../../moonmind/workflows/temporal/checkpoint_policy.py), [`authority_chain.py`](../../moonmind/omnigent/authority_chain.py) | Optional Git metadata where valid, verified portable saved outputs, separate session/workspace restore evidence, and truthful terminal outcomes. |
| Bootstrap/default UI | [`main.py`](../../api_service/main.py), [`omnigent_agent_bootstrap_service.py`](../../api_service/services/omnigent_agent_bootstrap_service.py), [`frontend/src/`](../../frontend/src/), [`README.md`](../../README.md) | Preserve operator defaults, qualify current free route, remove unconditional repository/PAT prerequisites, expose save/publication results. |

During Slice 0, enumerate additional imports and call sites of `resolve_github_credential`, `resolve_github_token_for_launch`, optional `github_token` parameters, and token environment readers. Add a CI guard against new execution-bound consumers reintroducing global resolution. The table is a starting inventory, not a claim that an unexecuted grep proved complete coverage.

### Migration and rollback

Preserve `repository-connection:git-default` for the effective legacy configuration. Do not create `source-control:github:legacy-default` as a second default identity. Migration records which legacy source actually won precedence. It must not assume `GITHUB_TOKEN` was the source or permanently poll several sources afterward.

Import known repository bindings without granting a wildcard. Where legacy scope cannot be safely determined, surface an explicit migration requirement for future authenticated runs while preserving existing history decoding. Scratch execution must not be blocked by that migration requirement.

Environment-backed deployments can retain one explicit environment SecretRef per imported connection until administrators rotate it into another supported backend. Missing that source is an error for that connection, not permission to discover another token. Environment reads are bootstrap/acquisition implementation details, never per-operation routing.

Support in-flight histories through frozen readers and compatible workers. Do not roll version-two executions back into a singleton resolver. If new consumers must be disabled, stop new admissions and preserve/drain supported in-flight work. Historical secret bodies must not be copied into new plans or migration audit events.

Delete superseded internal aliases, token fields, singleton discovery, and obsolete new-write docs in the cohesive cutover. Retain compatibility only where identified durable histories or persisted records require it, with an owner and removal condition. Do not make indefinite fallback chains the migration strategy.

Canonical updates belong with the relevant slices: repository/Lore integration, publishing, workspace locators, Secrets System, Provider Profiles, OpenCode host, checkpoint/recovery, and Quick Start. In particular, update claims that `none` leaves data only in an ephemeral workspace or that failed work must always reach a Git remote. Do not prematurely advertise a shipped zero-config journey in this plan-only change.

## 15. Definition of done and test strategy

Use the repository's existing test taxonomy. Required CI must remain hermetic. Exercise production Activities/adapters with local Git remotes, real object storage/DB boundaries where selected, fake identity/token endpoints, and controlled clocks. External provider availability is checked in separately labeled live qualification, not used to make ordinary PR CI flaky.

### Essential acceptance matrix

| Scenario | Required behavior |
| --- | --- |
| Scratch report, no PAT/model key in environment | No repository target, resolver call, GitHub probe, or `gh` requirement. Qualified model execution saves a downloadable report. |
| Scratch code | Local content and applicable Git exports are durable before host cleanup, without a remote. |
| Public repository, explicit anonymous access | Clean anonymous clone and local edits succeed. Ambient PATs and host credential caches are never used. |
| Anonymous inaccessible/missing repository | Actionable typed failure, no credential probing or false certainty about `404`. |
| Explicit connection B while ambient PAT A exists | Clone, API calls, `gh`, publication, and recovery use B or fail closed. |
| Two concurrent identities on one host | No shared environment/config/helper/volume/cleanup crossover. |
| Two PATs for the same GitHub actor | Account-level quota is not treated as two independent budgets. |
| Multiple matching routes | Exactly one default or an explicit selection is required. Concurrent conflicting defaults fail transactionally. |
| Connection count changes from two to one | Strict routing remains unchanged. |
| Credential rotates during admission/acquisition/retry | Revision attribution remains correct. Old attempts are fenced. Candidate validation failure preserves the old active credential. |
| Expiring App-like issuance | Same authority refreshes without actor/scope change. Concurrent refresh and revocation are bounded and tested. |
| Connection disabled while queued or active | New acquisition/controlled remote operations are denied. No identity fallback. Raw-token revocation limitations are not hidden. |
| Public GET succeeds with insufficient/pending token | No false verified-write or private-access evidence. |
| Skill edits issues with `publish.mode=none` | Issue authority is required from its declaration. No final repository publication is inferred. |
| Unsupported mediated `auto` execution | High-security policy rejects before launch instead of exposing a broad PAT under a false confinement claim. |
| Unsafe URL/archive/config/submodule/LFS dependency | Rejected or explicitly admitted through supported authority. No SSRF, credential forwarding, path escape, or silent incomplete checkout. |
| Crash during preparation | Partial directory is not accepted as ready. Retry completes or safely reconciles it. |
| Crash/failure/cancellation during save | Required verified outputs or bounded preserved recovery state remain. Cleanup never destroys the sole copy. |
| Save succeeds, remote publication fails | Compute/save evidence stays successful and downloadable. Publication-only retry is available. |
| Restore private-source saved work without original PAT | Self-contained content restores under artifact authorization. No live source credential/approval is restored. |
| Scratch import into existing repository | Unrelated destination files remain untouched by default. Conflicts and explicit deletions are visible. |
| Lost push/PR-create response | Retry reconciles remote evidence with the same publication binding instead of duplicating work. |
| Source repository renamed/transferred | Verified rename reconciles identity. Owner transfer triggers policy revalidation. |
| Tenant/actor attempts unauthorized discovery/probe/secret attachment | Rejected without leaking another scope's metadata or secret values. |
| Runtime credential leakage checks | No MoonMind-issued credentials in histories, artifacts, Git config, argv, Docker metadata, logs, or saved snapshots. |
| New implementation reads a version-one history | Original meaning/digest remains valid. New writes cannot use legacy aliases. |

GitHub's [rate-limit documentation](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api) distinguishes actor, installation, and unauthenticated-IP accounting. Track endpoint/resource buckets and honor retry/reset headers. Throttling causes bounded Temporal waits or retryable outcomes, not credential invalidation or switching. Include anonymous shared-IP throttling in the matrix.

### Clean-install deployment journey

Run against a clean checkout with initialized submodules, fresh volumes, no `.env`, and no GitHub/model credentials or login caches. Under controlled eligible-model availability, verify:

1. The documented Compose command starts the required infrastructure and obtains public runtime images without source credentials.
2. The existing free profile and exact host qualify without an API key, while explicit privacy/default policy is respected.
3. The normal UI/API submits a scratch workflow with omitted/default values, not a test-only enablement flag.
4. The agent writes a file and produces a useful result in the authoritative workspace.
5. Required artifacts are uploaded, verified, presented, and downloadable.
6. The host can be destroyed and the result restored for a new turn without GitHub access.
7. Public Git input works anonymously through the same admission path.
8. Adding a connection enables separately admitted publication of that saved work without rerunning the agent.
9. With no eligible model, the application remains usable and reports the actual model/pricing/privacy failure without paid fallback or a PAT requirement.

Run a separate live anonymous-provider smoke to verify current third-party availability and exact runtime behavior. Do not claim that mocked catalog/admission tests alone prove the no-account product journey.

### Runtime and upgrade coverage

Cover the supported runtime x source x access-mode x output/publication combinations for generic OpenCode, direct managed compatibility paths, and profile-bound/generic Codex or Claude lanes where currently supported. Advertised generic support must not be inferred merely from a shared image or the existence of a capture function. Reject unqualified combinations before launch.

Extend existing fixtures and tests, including [`test_default_omnigent_launch_authority.py`](../../tests/unit/services/test_default_omnigent_launch_authority.py), [`test_startup_profile_seeding.py`](../../tests/integration/test_startup_profile_seeding.py), and [`test_omnigent_publication_semantics_journey.py`](../../tests/integration/reliability_journey/test_omnigent_publication_semantics_journey.py). Add minimized replay fixtures for escaped failures. Test database upgrades, explicit operator defaults, saved-draft migration, active-run replay, and rollback admission fencing.

## 16. Scope boundaries and completion criteria

Deliver the shared contract, safe multi-PAT routing, scratch/public/import/restore sources, saved outputs, deferred publication, and tested default behavior. Do not block the initial release on universal provider support, arbitrary host mounts, a general credential platform, a new free-model marketplace, or a second publishing engine.

GitHub Apps should require only a new acquisition/enrollment adapter and its conformance evidence. Additional repository hosts must implement their declared transport/collaboration capabilities rather than masquerade as GitHub. Strong raw-agent confinement remains an explicit security capability, not a retrospective claim attached to PAT routing.

The overhaul is complete when repository-independent work never enters repository credential resolution, authenticated work uses one explicit authority chain, saved work outlives both host and credentials, and immediate/deferred publication use the same bound destination machinery. A future authentication method must be demonstrably insertable without altering those consumers.

## 17. Source and recommendation traceability

| Input | Preserved intent | Deliberate refinement |
| --- | --- | --- |
| Multiple-PAT proposal: identity, routing, secret storage | Named connections, many-to-many repository assignments, deterministic selection, last-boundary secret resolution | Reuse `RepositoryConnection`, typed scopes/capabilities, explicit anonymous mode, no connection-count fallback. |
| Multiple-PAT proposal: execution bindings and retries | Immutable admission, runtime handles, audit identity, replay compatibility | Typed union, role-specific authority, policy snapshots, separate credential revision and expiring issuance. |
| Architecture review: simplicity/security/reliability | Existing-domain reuse, honest confinement, safe rotation, validation distinctions, isolation, UX | Included in Sections 4 through 8 and the migration/test gates. |
| Secondary proposal: source and destination independence | Scratch/import/restore/public sources, artifacts, publish later | Keep existing top-level repository and publication semantics, do not duplicate source authority, preserve Lore. |
| Secondary proposal: free anonymous compute | Existing free Provider Profile, catalog-driven selection, explicit data-use policy | Correct stale seeding/materializer claims, preserve stable profile IDs, separate runtime qualification from a permanent availability promise. |
| Secondary proposal: local commits and portable results | Durable snapshots, applicable patches/bundles, resumable checkpoints | Safe capture policy, no unconditional `git add -A`/`--all`, runtime-qualified restore, bounded retention, collision-safe deferred publication. |

The uploaded proposals are design inputs. Current-state claims above come from the reviewed repository and linked provider documentation. New policies, schema additions, sequencing, and acceptance criteria in this document are proposed decisions, not claims that those behaviors are already implemented.
