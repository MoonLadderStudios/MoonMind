# Repository Access and Workspace Decoupling Plan

**Document Class:** Imperative working document  
**Viewpoint:** Implementation / Migration Plan  
**Status:** Proposed  
**Updated:** 2026-09-04  
**Canonical Target:** [Repository Access and Workspace Design](../RepositoryAccessAndWorkspaceDesign.md)  
**Reviewed Baseline:** [`63bce9852ffa33e33cb0b416bc24a654b1b6f92b`](https://github.com/MoonLadderStudios/MoonMind/commit/63bce9852ffa33e33cb0b416bc24a654b1b6f92b)  
**Delete/Archive Trigger:** Archive or delete after the supported capability matrix, migration, replay-safe retirement, and owning-document reconciliation are complete. Preserve durable requirements in the canonical design and its owning successor views.

This plan is the execution companion to the declarative design. It retains current-state findings, delivery dependencies, consumer cutover, migration/rollback, and qualification procedures from the consolidated September 4 proposals. Target semantics have moved to the design rather than remaining duplicated here. The design is the desired-state input for MoonSpec breakdown; this file is temporary implementation scaffolding, not an alternative authority.

The baseline observations below are source inspection, not deployment or integration-test evidence. Recheck them before implementation against the then-current repository. Neither the plan nor the design implies that the proposed API shapes or complete no-key journey are already implemented.

## 1. Delivery constraints and design traceability

| Work area | Canonical target |
| --- | --- |
| Optional repository access and responsibility separation | Design Sections 1–2, `DOC-REQ-001`, `DOC-REQ-002`, `CONTRACT-001`, `INV-001` |
| Source union, canonical authoring, access intent, and capability compilation | Section 3, `CONTRACT-002`, `CONTRACT-003`, `CONTRACT-004`, `INV-002` |
| Connection persistence, scope, routing, authentication variants, and validation | Section 4, `CONTRACT-005`, `CONTRACT-006`, `CONTRACT-007`, `QUALITY-001` |
| Typed execution bindings, rotation, refresh, revocation, and throttling | Section 5, `CONTRACT-008`, `CONTRACT-009`, `INV-003`, `QUALITY-002` |
| Bound consumers, isolation, confinement, and source safety | Section 6, `CONTRACT-010`, `INV-004`, `INV-005`, `QUALITY-003` |
| Workspace preparation and restore | Section 7, `CONTRACT-011`, `INV-006` |
| Saved-work formats, finalization, ownership, and retention | Section 8, `CONTRACT-012`, `INV-007`, `QUALITY-004`, `QUALITY-005` |
| Deferred publication and recovery | Section 9, `CONTRACT-013`, `INV-008`, `QUALITY-006` |
| Historical interpretation and observability | Section 10, `CONTRACT-014`, `QUALITY-007` |
| Free-model qualification and product UX | Sections 11–12, `DOC-REQ-003`, `DOC-REQ-004`, `DOC-REQ-005`, `INV-009`, `QUALITY-008` |
| Conformance and scope | Sections 13–14, `TEST-001`, `TEST-002`, `TEST-003`, `TEST-004`, `NON-GOAL-001`, `QUALITY-009` |

The slices share one contract. Do not enable a new public submission path until all supported consumers enforce its bindings. Internal staging may use explicit version gates, never connection-count heuristics. Unsupported runtime/capability combinations must be rejected rather than allowed to use legacy discovery.

Keep the default always-on container footprint unchanged. Reuse the existing repository, workspace, secret, artifact, publisher, and runtime boundaries. Do not make universal provider support or a general credential service a dependency of the initial overhaul.

## 2. Inspected foundation and implementation gaps

| Area | Observed at the reviewed baseline | Work implication |
| --- | --- | --- |
| Repository authority | [`repository_contract.py`](../../moonmind/workflows/executions/repository_contract.py) defines authored/resolved Git and Lore targets, `RepositoryConnection`, client/operation policy, and readiness. | Extend the existing domain and persistence; do not add `SourceControlConnection` as another owner. |
| Selection/authoring | The compiler inserts `repository-connection:git-default` for omitted Git connections. [`workspace_intent.py`](../../moonmind/omnigent/workspace_intent.py) reads runtime `repositoryTarget`, nested repository, and historical top-level forms. | Preserve selection origin until routing; reconcile new-write producers and frozen legacy readers. |
| Global credentials | [`github_credentials.py`](../../moonmind/auth/github_credentials.py) accepts a repository but resolves global precedence; [`GitHubService`](../../moonmind/workflows/adapters/github_service.py) calls it. | A repository argument alone is not routing. Replace new-execution discovery with bound access. |
| Generic workspace preparation | [`host_services/workspace.py`](../../moonmind/omnigent/host_services/workspace.py) requires a GitHub source, branch, and token for fresh sandboxes. | Add scratch/import/restore and explicit anonymous access at the shared boundary. |
| Clone transport | The generic clone path already uses a clean URL and stdin-delivered temporary helper. | Preserve it. The secondary proposal's claim that this particular path embeds the PAT is stale; audit remaining compatibility paths. |
| Omnigent CLI materialization | [`host_services/github_credentials.py`](../../moonmind/omnigent/host_services/github_credentials.py) projects global credentials into a lease-owned `gh` volume. | Preserve ownership/cleanup while consuming admitted bindings. |
| Secret lifecycle | [`SecretsService`](../../api_service/services/secrets.py) overwrites values; rotation sets `ROTATED`, while normal lookup selects `ACTIVE`. | Implement atomic revision semantics; a new counter cannot retrieve an overwritten old PAT. |
| Plan bindings | [`credential_bindings.py`](../../moonmind/omnigent/harness_platform/credential_bindings.py) and [`provider_leases.py`](../../moonmind/omnigent/provider_leases.py) assume Provider Profiles. | Type and dispatch repository authority without copying model capacity rules. |
| Free model route | [`OpenCodeHost.md`](../Omnigent/OpenCodeHost.md), [`main.py`](../../api_service/main.py), and default-admission tests already describe/seed `opencode-zen-free` with `none@1`, separate from keyed Go. | Qualify actual host/model/default gaps; do not rename or recreate the profile. |
| Saved-work substrate | [`managed_checkpoint_models.py`](../../moonmind/schemas/managed_checkpoint_models.py), [`checkpoint_policy.py`](../../moonmind/workflows/temporal/checkpoint_policy.py), and [`authority_chain.py`](../../moonmind/omnigent/authority_chain.py) contain checkpoint/result concepts. | Extend them; qualify capture and restore independently by workspace/runtime capability. |
| Publication | [`WorkflowPublishing.md`](../Workflows/WorkflowPublishing.md) defines existing modes/Skill ownership; [`LoreVcsIntegrationDesign.md`](../Workflows/LoreVcsIntegrationDesign.md) defines provider authority. | Reconcile transitional forms without introducing another publishing enum, semantic implementation, or evidence authority. |
| Onboarding | [`README.md`](../../README.md) still requests a PAT and model authentication in Quick Start. | Change operator-facing shipped-behavior claims only with executable no-key journey evidence. |

## 3. Implementation sequence

| Slice | Deliverables and dependency | Exit evidence |
| --- | --- | --- |
| **0. Contract reconciliation and inventory** | Confirm current read/write schemas, runtime support, free-profile behavior, capture authority, and global-token consumers. Map the design's source/access/output/publication additions to owning contracts. | Reviewed traceability and support matrix with no competing connection domain or new-write aliases. |
| **1. Connection persistence and lifecycle** | Extend `RepositoryConnection`, normalized routing, scope/secret-use authorization, atomic revision lifecycle, non-mutating probes, and a minimal Settings wizard. Preserve the existing default identity. | Transactional route/rotation tests and usable per-connection diagnostics. |
| **2. Bound access and execution cutover** | Typed credential-binding union, immutable snapshots, bound transport/API clients, expiring acquisition interface, isolated runtime delivery, and independent registry authentication. Update all supported consumers below. | PAT A/B isolation, no ambient fallback, original-history replay, and expiring-issuance conformance. Unsupported lanes fail admission. |
| **3. Workspace source decoupling** | Scratch, anonymous Git, artifact/checkpoint input, remote default-branch discovery, safe partial preparation, and capability-based launch without incidental `gh`. Use Slice 2 access intent. | Real preparation without GitHub resolver calls, hidden login state, or half-prepared directories. |
| **4. Saved-work finalization** | Verified manifests/snapshots, applicable exports, failure/cancellation capture, retention/restore, and save-before-cleanup. Reconcile remote-only recovery assumptions. | Downloads and self-contained restore survive host removal and loss of the original source credential. |
| **5. Publish Saved Work** | Destination admission, safe delta/import strategy, canonical publisher use, exact remote verification, conflict handling, and publication-only recovery. | Same-repository and scratch-to-existing-repository journeys, lost-response reconciliation, and immutable original artifacts. |
| **6. Default UX and retirement** | Qualify free-model pricing/privacy policy, exact host and public image pulls, normal create/detail/results UI, no-key onboarding, and supported default execution. Remove superseded internal new-write paths. | Clean-install/upgrade journeys, supported-runtime matrix, operator-choice preservation, and accurate docs. |
| **7. GitHub App acquisition adapter** | Installation enrollment, scoped issuance/refresh/revocation, endpoint capability evidence, and Settings UX using the same consumers. | Existing execution/publication journeys pass with expiring installation credentials without PAT-specific consumer changes. |

A fake expiring-credential adapter is required in Slice 2 before the PAT overhaul is enabled. Do not defer interface evidence for refresh, scope pinning, and revocation until live GitHub App enrollment. OAuth/device enrollment, SSH, enterprise endpoints, and other hosts remain separately qualified capabilities, not working UI choices before support exists.

## 4. Required consumer cutover

| Boundary | Starting points | Required change |
| --- | --- | --- |
| Authoring/admission | [`repository_contract.py`](../../moonmind/workflows/executions/repository_contract.py), [`workspace_intent.py`](../../moonmind/omnigent/workspace_intent.py) | Optional source, explicit access intent, canonical aliases, and role-specific capability compilation. |
| Secrets/settings | [`secrets.py`](../../api_service/services/secrets.py), [`settings.py`](../../api_service/api/routers/settings.py), [`models.py`](../../api_service/db/models.py) | Persistence, protected secret attachment/use, revision lifecycle, scoped discovery/probes, and constrained defaults. |
| Hosting API consumers | [`github_service.py`](../../moonmind/workflows/adapters/github_service.py), [`jules_client.py`](../../moonmind/workflows/adapters/jules_client.py), [`story_output_tools.py`](../../moonmind/workflows/temporal/story_output_tools.py) | Bound clients for PR/issue/readiness/review/story operations without duplicating portable Skill semantics. |
| Managed launch | [`managed_api_key_resolve.py`](../../moonmind/workflows/temporal/runtime/managed_api_key_resolve.py), [`launcher.py`](../../moonmind/workflows/temporal/runtime/launcher.py), [`managed_session_models.py`](../../moonmind/schemas/managed_session_models.py) | Non-sensitive access authority, no new-run ambient discovery, independent registry auth, and no-repository execution. |
| Omnigent planning/acquisition | [`harness_platform/`](../../moonmind/omnigent/harness_platform/), [`provider_leases.py`](../../moonmind/omnigent/provider_leases.py), [`credential_materializers.py`](../../moonmind/omnigent/credential_materializers.py) | Typed authority dispatch in planning, leases, continuation, child work, binding serialization, and cleanup. |
| Omnigent host/workspace | [`host_services/workspace.py`](../../moonmind/omnigent/host_services/workspace.py), [`host_services/github_credentials.py`](../../moonmind/omnigent/host_services/github_credentials.py), [`realizers/generic_host.py`](../../moonmind/omnigent/realizers/generic_host.py), [`profile_bound_execution.py`](../../moonmind/omnigent/profile_bound_execution.py) | Source union, anonymous execution, bound materialization, ownership/cleanup, and rejection of unsupported compatibility lanes. |
| Publication/recovery | [`publish/service.py`](../../moonmind/publish/service.py), [`workspace_publication.py`](../../moonmind/omnigent/workspace_publication.py), [`omnigent_activities.py`](../../moonmind/workflows/temporal/activities/omnigent_activities.py) | Destination authority, durable save-only finalization, publication-only recovery, and child/retry binding preservation. |
| Checkpoint/results | [`managed_checkpoint_models.py`](../../moonmind/schemas/managed_checkpoint_models.py), [`checkpoint_policy.py`](../../moonmind/workflows/temporal/checkpoint_policy.py), [`authority_chain.py`](../../moonmind/omnigent/authority_chain.py) | Valid non-Git metadata, portable saved work, separate session/workspace restore evidence, and truthful outcomes. |
| Bootstrap/UI | [`main.py`](../../api_service/main.py), [`omnigent_agent_bootstrap_service.py`](../../api_service/services/omnigent_agent_bootstrap_service.py), [`frontend/src/`](../../frontend/src/), [`README.md`](../../README.md) | Existing free-profile identity, operator defaults, removal of incidental PAT prerequisites, and source/save/publication UX. |

Enumerate remaining imports/call sites of `resolve_github_credential`, `resolve_github_token_for_launch`, optional `github_token` arguments, and token environment readers during Slice 0. Include repository discovery and GitHub probes, not just launches. Add a CI guard against reintroducing global resolution into execution-bound consumers. This table is a starting inventory, not a claim that an unexecuted repository-wide scan proved completeness.

## 5. Migration and rollback

Preserve `repository-connection:git-default` for the effective legacy configuration. Do not introduce a second synthetic default. Record the legacy source that actually won precedence rather than assuming `GITHUB_TOKEN`, and do not permanently poll several sources afterward.

Import known repository bindings without granting a wildcard. When legacy scope cannot safely be established, require explicit configuration for future authenticated runs while preserving historical decoding. That requirement must not block scratch work.

Environment-backed deployments can retain one explicit environment SecretRef per imported connection until rotation into another supported backend. Missing that source is an error for that connection, not permission to discover another token. Environment reads belong to bootstrap/acquisition, not routing.

Migrate saved drafts explicitly so automatically inserted defaults do not become false explicit choices. Reject conflicting aliases in new requests. Freeze historical parsing/digest rules and use the established Temporal cutover mechanism for changed workflow decisions, with compatible workers for in-flight work.

Do not roll versioned executions back into the singleton resolver. If new consumers must be disabled, stop admissions and preserve/drain supported in-flight work. Do not copy historical secret bodies into new plans or audit events.

Remove superseded aliases, token fields, singleton discovery, and old new-write documentation in the cohesive cutover. Compatibility exceptions require an identified durable-history/persisted-record need, owner, and removal condition. Indefinite fallback chains are not the migration strategy.

## 6. Owning-document reconciliation

Keep this plan imperative and the design declarative. Reconcile the providing module's contracts as each accepted change is implemented, without duplicating formal schemas into consumer docs. Required alignment includes:

| Owning surface | Reconciliation concern |
| --- | --- |
| [`AGENTS.md`](../../AGENTS.md) and system/workflow architecture | Explicitly align the proposed artifact-backed durability handoff with recovery guidance that currently requires remote checkpoint publication. Do not treat an unaccepted proposal as permission to bypass a current gate. |
| Repository/Lore integration contracts | Optional source, explicit anonymous/routed/explicit access, normalized persistent connections, and unchanged Git/Lore authority distinction. |
| [`WorkflowPublishing.md`](../Workflows/WorkflowPublishing.md) | Durable save-only results, explicit `none`, capability-based side effects, same publisher for deferred work, and no duplicate terminal-evidence contract. |
| Workspace locators and checkpoint/recovery docs | Non-Git sources, staging/ownership, supported capture/restore, completeness, and independent session reattachment. |
| Secrets System and Provider Profiles | Atomic credential revisions, reference-use protection, distinct repository/model identity, and no raw-token confinement claim. |
| Omnigent/OpenCode docs | Typed binding dispatch, no-repository/anonymous qualification, existing free-profile identity, and pricing/privacy policy. |
| Quick Start and UI help | Advertise no-key behavior only when the actual supported default journey is qualified. |

The design remains `Proposed` until accepted. Once its behavior is implemented and settled, promote durable architecture into the owning views and supersede the feature design according to the documentation standard. A source-inspection finding alone is not live support evidence.

## 7. Test strategy and acceptance matrix

Use the existing repository test taxonomy. Required CI remains hermetic, using production Activities/adapters, local Git remotes, controlled identity/expiry endpoints, real selected object-store/database boundaries, and controlled clocks. External availability belongs in separately labeled provider qualification.

| Scenario | Required evidence |
| --- | --- |
| Scratch report without PAT/model key | Qualified compute saves a downloadable report without a repository target, resolver call, GitHub probe, or `gh` requirement. |
| Scratch code | Applicable local content/Git exports are durable before host cleanup without a remote. |
| Explicit anonymous public source | Clone and local editing work; ambient PATs, login caches, and host helpers are not used. |
| Inaccessible/missing anonymous source | Actionable uncertainty and typed failure without credential probing or false certainty about a hidden `404`. |
| Explicit B while ambient PAT A exists | Clone, API, CLI, publication, and recovery use B or fail closed. |
| Concurrent identities on one host | No shared configuration, helper, environment, volume, or stale-cleanup crossover. |
| Several PATs for the same actor | Quota handling does not treat them as independent account budgets; anonymous shared-IP throttling is covered too. |
| Multiple routes/concurrent default edits | Exactly one applicable default or explicit selection; conflicting writes fail transactionally. |
| Two connections become one | Strict routing remains unchanged. |
| Rotation during admission/acquisition/retry | Correct revision attribution, old-attempt fencing, and preservation of the active credential when replacement validation fails. |
| Expiring App-like issuance | Same-authority refresh, no scope/actor change, bounded concurrent renewal, and effective revocation. |
| Disable while queued/active | New acquisition and controlled remote operations stop without fallback; copied-raw-token limitations stay explicit. |
| Public read with pending/insufficient token | No false write/private-access verification. |
| Issue-editing Skill with `none` | Declared issue authority is required without inferred final repository publication. |
| Unsupported high-security `auto` | Admission rejects unproven confinement instead of exposing a broad PAT as though routing confined it. |
| Unsafe URL/archive/config/submodule/LFS | Rejection or explicit qualified dependency authority; no SSRF, credential forwarding, path escape, or silent incomplete checkout. |
| Preparation crash | Existing partial directory is not accepted as ready; retry reconciles safely. |
| Failure/cancellation during capture | Required verified outputs or bounded recoverable state remain; cleanup does not destroy the sole copy. |
| Successful save, failed publication | Compute/save evidence stays valid and downloadable; publication-only recovery is available. |
| Private-source restore without original PAT | Self-contained content restores under artifact permission without old credentials or approvals. |
| Scratch into an existing repository | Unrelated files remain by default; conflicts and explicit deletions are visible. |
| Lost push/PR response | Exact remote reconciliation with the same binding, not duplicate mutation. |
| Rename/owner transfer | Rename verifies identity; owner transfer requires policy revalidation. |
| Unauthorized discovery/probe/secret attachment | No cross-scope metadata or secret leak. |
| Leakage across runtime and export boundaries | No issued credential in histories, artifacts, Git config, argv, Docker metadata, logs, or snapshots. |
| Original version-one history | Original meaning/digest remains valid; new writes cannot use old aliases. |

Connect each implementation's tests to the design's stable claim IDs. Add minimized replay fixtures for escaped failures. Keep prerequisite failure classification, required output completeness, and primary-versus-auxiliary outcome handling observable in test assertions.

## 8. Deployment and upgrade qualification

### Clean-install journey

Use a clean checkout with initialized submodules, fresh volumes, no `.env`, and no GitHub/model credentials or login caches. Under controlled eligible-model availability, verify:

1. The documented Compose command starts required services and obtains public runtime images without source credentials.
2. The existing free profile and exact host qualify without a key while explicit privacy/default policy is respected.
3. Normal UI/API defaults submit scratch work without test-only enablement.
4. The agent produces useful output in the authoritative workspace.
5. Required artifacts are uploaded, verified, displayed, and downloadable.
6. The host can be destroyed and the result restored for a new turn without GitHub access.
7. Public Git input works anonymously through the same admission path.
8. Adding a connection enables a separately admitted publication of that saved output without agent rerun.
9. With no eligible model, settings/artifacts remain usable and the actual model/pricing/privacy failure is reported without paid fallback or a PAT requirement.

Run a separate live anonymous-provider smoke for current third-party availability and exact runtime behavior. Do not claim catalog/admission mocks alone prove the product journey. Verify default image acquisition independently of repository auth.

### Runtime and upgrade coverage

Cover the supported runtime × source × access-mode × output/publication matrix for generic OpenCode and currently supported managed/profile-bound/generic Codex and Claude lanes. Reject unqualified combinations before launch rather than infer support from a shared image or capture function.

Extend existing fixtures, including [`test_default_omnigent_launch_authority.py`](../../tests/unit/services/test_default_omnigent_launch_authority.py), [`test_startup_profile_seeding.py`](../../tests/integration/test_startup_profile_seeding.py), and [`test_omnigent_publication_semantics_journey.py`](../../tests/integration/reliability_journey/test_omnigent_publication_semantics_journey.py). Include database upgrades, operator disable/default choices, saved-draft migration, active-run replay, and rollback admission fencing.

## 9. Source traceability and completion

The inputs are the September 4 multi-PAT proposal, its architecture review, and the repository-independent execution proposal. Their consolidated desired-state decisions now live in the design. This plan retains the reviewed baseline and operational material rather than repeating the feature specification.

| Input | Preserved intent and refinement now in the design |
| --- | --- |
| Multi-PAT identity/routing/secret storage | Named existing connections, many-to-many assignments, deterministic selection, typed scope/capabilities, and explicit anonymous access. |
| Multi-PAT execution bindings | Immutable role authority, typed union, policy snapshot, replay safety, and separate revision/issuance. |
| Architecture review | No duplicate domain, honest confinement, safe rotation, evidence versus health, isolated delivery, and simple UX. |
| Source/destination decoupling | Scratch/import/restore/public sources, durable artifacts, deferred publication, existing publish modes, and Lore authority. |
| Anonymous/free compute | Existing profile/materializer identity, catalog/pricing/privacy policy, qualification, and no permanent external-availability promise. |
| Portable results | Safe conditional snapshots/deltas/bundles, runtime-qualified restore, bounded retention, and collision-safe import into existing repositories. |

Provider references retained from the reviewed plan are background for adapter implementation, not proof that a deployment has passed qualification: [GitHub PAT guidance](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens), [organization token approval](https://docs.github.com/en/organizations/managing-programmatic-access-to-your-organization/setting-a-personal-access-token-policy-for-your-organization), [installation authentication](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/authenticating-as-a-github-app-installation), [Git credentials](https://git-scm.com/docs/gitcredentials), [GitHub CLI environment](https://cli.github.com/manual/gh_help_environment), [GitHub rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api), and [OpenCode Zen](https://opencode.ai/docs/zen/). Reverify external constraints when implementing the adapter.

Complete the shared contract, safe multi-PAT routing, credential-independent workspace/results, deferred publication, qualified default UX, and replay-safe retirement without blocking on universal integrations. Completion evidence must show that an expiring authentication adapter can use the same execution/publication consumers. Archive this working plan when that scope and its owning-document reconciliation are complete.

## 10. Slice-0 reconciliation record (issue #4004)

Slice 0 is the contract/ownership reconciliation for the design: a rechecked
consumer inventory with per-site owners, the module-owned contract mapping,
one recovery-guidance reconciliation decision, the supported-combination
matrix with genuine implementations distinguished from gaps, and the work
partition with sibling issues. It enables no new runtime path and claims no
live qualification. Source inspection below is not deployment or
integration-test evidence.

### 10.1 Rechecked baseline and method

- **Rechecked HEAD:** `5da35a2ba` on `main` (2026-09-06). The reviewed
  baseline in the header (`63bce9852ffa33e33cb0b416bc24a654b1b6f92b`) remains
  the historical reference; the tables below were re-verified against the
  rechecked HEAD by repository-wide search for `resolve_github_credential`,
  `resolve_github_token_for_launch`, `github_token`, `GITHUB_TOKEN` /
  `GH_TOKEN`, `compile_repository_target`,
  `materialize_resolved_repository_target`, `persist_repository_connection`,
  `reconcile_default_git_connection`, `decode_legacy_repository_history_v1`,
  `DEFAULT_GIT_CONNECTION_REF`, `savedWorkPolicy`, `savedWorkRef`,
  `accessMode`, and `repositoryAccessSnapshot`.
- **Line numbers are as-of the rechecked HEAD.** Paths and enclosing symbols
  are the stable identifiers; the accompanying conformance test
  (`tests/unit/docs/test_repository_access_slice0_reconciliation.py`) asserts
  path-level completeness, not line numbers.
- **No new-write alias or second connection domain was added by this
  change.** `provider: git | lore`, `RepositoryConnection`,
  `none | branch | pr | auto`, `opencode-zen-free`, and `none@1` are reused
  unchanged.

### 10.2 Rechecked consumer inventory with owners

Authority-input, side-effect-owner, cleanup-owner, and verification
columns use the post-Slice-2 target vocabulary; the "Implementing issue"
column names the single owner of the cutover for that row. Slice-0 (#4004)
owns this inventory and the interface agreement only — not the cutovers.

#### 10.2.1 Global credential resolver call sites

| # | Path (enclosing symbol) | Resolver used | Implementing issue |
| --- | --- | --- | --- |
| R-01 | `moonmind/auth/github_credentials.py` (`resolve_github_credential`, `resolve_github_credential_sync`) | Definition site: global precedence resolver | #2615 replaces execution-bound discovery with bound access; resolver remains only as the compatibility acquisition behind the default connection until retirement |
| R-02 | `moonmind/workflows/executions/repository_contract.py` (`resolve_default_git_credential`, default of `ensure_repository_ready`) | `resolve_github_credential(repo=…)` | #2615 (cut over the default `credential_resolver` to admitted-issuance acquisition; #4004 agrees the interface here) |
| R-03 | `moonmind/workflows/adapters/github_service.py` (`GitHubService.resolve_github_token`, PR/issue/readiness paths) | `resolve_github_credential(explicit_token, repo=…)` | #2615 (bound hosting-API clients; must not duplicate portable Skill semantics) |
| R-04 | `moonmind/workflows/adapters/jules_client.py` (Jules session/PR paths taking `github_token`) | Token argument, resolved upstream via R-03/R-06 | #2615 (Jules bound client) |
| R-05 | `moonmind/workflows/temporal/github_issue_search.py` (issue-search tool) | `github_service.resolve_github_token(repo=…)` | #2615 (tool bound client) |
| R-06 | `moonmind/workflows/temporal/activity_runtime.py` (workspace push-token helper, readiness probes) | `resolve_github_credential(repo=…)` | #2615 (runtime bound delivery) |
| R-07 | `moonmind/workflows/temporal/runtime/managed_api_key_resolve.py` (`resolve_github_token_for_launch` definition; managed launch env shaping) | Definition site + `resolve_github_credential()` | #2615 (non-sensitive access authority; no new-run ambient discovery; independent registry auth) |
| R-08 | `moonmind/workflows/temporal/runtime/managed_session_controller.py` (managed launch, session environment) | `resolve_github_token_for_launch(…)` | #2615 (managed launch cutover) |
| R-09 | `moonmind/workflows/temporal/runtime/launcher.py` (generic launch, env overrides) | `resolve_github_token_for_launch(…)` | #2615 (launcher cutover; also owns new-write producers W-01–W-04 below) |
| R-10 | `moonmind/workflows/temporal/runtime/checkpoint_restore.py` (restore path token) | `resolve_github_token_for_launch()` | #3938 proposed (recovery path must restore under artifact authorization without the original source PAT; confirm in review) |
| R-11 | `moonmind/workflows/temporal/runtime/github_auth_broker.py` (`request_github_token`, local broker env) | Token-broker delivery | #2615 (broker consumes admitted issuance; R-10/R-11 share the acquisition interface agreed here) |
| R-12 | `moonmind/omnigent/profile_bound_execution.py` (Omnigent planning `_github_token`) | `resolve_github_credential(repo=…)` | #2615 (typed authority dispatch in planning/leases/continuation) |
| R-13 | `moonmind/omnigent/workspace_publication.py` (workspace publication) | `resolve_github_credential(repo=…)` | #3938 proposed (destination authority via the existing publisher; confirm in review) |
| R-14 | `moonmind/omnigent/host_services/github_credentials.py` (lease-owned `gh` volume projection) | `resolve_github_credential(repo=…)` | #2615 (bound materialization; preserve ownership/cleanup) |
| R-15 | `moonmind/omnigent/host_services/workspace.py` (fresh-sandbox preparation token) | `resolve_github_token_for_launch()` | #2615 (source union + anonymous execution at the shared boundary) |
| R-16 | `moonmind/agents/codex_worker/worker.py`, `moonmind/agents/codex_worker/handlers.py`, `moonmind/agents/codex_worker/cli.py` (Codex worker launch) | `resolve_github_credential(repo=…)` | #2619 proposed (Codex runtime lane cutover; confirm in review) |
| R-17 | `moonmind/publish/service.py` (publisher remote mutation) | `resolve_github_credential(repo=…)` | #3938 proposed (publisher consumes destination admission; confirm in review) |
| R-18 | `moonmind/workflows/temporal/story_output_tools.py`, `moonmind/workflows/temporal/activities/omnigent_session_activities.py` (story/issue/PR tools, session activities) | `service.resolve_github_token(repo=…)` / coordinator `_github_token` | #2615 (bound tool clients) |
| R-19 | `moonmind/auth/secret_refs.py` (`resolve_github_auth`) | Typed secret-ref auth resolution (not global discovery) | #1090 proposed (Secrets System revision/use-protection surface; confirm in review) |
| R-20 | `.agents/skills/_shared/batch_workflows.py`, `.agents/skills/pr-resolver/bin/pr_resolve_finalize.py`, `.agents/skills/fix-comments/tools/get_pr_comments.py` (portable Skill tooling) | Skill-local token handling outside MoonMind native bindings | Out of scope for native cutover: portable Skills stay runnable outside MoonMind per AGENTS.md; native hosts may only supply execution substrate |

#### 10.2.2 Optional `github_token` arguments

| # | Path (enclosing symbol) | Implementing issue |
| --- | --- | --- |
| G-01 | `moonmind/workflows/adapters/github_service.py` (all public methods taking `github_token: str \| None = None`) | #2615 (replace with bound access clients per CONTRACT-010) |
| G-02 | `moonmind/workflows/adapters/github_client.py` (`GithubClient(token or getenv)`) | #2615 (bound client; remove ambient `GITHUB_TOKEN` fallback) |
| G-03 | `moonmind/workflows/adapters/jules_client.py` (session/PR methods) | #2615 |
| G-04 | `moonmind/workflows/temporal/story_output_tools.py` (`github_token=None` tool parameter) | #2615 |
| G-05 | `moonmind/workflows/temporal/activity_runtime.py` (workspace/push/publish method parameters) | #2615 |
| G-06 | `moonmind/workflows/temporal/activities/omnigent_session_activities.py` (session-activity `github_token` plumbing) | #2615 |
| G-07 | `moonmind/omnigent/execution_ports.py`, `moonmind/omnigent/profile_bound_execution.py` (Omnigent launch ports) | #2615 |
| G-08 | `moonmind/omnigent/workspace_publication.py`, `moonmind/publish/service.py` (publication token parameters) | #3938 proposed |
| G-09 | moonmind/manifest/adapters.py — RETIRED by MoonLadderStudios/MoonMind#4192 (`GithubClient(github_token=token, …)` was fan-out adjacent; bound client) | #2615 (manifest pipeline retired; no live token site) |
| G-10 | `api_service/api/routers/workflow_console_view_model.py`, `api_service/api/routers/settings.py`, `api_service/services/settings_catalog.py`, `api_service/db/models.py` (settings/console token fields) | #1090 proposed (Settings wizard, secret attachment, scoped discovery; confirm in review) |
| G-11 | `moonmind/omnigent/oauth_host_runtime.py` (`start_session`/`_launch_daemon` `github_token: str \| None = None`, `GH_TOKEN` child-env injection) | #2615 (Omnigent host lane: replace explicit-token/env delivery with admitted-issuance acquisition; shares the R-10/R-11 acquisition interface) |

#### 10.2.3 Token-environment readers

| # | Path | Read | Implementing issue |
| --- | --- | --- | --- |
| E-01 | `moonmind/auth/github_credentials.py`, `moonmind/auth/env_shaping.py`, `moonmind/config/settings.py` | Canonical `GITHUB_TOKEN` env catalogue and shaping | #2615 (reads move to bootstrap/acquisition; never routing) |
| E-02 | `moonmind/workflows/temporal/runtime/managed_api_key_resolve.py` (launch/shaped environment `GITHUB_TOKEN`) | Launch env shaping | #2615 |
| E-03 | `moonmind/workflows/temporal/runtime/managed_session_controller.py`, `moonmind/workflows/temporal/runtime/launcher.py`, `moonmind/workflows/temporal/runtime/git_auth.py`, `moonmind/workflows/temporal/runtime/github_auth_broker.py` (session/broker env) | Session/broker delivery env | #2615 |
| E-04 | `moonmind/workflows/temporal/activity_runtime.py` (ambient read + workspace command env + scrub) | Ambient read, injection, and scrubbing | #2615 (scrub/isolate per INV-004; reads only from admitted adapter material) |
| E-05 | `moonmind/workflows/temporal/worker_runtime.py` (`GH_TOKEN`/`GITHUB_TOKEN` fallback) | Worker ambient fallback | #2615 (remove fallback; fail closed) |
| E-06 | `moonmind/workflows/adapters/managed_agent_adapter.py` (`_SECRET_ENV_PASSTHROUGH_KEYS = ("GITHUB_TOKEN",)`) | Secret env passthrough | #2615 (passthrough only admitted material) |
| E-07 | `moonmind/schemas/managed_session_models.py`, `moonmind/schemas/agent_runtime_models.py` (session schema token fields) | Schema-carried token refs | #2615 (non-sensitive authority refs; secret-carrying types stay in trusted boundaries) |
| E-08 | `moonmind/agents/codex_worker/worker.py`, `moonmind/agents/codex_worker/handlers.py`, `moonmind/agents/codex_worker/cli.py`, `moonmind/utils/logging.py` (worker env, log redaction) | Worker env + redaction | #2619 proposed for worker env; redaction stays (INV-004) |
| E-09 | `moonmind/omnigent/oauth_host_runtime.py` (`_launch_daemon` injects `GH_TOKEN` child env from explicit `github_token`) | Explicit-token environment delivery | #2615 (delivery only from admitted issuance; never ambient discovery; shares the R-10/R-11 acquisition interface) |

#### 10.2.4 New-write producers and retained history readers

| # | Path (enclosing symbol) | Role | Implementing issue |
| --- | --- | --- | --- |
| W-01 | `moonmind/workflows/executions/execution_contract.py` (authoring compile + legacy branch) | New-write `compile_repository_target`; frozen legacy decode branch | #2615 (canonical aliases, role capability compilation); legacy branch frozen per §10.6 |
| W-02 | `moonmind/workflows/temporal/runtime/launcher.py` (`reconcile_default_git_connection`, `persist_repository_connection`, `compile_repository_target`, `materialize_resolved_repository_target`) | Default-connection reconcile + persist + compile + materialize | #2615 |
| W-03 | `api_service/api/routers/executions.py` (submission-time `compile_repository_target`) | API admission compile | #2615 (optional source + explicit access intent at admission) |
| W-04 | `moonmind/workflows/temporal/runtime/lore_repository_adapter.py` (`materialize_resolved_repository_target`) | Lore materialization | #2615 (unchanged Git/Lore authority distinction) |
| H-01 | `moonmind/workflows/executions/execution_contract.py` (`decode_legacy_repository_history_v1` call sites) | Frozen history reader | #4004 agrees; #2615 preserves (original digest behavior, §10.6) |
| H-02 | `moonmind/workflows/temporal/runtime/launcher.py` (`DEFAULT_GIT_CONNECTION_REF` branch) | Compatibility identity for effective legacy source | #2615 (single synthetic default; no second default) |

#### 10.2.5 Remaining surfaces swept (no global resolution found; owners for design additions)

| Surface | Representative paths | Design addition owner |
| --- | --- | --- |
| UI/API create/edit/rerun/presets/schedules | `frontend/src/`, `api_service/api/routers/executions.py`, `api_service/services/omnigent_agent_bootstrap_service.py`, `moonmind/workflows/executions/preset_expansion.py`, `preset_goal_scheduler.py`, `moonmind/omnigent/workspace_intent.py` (single intent compiler for all five surfaces) | #3940 proposed (normal create/detail/results UX, no-key onboarding; confirm in review) |
| Repository discovery/indexing | Settings discovery via `api_service/api/routers/settings.py`; public-URL entry requires no authenticated discovery (DOC-REQ-004) | #1090 proposed (scoped discovery/probes; confirm in review) |
| Fan-out | `moonmind/workflows/temporal/workflows/run.py`, `service.py`, moonmind/manifest/pipeline.py — RETIRED by MoonLadderStudios/MoonMind#4192, `moonmind/services/skill_resolution.py` | #2615 (children resolve own authority or inherit a verified compatible binding, never a raw PAT) |
| Attestation | `moonmind/workflows/temporal/agent_result_payloads.py`, `activities/omnigent_activities.py`, `workflows/omnigent_session.py`, `workflows/container_job.py`, `moonmind/workloads/docker_launcher.py` | #2615 (attestation/cleanup dispatch by authority kind; existing boundaries retained) |
| Registry pulls | `moonmind/omnigent/bootstrap/image_resolution.py`, `provider_revalidation.py`, `harness_platform/support.py` | #2615 (independent registry auth; source PAT never a GHCR fallback) |
| Artifact/checkpoint | `moonmind/schemas/managed_checkpoint_models.py`, `moonmind/workflows/temporal/checkpoint_policy.py`, `moonmind/omnigent/authority_chain.py`, `moonmind/omnigent/checkpoints.py` | #3938 proposed (saved-work manifest, capture/restore, save-before-cleanup; confirm in review) |
| Bootstrap env shaping | `api_service/main.py` (preferred `GITHUB_TOKEN`/`GITHUB_PAT` env selection into the secret store at startup) | #2615 (reads belong to bootstrap/acquisition, never routing; no new-run ambient discovery) |

### 10.3 Module-owned contract mapping, wire versions, and type owners

No `SourceControlConnection` and no `RepositoryTargetV2` are introduced.
New semantics land in the owning modules below; the design's YAML shapes
remain illustrative authoring semantics, with owner-defined fixtures in
`docs/tmp/repository-access-slice0-fixtures.yaml`.

| Design addition | Owning module (type owner) | Wire version / linkage |
| --- | --- | --- |
| `workspaceSource` union (`scratch \| repository \| artifact \| checkpoint \| existing_workspace`) (`DOC-REQ-002`, `CONTRACT-002`) | `moonmind/omnigent/workspace_intent.py` + `moonmind/schemas/workspace_intent.py` (`WorkspaceIntentRecord`; single compiler for create/edit/rerun/schedule/preset surfaces) | Extend `WorkspaceIntentRecord` schema version (`WORKSPACE_INTENT_SCHEMA_VERSION`); top-level `repository` present only for the `repository` kind (sibling of `workspaceSource`, per the design §3 shapes); publication destination stays a separate role, not a second source alias |
| `accessMode` (`anonymous \| routed \| explicit`) (`CONTRACT-003`) | `moonmind/workflows/executions/repository_contract.py` | New field on the authored target at the next owned contract revision; `anonymous` forbids `connectionRef` and creates no dummy connection; omitted values compile to documented-UI-default intent without inspecting token presence |
| Typed acquisition variants (`CONTRACT-006`) | `moonmind/auth/github_credentials.py` (compatibility acquisition) + `moonmind/workflows/temporal/runtime/managed_api_key_resolve.py` + `moonmind/workflows/temporal/runtime/github_auth_broker.py` + `moonmind/omnigent/oauth_host_runtime.py` (host-lane delivery, G-11/E-09) | Discriminated PAT / App-installation / Lore configuration; expiring issuance refreshes same-authority scope; consumers use returned scope/expiry, never hardcoded lifetimes |
| Capability bundles + role-specific compilation | `moonmind/workflows/executions/repository_contract.py` (`derive_repository_capabilities`, `ensure_repository_ready`) with `moonmind/omnigent/effective_capabilities.py` as consumer | Capability tokens stay strings; readiness registry remains fail-closed; friendly/indexing/readiness/publish/full-PR profiles are versioned bundle presets, not a privilege ladder |
| Role snapshots (`repositoryAccessSnapshotRef`) | `moonmind/workflows/executions/repository_contract.py` (immutable snapshot type; `RepositoryAuthorityBinding` union) | Snapshot carries endpoint/repository, role, admitted operations, policy/binding revision, selection origin, principal/workspace scope; existing binding-set version/digest infrastructure supplies immutable linkage |
| `outputPolicy` (small format profile, not booleans) | `moonmind/omnigent/workspace_intent.py` (`savedWorkPolicy` today) + artifact/checkpoint owners (§10.2.5) | `savedWorkPolicy` string evolves into the owned profile enum; required-format failure blocks save finalization |
| Saved-work manifests | `moonmind/schemas/managed_checkpoint_models.py`, `moonmind/workflows/temporal/checkpoint_policy.py`, `moonmind/omnigent/authority_chain.py` | Manifest is a compact index (schema version/digest, source identity, content digest, completeness, ownership/retention); reuse suitable checkpoint archives, never duplicate |
| Publication-only requests (`savedWorkRef` + destination admission) | `moonmind/publish/service.py` (existing publisher; sole publication engine) | `savedWorkRef` is an immutable digest reference; destination target/connection/operations/client-policy snapshot newly admitted; no rerun by default |
| `provider: git \| lore`, `RepositoryConnection`, `none \| branch \| pr \| auto` | Unchanged owners (`repository_contract.py`, `publish/service.py`, `WorkflowPublishing.md`) | `moonmind.repository-connection.v1`, `moonmind.resolved-repository-target.v1`, `moonmind.repository-legacy-history.v1` stay; changed contracts version at their owning boundary. `auto` is the existing Skill-owned mode (`WorkflowPublishing.md:3-42`) for `pr-resolver`, `fix-comments`, `fix-ci`, `fix-merge-conflicts`; see the §10.5 `auto` rows for its capability-qualified combinations |
| Generated types | Each owning module generates/consumes its own types; no cross-module generated-type owner is introduced | Frontend/API projections consume authoritative records; UI text never replaces primary evidence |

Schema semantics without fake values (acceptance): `scratch` carries no
top-level repository keys, no credential binding, and no resolver call;
`anonymous` carries a top-level repository target (sibling of
`workspaceSource`) with `accessMode: anonymous`, no `connectionRef`,
and no credential materializer; routed/explicit carry admitted connection
authority, never raw tokens. The fixtures file records these as validated
owner-defined examples.

### 10.4 Recovery-guidance reconciliation (reviewed docs decision)

**Decision (Slice-0, review-required):** AGENTS.md's resilience rule —
"preserve the same authoritative workspace and immutable inputs, perform
bounded continuation or retry, and publish a remotely verified recovery
checkpoint before cleanup when retries exhaust" — remains mandatory and is
not relaxed by this change. The design's artifact-backed durability handoff
(`INV-007`: quiesced workspace → approved captured snapshot → verified
required artifact objects → committed manifest/checkpoint references →
recorded save outcome → optional admitted publication → cleanup release)
remains **Proposed** until accepted through repository review, exactly as the
design's Section 1 and the plan's Section 6 already state.

Reconciliation of the two, recorded here so no runtime path can claim
otherwise:

1. Verified artifact storage is the credentialless durability handoff for
   save-only finalization; it satisfies "verified durable evidence before
   cleanup" without requiring a GitHub identity.
2. The remote-checkpoint branch (pushing recovery state to a remote) still
   requires an already-admitted destination and mutation authority; missing
   GitHub credentials are never a reason to skip saving, and never permission
   to acquire an unrequested remote identity.
3. Failed/cancelled compute keeps its primary outcome while capture is
   attempted; save failure retains the authoritative workspace under bounded
   recovery and ordinary cleanup must not destroy the sole copy.
4. No code in this change enables, bypasses, or weakens any recovery gate;
   the design stays `Status: Proposed` and migration steps stay under
   `docs/tmp/`.

### 10.5 Supported-combination matrix and capture/restore owners

The matrix records genuinely existing implementations versus gaps as of the
rechecked HEAD. "Genuine" means the path exists in code today; it is still
source inspection, not live qualification evidence. Unlisted combinations
are unsupported and must be rejected before execution (admission), never
downgraded silently.

| Runtime | Source | Access | Output/publication | Status and evidence owner |
| --- | --- | --- | --- | --- |
| Managed Docker | `repository` (git) | explicit/default compat identity | `none` / `branch` / `pr` | Genuine (launcher, managed controller, publisher). Owners: #2615 launch, #3938 publication |
| Managed Docker | `scratch` | n/a (no repository) | saved work, `none` | Gap: scratch execution path not yet cut over (workspace prep requires GitHub source). Owner: #2615 |
| Managed Docker | `artifact` / `checkpoint` import/restore | artifact authorization | saved work | Partial: checkpoint models/policy exist; authorized import + digest/size/traversal bounds are gaps. Owner: #3938 proposed |
| Managed Docker | `repository` (public) | `anonymous` | saved work, `none` | Gap: no anonymous access mode; ambient-credential isolation (INV-004) not proven. Owner: #2615 |
| Omnigent generic host | `repository` (git) | explicit/default compat identity | `none` / `branch` / `pr` | Genuine (host services workspace + github_credentials, generic_host realizer). Owners: #2615 launch, #3938 publication |
| Omnigent generic host | `scratch` | n/a | saved work, `none` | Gap (same prep prerequisite). Owner: #2615 |
| Omnigent generic host | `existing_workspace` locator | ownership grant | policy-approved outputs | Partial: locator + `assert_no_runtime_shortcut_keys` exist; grant/capability qualification is a gap. Owner: #2615 |
| Codex worker lane | `repository` (git) | explicit/default compat identity | `none` / `branch` / `pr` | Genuine via worker resolver call (R-16). Owner: #2619 proposed |
| Codex worker lane | `scratch` / `anonymous` | n/a / `anonymous` | saved work | Gap. Owner: #2619 proposed |
| Claude/managed profile-bound lanes | `repository` | explicit/default compat identity | `none` / `branch` / `pr` | Genuine where profile-bound execution resolves today; bound-authority cutover is a gap. Owners: #2615, #2619 proposed |
| Managed Docker | `repository` (git) | explicit/default compat identity | `auto` (Skill-owned: `pr-resolver`, `fix-comments`, `fix-ci`, `fix-merge-conflicts`; terminal evidence validated per `WorkflowPublishing.md:3-42`) | Genuine (launcher, managed controller, existing publisher validation of `artifacts/publish_result.json`). Owners: #2615 launch, #3938 publication |
| Omnigent generic host | `repository` (git) | explicit/default compat identity | `auto` (Skill-owned; same skill set and evidence contract) | Genuine (host services workspace + github_credentials, generic_host realizer, existing evidence validation). Owners: #2615 launch, #3938 publication |
| Codex worker lane | `repository` (git) | explicit/default compat identity | `auto` (Skill-owned) | Genuine where the worker lane resolves today (R-16); bound-authority cutover is a gap. Owner: #2619 proposed |
| Claude/managed profile-bound lanes | `repository` | explicit/default compat identity | `auto` (Skill-owned) | Partial: profile-bound execution resolves today; Skill-owned evidence validation exists but bound-authority cutover is a gap. Owners: #2615, #2619 proposed |
| Any runtime | any | any | publication-only (`savedWorkRef`) | Gap: no `savedWorkRef` admission; publisher has no destination re-admission. Owner: #3938 proposed |
| Any runtime | `repository` (lore) | explicit | `none` / `branch` / `pr` | Partial: Lore target/adapter/authority exist; normalized-connection routing + capability evidence are gaps. Owner: #2615 |
| Free route | `scratch` / public `repository` | n/a / `anonymous` | saved work, `none` | Partial: `opencode-zen-free` + `none@1` identity seeded; pricing/privacy qualification + no-key journey unproven. Owner: #3940 proposed |

Capture/restore/session-reattach owners: capture and saved-work
finalization with the artifact/checkpoint owners; workspace restore is
separate from provider-session reattachment (`_partition_restore_refs` keeps
`artifact://` inputs distinct from `external-state:` refs) — restore owner
#3938 proposed, session-reattach owner with the Omnigent session lane
(#2615). No runtime qualification is claimed by this documentation task.

### 10.6 Work partition, compatibility, digests, gates, rejections

- **#4004 (this issue):** Slice-0 reconciliation only — this inventory,
  the §10.3 interface agreement, the §10.4 recovery decision, the §10.5
  matrix, fixtures, and claim/link checks. No runtime behavior change.
- **#2615:** Source/admission implementation — bound access and execution
  cutover (slices 2–3): typed bindings, immutable snapshots, bound
  transport/API clients, expiring acquisition interface, isolated delivery,
  independent registry auth, workspace source decoupling, fan-out authority,
  attestation/cleanup dispatch. All R/G/E rows marked #2615. Owns the
  `TEST-002` credential-isolation conformance (explicit-B-wins, concurrency,
  rotation/refresh/revocation) across the cut-over consumers.
- **#1090 (proposed, confirm in review):** Connection persistence and
  lifecycle (slice 1): `RepositoryConnection` extension, normalized routing,
  scope/secret-use authorization, atomic revision lifecycle, non-mutating
  probes, Settings wizard, scoped discovery. Rows R-19, G-10, discovery.
- **#2619 (proposed, confirm in review):** Runtime-lane cutovers beyond
  generic managed/Omnigent (Codex/Claude/profile-bound lanes, worker env).
  Rows R-16, E-08, E-09 (OAuth host lane), matrix Codex/Claude/`auto` rows.
- **#3938 (proposed, confirm in review):** Saved-work finalization and
  Publish Saved Work (slices 4–5): manifests, exports, failure/cancellation
  capture, retention/restore, save-before-cleanup, destination admission,
  exact remote verification, publication-only recovery. Rows R-10, R-13,
  R-17, G-08, G-11/E-09 (OAuth host delivery), artifact/checkpoint surface.
  Owns the `TEST-003` saved-work survival conformance (host/credential loss,
  crash/cancel capture, deferred-publication recovery).
- **#3940 (proposed, confirm in review):** Default UX and retirement
  (slice 6): free-model pricing/privacy qualification, exact host and public
  image pulls, create/detail/results UX, no-key onboarding, superseded-path
  removal. UI/API surface, free-route matrix row.
- Epic #4003 tracks the cohort; focused epic issues take App/restore,
  retention, UX, migration, and qualification slices not owned above. No
  42-claim slice is silently dropped (§10.7).

Compatibility readers (explicit, owned by #2615 implementation):
`decode_legacy_repository_history_v1` stays the frozen reader for
already-recorded histories and is never called for authoring;
`repository-connection:git-default` stays the single compatibility identity
for an explicitly bound effective legacy source, never a live chain of
guessed credentials; historical parsing/digest rules freeze under
`moonmind.repository-legacy-history.v1` while new writes use the one
canonical contract and reject historical aliases.

Original digest behavior: recorded workflows keep compatible deterministic
decisions and worker support; binding-set version/digest infrastructure
supplies immutable linkage for new snapshots; versioned execution never
falls back to a singleton resolver.

Release admission gates: no new public submission path is enabled until all
supported consumers enforce its bindings; internal staging uses explicit
version gates, never connection-count heuristics; the default always-on
container footprint stays unchanged; no universal-provider or
general-credential-service dependency gates the initial overhaul.

Unsupported combinations reject: unqualified runtime × source × access ×
output lanes fail admission before external access; conflicting new-write
aliases are rejected before external access; high-security `auto` without a
qualified mediated path is rejected rather than exposing a broad PAT;
missing/ambiguous routed authority, unsafe URLs/archives/configs, and
unadmitted publication are actionable failures, never silent downgrades or
substitutions.

### 10.7 Fixtures, claim/link checks, and review decision

- Fixtures: `docs/tmp/repository-access-slice0-fixtures.yaml` — validated
  owner-defined examples for `scratch`, explicit-`anonymous` public read,
  and routed-default authoring, with schema semantics per §10.3 (no
  repository/credential keys on scratch; no `connectionRef`/credential on
  anonymous; no raw tokens anywhere).
- Claim coverage: all 42 stable design claims resolve to §1
  traceability rows plus the §10.3/§10.5/§10.6 owning records above:
  `CONTRACT-001`, `CONTRACT-002`, `CONTRACT-003`, `CONTRACT-004`,
  `CONTRACT-005`, `CONTRACT-006`, `CONTRACT-007`, `CONTRACT-008`,
  `CONTRACT-009`, `CONTRACT-010`, `CONTRACT-011`, `CONTRACT-012`,
  `CONTRACT-013`, `CONTRACT-014`, `DOC-REQ-001`, `DOC-REQ-002`,
  `DOC-REQ-003`, `DOC-REQ-004`, `DOC-REQ-005`, `INV-001`, `INV-002`,
  `INV-003`, `INV-004`, `INV-005`, `INV-006`, `INV-007`, `INV-008`,
  `INV-009`, `NON-GOAL-001`, `QUALITY-001`, `QUALITY-002`, `QUALITY-003`,
  `QUALITY-004`, `QUALITY-005`, `QUALITY-006`, `QUALITY-007`, `QUALITY-008`,
  `QUALITY-009`, `TEST-001`, `TEST-002`, `TEST-003`, `TEST-004`. The
  six acceptance checkboxes map to §10.2 (ownership), §10.7 (claims),
  fixtures (schema semantics), §10.4 (recovery reconciliation), §10.5
  (honest matrix), and this section (fixtures/checks/baseline/decision).
- Link checks: design ↔ plan cross-links verified (design points to this
  plan; plan header points to the design); owning-module paths in §10.2–§10.3
  verified to exist at the rechecked HEAD by the conformance test.
- Exact source baseline: `5da35a2ba` (main, 2026-09-06); historical reviewed
  baseline `63bce9852ffa33e33cb0b416bc24a654b1b6f92b` preserved in the header.
- Review decision: design remains `Status: Proposed`. Acceptance of the
  target behavior happens through repository review of a future change, not
  by merging this reconciliation. No runtime qualification claimed.

(End of Slice-0 record.)
