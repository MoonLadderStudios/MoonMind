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
| Optional repository access and responsibility separation | Design Sections 1–2, `DOC-REQ-001`, `CONTRACT-001`, `INV-001` |
| Source union, canonical authoring, access intent, and capability compilation | Section 3, `CONTRACT-002` through `CONTRACT-004`, `INV-002` |
| Connection persistence, scope, routing, authentication variants, and validation | Section 4, `CONTRACT-005` through `CONTRACT-007`, `QUALITY-001` |
| Typed execution bindings, rotation, refresh, revocation, and throttling | Section 5, `CONTRACT-008`, `CONTRACT-009`, `INV-003`, `QUALITY-002` |
| Bound consumers, isolation, confinement, and source safety | Section 6, `CONTRACT-010`, `INV-004`, `INV-005`, `QUALITY-003` |
| Workspace preparation and restore | Section 7, `CONTRACT-011`, `INV-006` |
| Saved-work formats, finalization, ownership, and retention | Section 8, `CONTRACT-012`, `INV-007`, `QUALITY-004`, `QUALITY-005` |
| Deferred publication and recovery | Section 9, `CONTRACT-013`, `INV-008`, `QUALITY-006` |
| Historical interpretation and observability | Section 10, `CONTRACT-014`, `QUALITY-007` |
| Free-model qualification and product UX | Sections 11–12, `DOC-REQ-003` through `DOC-REQ-005`, `INV-009`, `QUALITY-008` |
| Conformance and scope | Sections 13–14, `TEST-001` through `TEST-004`, `NON-GOAL-001`, `QUALITY-009` |

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

## 10. Slice 0 rechecked baseline (MoonLadderStudios/MoonMind#4004)

