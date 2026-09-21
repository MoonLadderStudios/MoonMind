# Repository Access and Workspace Decoupling Plan

**Document Class:** Imperative working document  
**Viewpoint:** Implementation / Migration Plan  
**Status:** Remaining implementation and bounded migration, not completion evidence  
**Updated:** 2026-09-21  
**Canonical Target:** [Repository Access and Workspace Design](../RepositoryAccessAndWorkspaceDesign.md)  
**Application Model:** [Single-User Application Design](../SingleUserApplicationDesign.md)  
**Deployment Owner:** [Docker Compose Update System](../Steps/DockerComposeUpdateSystem.md)  
**Tracking:** [#4003](https://github.com/MoonLadderStudios/MoonMind/issues/4003)
**Delete/Archive Trigger:** Archive or delete once the remaining implementation and bounded migration in Status are delivered, their references have a home in the canonical design and owning successor views, and no active consumer references this plan.

This is temporary execution guidance under [AGENTS.md](../../AGENTS.md). The design owns desired behavior and providing modules own executable interfaces. This plan must not become a second authority, production registry, or approval workflow. Desired state is not a claim that a release implements it.

The [earlier plan and full Slice-0 source inventory](https://github.com/MoonLadderStudios/MoonMind/blob/93e325464e0fc5fa416b0842306e7ac18af50d00/docs/tmp/RepositoryAccessAndWorkspaceDecouplingPlan.md) remain available for concrete historical questions. Their old issue assignments, source paths, claim counts, and PR statuses are not a second backlog to execute.

## 1. Delivery constraints and design traceability

The required outcomes remain: optional repository access, deterministic selected authority, safe workspace sources, host-independent saved results, and immediate or deferred publication through one publisher. Production GitHub App support remains required for the complete feature. Narrow reliability fixes do not wait for that entire scope.

Preserve original task and publication intent, selected provider/model/cost/privacy choices, secret isolation, actual resource authority, and recoverable work. One operator can use many connections, browser tabs, workflows, and independent deployments. Connections are instance resources, not a reason to create application users, tenancy, a permission hierarchy, or another profile domain.

Prefer the existing Omnigent lifecycle and thin harness adapters. A historical managed or profile-bound path survives only while an actual supported consumer needs it. Remove replaced code and tests coherently without deleting active work or pretending retained histories are automatically compatible. No new always-on service, general credential broker, archive store, result database, or duplicate publisher is required.

Current issues identify remaining work. Stable design references help explain behavior, but exact row counts and a complete machine-readable coverage ledger do not prove it. A small change can satisfy several outcomes through one real integration journey.

## 2. Inspected foundation and implementation gaps

Source review at `93e325464e0fc5fa416b0842306e7ac18af50d00` found existing foundations, not an untouched proposal:

| Existing surface | Reuse and remaining question |
| --- | --- |
| `credential_bindings.py` and `execution_plan.py` | Model/repository authority types and v1/v2 interpretation already exist. Verify actual producer, capacity, acquisition, and cleanup consumers rather than commission another envelope. |
| `workspace_artifacts.py` | Bounded extraction, staging, import ownership, completeness checks, neutralization, and promotion exist. Verify actual authored admission, historical-reader isolation, and interrupted/concurrent handoffs. |
| Generic-host finalization and `test_attempt_completion.py` | Phase-aware reconciliation exists. A substituted publisher and synthetic saved reference do not prove storage durability after host removal. |
| `publish/service.py` | Reuse the publisher, but its live-worktree interface, optional remote verification, global-token fallback, and created-only PR result check are not a complete saved-candidate path. |
| `GithubTokenProbePanel.tsx` | The current global probe and locally defined descriptions need connection-bound transport and truthful evidence. A dropdown alone does not change credential selection. |
| Merged #4484 | Preserve gh configuration-migration suppression and isolated tool-version inspection. Build inspection must not depend on a provider login succeeding. |

These are source observations, not newly executed regressions or live qualification. Recheck current code and PRs before implementation. Do not restore old defects or repeat landed work just because a historical issue describes them.

## 3. Implementation sequence

The slices retain their original purposes with the actual existing owners. They are coordination boundaries, not a serial approval pipeline or selectable product modes.

| Slice | Existing owners | Smallest useful delivery |
| --- | --- | --- |
| 0. Contract reconciliation | #4004 and the relevant providing module | Resolve a real contradiction when encountered. No permanent caller or claim registry. |
| 1. Connections and diagnostics | #4005, #4006, #4008, #4019 | One connection/secret lifecycle and Settings form, selected discovery, accurate non-mutating probes. |
| 2. Bound access | #4007, #4009, #4010, #4011, #4012 | Wire selected acquisition and existing binding types into consumers. Keep image-registry authentication separate. |
| 3. Workspace sources | #2615, #4014 | One source compiler/materializer for scratch, anonymous repository, artifact, checkpoint, and authorized existing workspace. |
| 4. Saved work | #4015, #4016, #4017 | Capture through the real owner, commit required evidence before deletion, and retain/recover content under existing protection. |
| 5. Publish Saved Work | #1090, #4018 | One publication policy/publisher, immutable candidate, fresh destination authority, and phase-specific retry. |
| 6. UX, migration, and verification | #2619, #4020, #4021, #4023, #4024 | Preserve authoring/default intent, usable results, bounded migration, and missing integrated evidence. #3938 owns the first-run journey and #3940 its verified documentation. |
| 7. GitHub App acquisition | #4022, using shared connection/acquisition consumers | One verified enrollment and scoped expiring-credential path, not an App-specific platform. |

#1090 is not the connection or Settings wizard owner. #3938 and #3940 do not implement the publisher, storage, or runtime migration. Shared UI Profile improvements in #4001/#4002 keep their own existing form and credential-setup responsibilities.

Begin with concrete preservation, publication, and selected-credential defects. A safe correction to existing behavior can land before all new capabilities are integrated. Before enabling a new path, its actual consumers must enforce its source, authority, and durability requirements. An unimplemented required capability cannot be silently relabeled unsupported to close the work.

## 4. Required consumer cutover

### Selected authority and delivery

Use the existing connection, secret, acquisition, and transport owners. Trace actual surviving composition roots and remove alternate global-discovery callers with their replacements. Historical Manifest/indexing code and retired runtime routes are not required new consumers.

Source, collaboration, publication, model service, and image-registry credentials have different purposes. Scratch work has no incidental GitHub probe or login. Explicit anonymous access never becomes a fallback after selected authentication fails. An unavailable selected connection never causes token shopping, changed provider billing, or a broader target.

Model bindings alone enter model capacity and OAuth-home exclusivity. Repository authority uses the existing bound issuer and declared operation. Keep compact admitted references in plans and concrete issuance/delivery ownership in its runtime record. Reuse historical v1/v2 interpretation rather than add another envelope solely to finish a checklist.

Isolate actual Git/gh environment and configuration precedence without breaking separately owned model authentication. Reuse the current broker or owned projection where supported. Complete writes, refresh, and cleanup share existing ownership. Stale cleanup cannot remove a replacement credential, and a version probe does not authenticate the operator. Routing a broad token is not confinement against arbitrary agent code.

Managed publishing keeps its destination write credentials outside the agent. Preserve explicit high-security and credential-exposure policy without inventing a mediation service merely to claim unsupported confinement. Secret bytes never belong in workflow history, normal logs, container metadata, or saved exports.

### Discovery and forms

Discovery and probes use one admitted connection or protected setup candidate. Reuse existing capability definitions and endpoint validation. Read success, saved connection, authentication, write permission, and temporary throttling are separate observations. Resolve the actual default branch rather than assume `main`.

Bound pagination and provider retry through existing tools. Incomplete reads preserve existing assignments and remain incomplete. Use meaningful current-input/revision association to ignore stale results. Honor observed throttling without trying another identity or creating a global quota service. Advisory refresh failures do not erase configuration or block unrelated edits; mandatory authorization remains at the action boundary.

Source Control and Provider Profiles retain their separate existing forms. Backend declarations supply choices. Preserve explicit edits, advanced values, immutable saved IDs, and safe drafts. Match asynchronous validation to the current draft, including repeated same-value selections and A-to-B-to-A changes. Suggestions are conveniences, not identity reservations or permission to suffix and retry a failed create.

### Workspace restore

Use the existing source compiler, artifact authorization, locator grants, and materializer. A contained raw path or service credential alone does not establish source ownership. New input cannot select a tolerant historical decoder to bypass required metadata, completeness, digest, or raw-access policy.

Reuse bounded extraction and attempt-owned staging. Verify compressed/expanded limits, path/link safety, source-associated readiness, and correct storage/UID/GID handoff. Authoritative restore differs from additive attachments: residue from an old checkout cannot silently become part of a full snapshot. Interruption and lost promotion acknowledgment reconcile existing ownership rather than create another importer or leave an orphaned lock.

Restored Git history and executable source are data, not permission to run imported hooks/helpers, follow external credential paths, or revive session authority. A self-contained result needs no original PAT or network fetch. Incomplete external dependencies require separate admission. Preserve required source variants without expanding to every archive format or arbitrary host mounts.

### Saving and finalization

Use one recoverable dependency order through the existing runtime binding, Step Execution, artifact, and checkpoint owners:

```text
record confirmed compute/turn outcome
-> capture through the actual stable workspace owner
-> verify required objects and commit saved-content references
-> perform any separately admitted publication
-> release remaining content only after its preservation obligations are satisfied
```

Independent credential/capacity release follows confirmed consumer shutdown and its existing authority. Non-sensitive file retention does not require holding a model slot. Unknown shutdown or release stays pending rather than being guessed successful.

Fresh, resumed, and Checkpoint Branch paths use the same preservation contract. If a child succeeded but its later capture failed, retain the compute evidence and report saving incomplete. Do not run the child again merely to recreate its result or assume every workspace belongs to the sandbox backend.

Retry the unfinished phase. Partial upload, missing required save references, committed save with failed preview, and failed publication have different existing owners. A valid save survives optional reporting failure. Failed/canceled compute stays failed/canceled even when useful content is saved. No duplicate terminal database or finalization daemon is needed.

All janitors that can remove the authoritative content honor the same durable preservation decision. Reuse quota/grace and use protection, with bounded recovery and explicit expiry or capacity limits. Ordinary cleanup never silently deletes the only recoverable copy to clear a warning.

Verified artifact storage can satisfy save-only durability without a GitHub identity. A remote recovery push still requires admitted destination and publication authority. The obsolete statement that AGENTS.md requires every exhausted attempt to push a recovery branch is not a current prerequisite. Do not bypass an actual unimplemented save boundary or claim that a local retained path is already durable.

### Publication-only recovery

Restore immutable saved bytes through existing preparation and publish through the existing publisher. No original source credential or model run is required solely to publish. Admit the actual destination, application strategy, supported mode, and remote expectation once.

A matching baseline permits its recorded delta. An unrelated existing destination uses additive/path-mapped import by default and preserves destination-only files. Deletions need applicable baseline evidence or explicitly authorized intent. Empty-target initialization needs positive evidence and supported policy. Lore remains the publication authority for Lore-backed content, not its GitHub projection.

Persist the exact candidate before effects. Required preview/confirmation belongs to the real action, not a universal manual signoff. A retry reuses the candidate and accepted expectation. A fresh observation of a competing branch tip must not become permission to overwrite it.

Record push and PR results separately. Unknown lookup is not absence. Reconcile the same operation before another mutation, including relevant closed/merged results and later actor edits. Verified adoption is not an implicit title/body update. Push success followed by PR failure retries only the latter. Never treat any unvalidated `adopted` flag as success merely to avoid a created-only check.

Explicit None, composition defaults, and historical Skill-owned Auto retain their distinct meanings. A later Branch/PR request does not replay an old Skill or certify unrelated effects. Publication and its cancellation cannot rewrite the original compute/save outcome or pretend a confirmed remote effect did not occur.

## 5. Migration and rollback

Use the existing versioned migrations, transactions/expected revisions, and deployment controller. Map proven effective legacy references rather than assuming a global environment token won every caller's precedence. Preserve `git-default` only for its demonstrated legacy binding, never a live fallback chain.

An empty or unknown old allowlist is not wildcard authority. Recover provenance through existing trusted evidence before asking for a real choice. Unresolved choices suspend only the affected authenticated operation, while unrelated scratch or explicitly anonymous work remains usable. Never probe every token to select a winner.

Preserve saved draft and schedule identity, explicit selections, and original history bytes/digests. Compatible readers can avoid unnecessary schedule recreation. Current revocation still applies to new use of credentials recorded in old history. Add replay evidence only where workflow-visible decisions change, or provide their specific controlled transition.

A brief maintenance window is acceptable when incompatible writers cannot safely coexist. A different SHA, image digest, or patch version alone is not that incompatibility. Do not build retained fleets, another migration ledger, or a permanent startup inventory of removed sources. On uncertain commits or external effects, reconcile the existing operation before repeating it.

Rollback depends on actual schema/data/history compatibility and intervening writes. Prefer existing forward repair when restoring would discard newer work. Do not restore global credential fallback, delete saved work, or overwrite shared PostgreSQL/Temporal data to recover a connection migration. Cleanup affects only positively owned obsolete resources.

## 6. Owning-document reconciliation

Feature changes update their actual providing contracts, generated clients, and relevant operator help. Workflow Publishing owns publication modes and remote intent/outcome semantics; the provider-neutral evidence schema remains owned by Lore VCS Integration. Artifact/checkpoint and workspace documents own durable content and restore. Secrets and Provider Profiles own their distinct credential lifecycles. The single-user design owns application admission, not machine or repository scope.

The existing repository-access design remains the desired-state source. Its proposed status is not evidence of runtime readiness, nor permission to retain obsolete contradictory instructions. Reconcile a genuine conflict with the affected change rather than create another design approval gate or repeat AGENTS.md everywhere.

The former Slice-0 document-wording, path-count, and 42-row checks are not acceptance obligations. The previously named `tests/unit/docs/test_repository_access_slice0_reconciliation.py` was not found at the reviewed revision. Do not recreate it. Dependent records still referencing that test (`docs/tmp/repository-access-slice0-fixtures.yaml:9-10` and `docs/tmp/DocumentationAssertionReview3964.md:39`) remain to be retired separately and are not updated by this plan. Real executable schema/parser/serialization fixtures remain with their code owners; prose and reference inventories are not a replacement for those tests.

## 7. Test strategy and acceptance matrix

Use current AGENTS.md: targeted local/container tests for the changed behavior, broader current-candidate GitHub Actions for regression, integration, image, and browser verification. Reuse existing feature tests and add only missing handoffs. Common production mechanisms can share representative coverage, with focused cases for genuinely different storage, acquisition, or runtime behavior.

| Required outcome | Appropriate existing evidence |
| --- | --- |
| Selected repository authority | Actual transport/process tests using admitted B with ambient A, explicit anonymous/scratch inputs, and relevant refresh/revocation cases. |
| Safe source and restore | Actual compiler/artifact/materializer admission, bounded extraction, interrupted promotion, and host/source-credential-independent restore. |
| Preserved results | Storage and finalization fault tests before/after commit, branch capture failure, stale cleanup, and publication failure without repeated compute. |
| Safe publication | Local Git plus provider fixtures proving candidate/remote expectations, additive preservation, exact adoption, and phase-specific recovery. |
| Useful operator forms | Existing component/API/browser journeys covering current draft ownership, truthful evidence, and safe result/action association. |
| Historical compatibility and default path | Relevant populated migrations, real replay only when needed, and the existing clean-install journey through actual product boundaries. |

This is an explanation of outcomes, not a production conformance table, fixed test count, or new runtime gate. Do not repeat duplicated prose or matrix rows for every runtime/source/access/output/fault combination, but retain executable evidence for every supported combination through its actual boundaries; an unimplemented required capability is never silently relabeled unsupported to close the work. Keep existing relevant negative cases and actual scope/accounting protection.

Request models, import scans, source inspection, synthetic receipts, and mock publishers prove narrower facts than a served workflow or durable restore. Tests should state what they exercise. Required failures, missing execution, cancellation, and unexpected skips remain non-success. Pure prose needs review and lightweight link checks, not tests of wording or headings.

## 8. Deployment and upgrade qualification

Reuse #3938's ordinary first-run journey, not a second onboarding harness. Under controlled eligible-model availability, demonstrate default scratch work, saved output, host-independent restore, explicit anonymous source, and later admitted publication without agent rerun. When no model meets cost/privacy policy, Settings, artifacts, and diagnostics remain usable without paid fallback or an irrelevant PAT prompt.

A seeded profile, free-looking model name, or shared image does not prove that journey. Keep the supported credentialless profile and explicit operator choices rather than invent new profile identities. Production App support uses the same connection and consumer boundaries. Live provider observations are separately authorized and honestly labeled, not mandatory private credentials in ordinary PR CI.

Isolated startup, image inspection, browser/API, and database tests that need no private access belong in existing CI. Actual deployment observations are separate. Inspect each authorized deployment independently, preserve its operator URL and protection, and record the real operation/result. An inaccessible device neither certifies itself nor blocks unrelated repository work or another safe update.

This plan authorizes no live credential enrollment, paid execution, production migration, destructive cleanup, or deployment. Missing authorization is not an implementation defect or a reason to consume repeated code-remediation attempts. Do not claim continuation is scheduled unless an existing execution owner accepted it.

## 9. Source traceability and completion

The earlier multi-PAT and workspace proposals remain available through the historical plan link. Their surviving intent is preserved here: optional sources, selected authority, scope-preserving refresh, safe capture/restore, conditional exports, independent publication, and truthful model availability. The current design and revised child issues are the active requirements, not the old inventory's duplicated assignments.

Repository completion requires actual supported behavior and current evidence. It is not a merged plan, passing grep, number of closed tickets, or claim-registry completeness. Required App acquisition, source variants, saved results, and publication-only recovery remain in scope even when a narrower reliability fix lands first.

Keep remaining live or retention obligations explicit without treating them as proof that code failed. Promote settled behavior into providing documents and archive or delete this temporary plan once its real references and remaining operational consumers have a home. Preserve historical Git, immutable workflow evidence, protected artifacts, and useful saved work rather than rewriting them to erase obsolete wording.
