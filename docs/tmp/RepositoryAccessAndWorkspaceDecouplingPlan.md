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