Historical reviewed baseline: `63bce9852ffa33e33cb0b416bc24a654b1b6f92b` (2026-09-04, from #3989). That baseline is explicitly historical and inspection-only; it is not reasserted as current.

Rechecked baseline for this issue: `5da35a2ba7ad80a8778ce766445184b134631fcb` (HEAD at the time of the Slice 0 recheck, 2026-09-06). Method is repository-wide source inspection only — the same evidentiary limit as the September 4 plan. No deployment, integration-test, or live-provider support is claimed by this record. Downstream slices must recheck again before cutover.

Corrections to the September 4 starting inventory found during the recheck:

- `provider_leases.py` lives at `moonmind/omnigent/provider_leases.py` (with `moonmind/omnigent/host_leases.py` beside it), not under `moonmind/omnigent/harness_platform/`. The plan Section 4 row naming `harness_platform/` plus `provider_leases.py` conflates two real locations; the Slice 0 table below records the exact paths.
- The generic clone path observation stands: the shared clone helper uses a clean URL with stdin-delivered credentials. Remaining compatibility paths still read ambient token state and are listed per consumer below rather than assumed clean.
- `resolve_github_token_for_launch` (launch-boundary resolver in `moonmind/workflows/temporal/runtime/managed_api_key_resolve.py`) is a distinct global resolver from `resolve_github_credential` (canonical precedence resolver in `moonmind/auth/github_credentials.py`) and from `resolve_default_git_credential` (thin repository-contract wrapper). All three must be cut over; auditing only one is incomplete.

## 11. Slice 0 consumer inventory (rechecked 2026-09-06, inspection only)

Each row names one implementation owner (slice and/or related issue), the authority input it must consume after cutover, the side-effect owner, the cleanup owner, and the verification obligation. "Today" describes the rechecked HEAD; "target" describes the design's desired state. No row is claimed as already cut over.

| # | Boundary / consumer | Exact path today | Signal today | Implementing owner | Authority input (target) | Side-effect owner | Cleanup owner | Verification obligation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| C-01 | Canonical GitHub precedence resolver | `moonmind/auth/github_credentials.py` L74 `resolve_github_credential`, L54-L63 token/secret-ref env lists (`GITHUB_TOKEN`, `GH_TOKEN`, `WORKFLOW_GITHUB_TOKEN`, `*_SECRET_REF`, `MOONMIND_GITHUB_TOKEN_REF`) | Global precedence; `repo` is advisory only | Slice 2 + epic #4003 focused auth slice (acquisition adapter surface; not this issue) | Per-role admitted binding (connection, operations, policy revision) | N/A (acquisition only) | Issuance/materializer generation fencing (Slice 2) | Slice 2 expiring-issuance conformance: scope-preserving refresh, bounded renewal, revocation through the same boundary |
| C-02 | Repository contract wrapper | `moonmind/workflows/executions/repository_contract.py` L599 `resolve_default_git_credential` | Delegates to C-01 for the default connection | Slice 2 (#4004 records; #2615 owns source/admission behavior, not duplicated here) | `RepositoryConnection` + immutable access snapshot | Pre-mutation readiness gate (`ensure_repository_ready`) | N/A | `test_repository_contract.py` readiness-before-mutation + Slice 2 PAT A/B isolation |
| C-03 | Launch-boundary resolver | `moonmind/workflows/temporal/runtime/managed_api_key_resolve.py` L252 `resolve_github_token_for_launch`, L72 managed-store slug fallback, L238 login probe | Launch env + managed store + legacy `GITHUB_TOKEN` env | Slice 2 (managed launch) | Non-sensitive access authority from the admitted plan; no new-run ambient discovery | Launcher / session controller | Attempt-scoped delivery objects | Launcher tests prove no ambient fallback; unsupported lanes fail admission |
| C-04 | Managed launcher | `moonmind/workflows/temporal/runtime/launcher.py` L73 import, L2160 + L2450 call sites, L1125/L1568/L1642 env reads, L2159-L2172 git-auth env build | Ambient launch token + `build_github_token_git_environment` | Slice 2 (managed launch) | Admitted repository authority + independent registry auth | Managed runtime (clone/push) | Runtime attempt cleanup | PAT A/B isolation; registry auth independent of source PAT |
| C-05 | Managed session controller | `moonmind/workflows/temporal/runtime/managed_session_controller.py` L62 import, L1076/L1442 call sites, L1109 `GITHUB_TOKEN` env projection, L2504 env pop | Session env carries/denies `GITHUB_TOKEN` | Slice 2 (managed launch) | Same as C-04 | Session runtime | Session-scoped cleanup | Controller tests: disable-while-queued/active stops acquisition without fallback |
| C-06 | Checkpoint restore path | `moonmind/workflows/temporal/runtime/checkpoint_restore.py` L28 import, L519 call site | Restore acquires launch token | Slice 4 (saved-work finalization) | Artifact authorization + digest; no source PAT for self-contained restore | Checkpoint/capture subsystem | Bounded recoverable state, not ordinary cleanup of the sole copy | Restore-without-PAT journey; host-removal restore |
| C-07 | Hosting API: GitHubService | `moonmind/workflows/adapters/github_service.py` L226-L228 + L548-L550 `resolve_github_credential` passthrough, L546 optional `github_token` arg, L184-L205 env diagnostics | Optional PAT arg + global discovery | Slice 2 (hosting API consumers; portable Skill semantics unchanged) | Bound access client for the admitted role | Publisher / provider adapter | Attempt-scoped | Bound-client tests; no per-call PAT plumbing after cutover |
| C-08 | Hosting API: Jules client | `moonmind/workflows/adapters/jules_client.py` L390-L438 optional `github_token` args | Optional PAT args threaded through Jules calls | Slice 2 (hosting API consumers) | Bound access client / provider-owned handoff | Jules provider session | Provider session lifecycle | Adapter tests: token only from admitted binding; provider handoff unchanged |
| C-09 | Story/issue/PR tools | `moonmind/workflows/temporal/story_output_tools.py` L446/L2292/L4441/L4721/L5493 `resolve_github_token` via GitHubService; L3532 `github_token=None` default | Global token via service | Slice 2 (hosting API consumers) | Declared issue/PR authority for the requested operation | Owning Skill (agent-owned) or publisher (managed) | Attempt-scoped | Issue-editing Skill with `publish.mode=none` still requires its declared issue authority (plan §7 row) |
| C-10 | Publisher (managed) | `moonmind/publish/service.py` L162 optional `github_token`, L265 env build, L269-L275 + L400-L414 resolver calls + `GITHUB_TOKEN`/`GH_TOKEN` injection | Global resolver + env injection | Slice 5 (Publish Saved Work; same publisher, no second engine) | Newly admitted destination authority (`savedWorkRef` + destination + operations) | Existing publisher | Candidate/attempt-scoped | Lost push/PR-response reconciliation; immutable original artifact |
| C-11 | Omnigent workspace preparation | `moonmind/omnigent/host_services/workspace.py` L325 import, L328 call site | Fresh sandboxes require GitHub source/branch/token | Slice 3 (workspace source decoupling) | Workspace source union (`scratch`/`repository`/`artifact`/`checkpoint`/`existing_workspace`) + explicit `accessMode` | Workspace materializer | Ownership handoff + quotas + cleanup authority | Real preparation without resolver calls, hidden login state, or half-prepared directories |
| C-12 | Omnigent CLI materialization | `moonmind/omnigent/host_services/github_credentials.py` L9 import, L87 call site | Global credentials projected into lease-owned `gh` volume | Slice 2 (bound delivery) | Admitted binding consumed by lease-owned materializer | Lease-owned materializer | Generation-fenced cleanup | Concurrency isolation: no shared config/helper/env/volume crossover |
| C-13 | Omnigent publication/recovery | `moonmind/omnigent/workspace_publication.py` L14 import, L317 resolver call, L127/L155-L326 optional `github_token` + env build | Global resolver + optional PAT | Slice 5 (publication-only recovery) | Destination authority + saved-work digest | Existing publisher | Attempt-scoped | Publication-only recovery neither reruns the agent nor mutates the original |
| C-14 | Omnigent planning/acquisition | `moonmind/omnigent/profile_bound_execution.py` L2930 `_github_token`, L2942-L2945 resolver call, L1241/L1269/L1732 threading; `moonmind/omnigent/provider_leases.py`; `moonmind/omnigent/harness_platform/credential_bindings.py`; `moonmind/omnigent/credential_materializers.py` | Provider-profile-shaped authority; repository token threaded as string | Slice 2 (typed authority dispatch) | Typed `RepositoryAuthorityBinding` union dispatched by authority kind | Runtime delivery | Binding/lease cleanup | Rotation/refresh/revocation conformance without copying model capacity rules |
| C-15 | Codex worker | `moonmind/agents/codex_worker/worker.py` L10617 `_resolve_github_token`, L10640-L10642 resolver call, L10579/L10594 call sites, L5774-L5807 publish env, L10682-L10683 env injection | Global resolver + `GITHUB_TOKEN`/`GH_TOKEN` env | Slice 2 (managed/Codex launch) | Admitted binding | Worker runtime | Attempt-scoped | Conformance: explicit connection B wins over ambient PAT A across clone/API/CLI/publication/recovery or fails closed |
| C-16 | Activity runtime (token env fan-out) | `moonmind/workflows/temporal/activity_runtime.py` (41 `github_token` references including env shaping/injection) + `moonmind/workflows/temporal/runtime/github_auth_broker.py` L283 token command, L414-L440 broker delivery; `moonmind/workflows/temporal/runtime/git_auth.py` env builder | Broad token-env plumbing | Slice 2 (isolation) | Bound access clients; scrubbed ambient identity (`INV-004`) | Owning activity | Generation-fenced | Leakage tests: no credential in histories, artifacts, argv, Docker metadata, logs, snapshots |
| C-17 | Bootstrap/settings/secrets | `api_service/main.py` L176-L205 `GITHUB_TOKEN` seeding; `api_service/services/omnigent_agent_bootstrap_service.py`; `api_service/services/secrets.py` + `api_service/api/routers/settings.py` + `api_service/db/models.py` (Slice 0 §4 row) | Startup seeding + settings/secret persistence | Slice 1 (connection persistence/lifecycle) + Slice 6 (bootstrap/UX) | Scoped discovery/probes; env reads belong to bootstrap/acquisition, not routing | Settings wizard (explicit admin action) | Revision lifecycle + deletion protection | Transactional default/rotation tests; no-key onboarding only with qualified journey evidence |
| C-18 | UI/API authoring surfaces | `frontend/src/entrypoints/workflow-start.tsx`, `workflow-detail.tsx`, `schedules.tsx`, `workflow-list.tsx`; `frontend/src/components/settings/GithubTokenProbePanel.tsx`; create/edit/rerun/preset/schedule paths via `moonmind/omnigent/workspace_intent.py` canonical readers | Authoring converges on `AgentExecutionRequest` → `WorkspaceIntentRecord`; UI still presents repository/PAT prerequisites | Slice 6 (default UX) + Slice 0 compiler ownership (this record) | Optional source + explicit access intent + canonical aliases + role capability compilation | N/A at authoring | Draft migration (explicit; auto-inserted defaults never become false explicit choices) | Clean-install journey; operator-choice preservation; no fake repository/credential values |
| C-19 | Discovery/indexing/attestation/registry | Repository discovery + GitHub probes (via C-07/C-09 clients); registry pulls via image acquisition (independent of source PAT); attestation via managed/Omnigent execution boundaries; artifact/checkpoint paths via `moonmind/schemas/managed_checkpoint_models.py`, `moonmind/workflows/temporal/checkpoint_policy.py`, `moonmind/omnigent/authority_chain.py` | Discovery/probes can touch global auth; registry/source separation not yet enforced at every call site | Slices 1-4 (discovery scope control; registry stays in image acquisition; attestation stays at execution boundary) | Connection the principal may use (discovery); deployment registry authority (pulls); artifact/checkpoint authorization (restore) | Owning subsystem per row | Owning subsystem | Unauthorized discovery leaks neither metadata nor secrets; public image pull independent of source credentials |

CI guard (Slice 0 requirement, owned by Slice 2): add a targeted unit guard against reintroducing global resolution into execution-bound consumers. This issue records the complete call-site list above so that guard has an explicit inventory to enforce; the guard itself lands with the Slice 2 cutover rather than as an unenforced pre-cutover block on current behavior.

## 12. Claim-to-tracked-work resolution (all 42 stable claims)

"Tracked work" means the owning slice and/or related issue that must implement or accept the claim. This Slice 0 record is the traceability artifact; it does not claim any row below as implemented.

| Claim | Design section | Owning module / doc (target) | Implementing work |
| --- | --- | --- | --- |
| `DOC-REQ-001` | §1 repository-independent work | Execution/workspace-intent contracts + artifact/checkpoint systems | Slice 0 (this record) + Slices 3-4 (scratch/anonymous/save) |
| `CONTRACT-001` | §1 module responsibilities | Existing modules per §1 table (repository contracts, workspace intent, Secrets System, Provider Profiles, execution boundaries, artifact/checkpoint, publisher, registry auth) | Slice 0 (this record); no new connection domain |
| `INV-001` | §1 no inferred authority | Admission/routing + runtime delivery | Slices 2-3 + Slice 0 inventory rows C-01-C-16 |
| `DOC-REQ-002` | §2 source/publication independence | Authoring UX + execution contracts | Slices 3 + 5 + 6 |
| `CONTRACT-002` | §3 workspace source union | `repository_contract.py` + `workspace_intent.py` + workspace materializers | Slices 2-3; #2615 owns source/admission implementation (not duplicated here) |
| `CONTRACT-003` | §3 access intent (`anonymous`/`routed`/`explicit`) | Same as `CONTRACT-002` + connection routing | Slices 1-3; #2615 |
| `CONTRACT-004` | §3 capability compilation | Shared capability compiler consumed by launchers | Slice 2 |
| `INV-002` | §3 publication intent preserved | `WorkflowPublishing.md` + authoring compilers | Slice 5 + `WorkflowPublishing.md` owner |
| `CONTRACT-005` | §4 `RepositoryConnection` single domain | Secrets/settings persistence (`secrets.py`, `settings.py`, `models.py`) | Slice 1; explicitly no `SourceControlConnection` |
| `CONTRACT-006` | §4 typed acquisition | Connection auth config + App/issuance adapter | Slices 1-2 + Slice 7 (App adapter) |
| `CONTRACT-007` | §4 deterministic selection | Routing/selection + persistence | Slice 1 |
| `QUALITY-001` | §4 validation vs authorization | Probes/diagnostics + evidence keys | Slice 1 |
| `CONTRACT-008` | §5 binding envelope | `credential_bindings.py`, `provider_leases.py` (`moonmind/omnigent/`), leases/continuation/serialization | Slice 2 |
| `CONTRACT-009` | §5 lifetimes (connection/revision/issuance) | `SecretsService` + acquisition | Slice 1 (revision) + Slice 2 (issuance) |
| `INV-003` | §5 refresh/revocation | Same as `CONTRACT-009` + runtime handles | Slices 1-2 |
| `QUALITY-002` | §5 throttling identity | Rate-limit coordination | Slice 2 |
| `CONTRACT-010` | §6 bound access clients | Transport + hosting API adapters; registry stays in image acquisition | Slice 2 |
| `INV-004` | §6 ambient isolation | Git/CLI execution + delivery objects | Slice 2 |
| `QUALITY-003` | §6 routing vs confinement | Managed publication vs agent-owned `auto` mediation | Slice 2 + Slice 5 |
| `INV-005` | §6 anonymous source safety | Endpoint/egress + fetch policy | Slices 2-3 |
| `CONTRACT-011` | §7 contained lifecycle | `host_services/workspace.py` + `generic_host.py` + locators | Slice 3 |
| `INV-006` | §7 restore ≠ authority | Artifact/checkpoint + continuation policy | Slice 4 |
| `CONTRACT-012` | §8 saved-work manifest | Artifact/checkpoint systems (+ manifest index) | Slice 4 |
| `QUALITY-004` | §8 portable outputs | Capture/export policy | Slice 4 |
| `INV-007` | §8 save-before-cleanup | Finalization handoff | Slice 4; reconciled by §15 decision (no runtime path enabled here) |
| `QUALITY-005` | §8 retention/access | Artifact ownership + quotas | Slice 4 |
| `CONTRACT-013` | §9 Publish Saved Work | Existing publisher (`publish/service.py`) | Slice 5 |
| `INV-008` | §9 destination application rules | Publisher + Lore/GitHub authority | Slice 5 |
| `QUALITY-006` | §9 publication recovery | Publisher idempotency + remote reconciliation | Slice 5 |
| `CONTRACT-014` | §10 explicit historical interpretation | Owning versioned boundaries; frozen legacy readers + one canonical new-write contract | Slice 0 (§13) + Slice 6 (retirement); explicitly no `RepositoryTargetV2` |
| `QUALITY-007` | §10 failure attribution | Diagnostics + audit evidence | Slices 2-5 |
| `DOC-REQ-003` | §11 credentialless model default | `OpenCodeHost.md` + `main.py` seeding + `opencode-zen-free`/`none@1` | Slice 6 (qualify; do not rename/recreate) |
| `INV-009` | §11 free-model policy | Catalog/pricing/privacy policy | Slice 6 |
| `QUALITY-008` | §11 zero-config honesty | Compose/startup + image acquisition | Slice 6 |
| `DOC-REQ-004` | §12 setup UX | Creation wizard + Source Control settings | Slice 6 |
| `DOC-REQ-005` | §12 results UX | Result views (save vs publish) | Slices 4-6 |
| `TEST-001` | §13 authority-handoff qualification | Hermetic CI at production boundaries | Slices 2-6 (each combination); live qualification separately labeled |
| `TEST-002` | §13 isolation/concurrency | Same as `CONTRACT-010`/`INV-004` + rotation/refresh | Slice 2 |
| `TEST-003` | §13 save/host/credential loss | Same as `CONTRACT-011`/`CONTRACT-012`/`INV-006`/`INV-007` | Slices 3-4 |
| `TEST-004` | §13 default-path promise | Clean-install journey + upgrade evidence | Slice 6 |
| `NON-GOAL-001` | §14 not a universal platform | Explicitly out of scope (all providers, generic credential service, extra container, Skill reimplementation, second target spec) | No slice; enforced by Slice 0 guards |
| `QUALITY-009` | §14 maintainability choices | Existing authorities per §14 table | All slices (reuse, not reimplementation) |

No requested App, restore, retention, UX, migration, or qualification slice is dropped: App enrollment is Slice 7; restore/retention are Slice 4; UX is Slice 6; migration/rollback is §5 as refined by §15; qualification is §7-§8 per-claim above.

## 13. Owning-module contract map (no new domains, no wire change in this issue)

- Source union, `anonymous`/`routed`/`explicit` access, capability bundles, role snapshots, output policy, saved-work manifests, and publication-only `savedWorkRef` requests map to their existing providing modules per the §12 table. This issue changes no wire schema and versions no contract; it records where each future wire version must be owned when its slice lands (repository contracts, workspace-intent contracts, Secrets System, Provider Profiles, execution boundaries, artifact/checkpoint systems, publisher, registry auth).
- Preserved invariants (enforced by `tests/unit/docs/test_repository_access_slice0_reconciliation.py`): `provider: git | lore` retains its VCS meaning; `RepositoryConnection` is the single connection domain (no `SourceControlConnection`); publication modes remain `none | branch | pr | auto` with existing Skill-ownership semantics; no parallel `RepositoryTargetV2` domain; `repository-connection:git-default` remains the compatibility identity for an explicitly bound effective legacy source; `decode_legacy_repository_history_v1` stays frozen history-only with original digest behavior; generated-type ownership stays with the current producers (no new generator introduced here).
- Source-independent and anonymous requests: no fake repository/credential values are introduced by this issue. The design's YAML shapes stay illustrative ("not complete wire-schema definitions"). Owner-defined validated fixtures for the future union arrive with Slices 2-3; this issue adds only the guard that illustrative examples must not be mistaken for wire schemas.
- Compatibility readers: new-write producers must use the one canonical contract and reject historical aliases; recorded workflows retain compatible deterministic decisions and worker support; versioned execution never falls back to a singleton resolver. Original digest behavior is frozen per `LEGACY_REPOSITORY_DECODER_VERSION`. Release admission gates: do not enable a new public submission path until all supported consumers enforce its bindings (§1); unsupported runtime/capability combinations are rejected rather than routed through legacy discovery.

## 14. Supported-combination support matrix (inspection only, not live support)

"Existing" below means source inspection found a present implementation at the rechecked baseline — not that the combination has passed deployment or integration qualification. Every "gap" needs its owning slice before any support claim. Capture/restore/session-reattach ownership is recorded per row.

| Runtime | Source | Access | Output / publication | Status at rechecked baseline | Capture / restore / session-reattach owner |
| --- | --- | --- | --- | --- | --- |
| Managed / Omnigent generic | `scratch` | none (no repository) | save-only (`publish.mode=none`) | Gap: scratch source union not yet in `host_services/workspace.py` (C-11 requires GitHub source today) | Artifact/checkpoint systems (Slice 4); session reattach separate from workspace restore |
| Managed / Omnigent generic | `repository` (public) | `anonymous` | save-only | Gap: explicit anonymous access not yet a first-class access mode; clean-URL clone helper exists and is reused, remaining paths need audit (C-11/C-12/C-16) | Same as above |
| Managed / Omnigent generic | `repository` (private) | `routed` / `explicit` | save-only, then deferred `Publish Saved Work` | Partial: `RepositoryConnection` + readiness gate exist; global resolvers still in C-01-C-05/C-07-C-16 so bound cutover is pending (Slices 1-2) | Same as above; deferred publication owned by Slice 5 |
| Managed (`branch`/`pr`) | `repository` | `routed` / `explicit` | immediate managed publication | Existing implementation via `publish/service.py` + push/PR path, but still on global auth (C-10); bound cutover pending | Publisher (Slice 5); remote reconciliation per `QUALITY-006` |
| Agent-owned `auto` (`pr-resolver`, `fix-comments`, `fix-ci`, `fix-merge-conflicts`) | `repository` | `routed` / `explicit` | agent-owned publication with `publish_result.json` evidence | Existing implementation per `WorkflowPublishing.md`; recovery-checkpoint branch is recovery evidence only, never success | Owning Skill during execution; finalization validates canonical evidence (Slice 4-5) |
| Jules provider | provider-managed source | provider-owned | provider-owned PR (`AUTO_CREATE_PR`) | Existing provider-owned handoff (C-08); not evidence for managed lanes | Provider session lifecycle |
| `artifact` / `checkpoint` import / restore | authorized ref + digest | artifact authorization (no source PAT) | save-only; later deferred publication | Gap: import/restore union not yet in preparation boundary (C-06/C-11) | Artifact/checkpoint systems (Slice 4) |
| `existing_workspace` locator | server-issued locator + grant | workspace authorization (advanced) | policy-approved outputs | Gap: advanced capability, not a raw path shortcut | Workspace plane / locator owner |

Reused without reimplementation (per the brief): `opencode-zen-free` identity with `none@1` materializer, clean clone URLs with stdin-delivered helpers, and qualified lifecycle helpers. Unsupported combinations (e.g. high-security `auto` without proven confinement, SSH/enterprise/other-host lanes before qualification, user-scope inheritance, free-model marketplace behavior) are rejected before execution; rejection behavior is owned by Slices 1-2 and Slice 6-7 respectively.

## 15. Recovery/publication reconciliation decision and review record (MoonLadderStudios/MoonMind#4004)

Decision: the design's proposed artifact-backed durability handoff (`INV-007` finalization chain) is accepted as a *Proposed target only*. It does not relax any current gate until the owning views explicitly accept it. The authoritative behavior until then is:

1. Preserve the AGENTS.md obligation to verify durable evidence before releasing retry, credential, workspace, or cleanup authority; a process exit, wrapper completion, assistant prose, attempt artifact, timestamp, or raw filesystem path is not objective completion.
2. Preserve `WorkflowPublishing.md` remote-recovery semantics: only an isolated terminal recovery-checkpoint branch after controlled failure, which remains failed-run recovery evidence and never success; ordinary `auto` execution stays Skill-owned.
3. Require admitted destination + mutation authority before any remote recovery/publication branch; missing GitHub credentials are never a reason to skip saving or to acquire an unrequested remote identity.

This reconciliation is recorded by the canonical-design note added in this issue (Section 1, Slice 0 decision paragraph) and this plan section. It enables no runtime path, changes no admission gate, and claims no qualification. All migration steps stay under this `docs/tmp/` plan per the documentation standard; durable architecture is promoted into owning views only after implementation settles.

Partitioning: #2615 owns source/admission implementation (this issue is contract/ownership reconciliation, not a duplicate); #1090, #2619, #3938, #3940 and the epic #4003's focused issues own their scoped slices per the §12 table. Interfaces are agreed here (§13) so downstream journeys do not wait on each other; circular task dependencies are avoided by converging new writes on the one canonical contract while frozen legacy readers preserve history.

Review record: rechecked baseline `5da35a2ba7ad80a8778ce766445184b134631fcb`; historical baseline `63bce9852ffa33e33cb0b416bc24a654b1b6f92b` retained for provenance. Contract fixtures and link/claim checks are enforced by `tests/unit/docs/test_repository_access_slice0_reconciliation.py` (42-claim presence, Proposed status, tmp-plan reference, no-new-domain guards, `git|lore` + `none|branch|pr|auto` preservation, legacy-decoder freeze, related-doc link existence). No runtime qualification is claimed by this documentation task.
