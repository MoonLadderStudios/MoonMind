# Workflow Publishing

**Owners:** MoonMind Engineering  
**Covers:** Authored workflow/batch publication intent, context and branch roles, compiled effect ownership, and publication outcome semantics. The provider-neutral evidence schema is owned by Lore VCS Integration Design section 3.13.  
**Related Implementation:** `moonmind/workflows/executions/execution_contract.py`, `moonmind/publish/`, `.agents/skills/_shared/publish_evidence.py`

**Related Docs:** [Workflow Presets System](WorkflowPresetsSystem.md), [Create Page](../UI/CreatePage.md), [Input Schema Guidance](../Steps/InputSchemaGuidance.md), [Executions API Contract](../Api/ExecutionsApiContract.md), [Workflow Dependencies](WorkflowDependencies.md), [PR Merge Automation](PrMergeAutomation.md), [Repository Access and Workspace Design](../RepositoryAccessAndWorkspaceDesign.md), [Lore VCS Integration Design](LoreVcsIntegrationDesign.md), [Settings System](../Security/SettingsSystem.md), [Checkpoint Branch System](CheckpointBranchSystem.md).

This document describes the publication model the compiler, publishers, and result consumers are built around. Not every part is implemented everywhere: an omitted `publish.mode` currently resolves through the `workflow.default_publish_mode` workspace setting (default `pr`), whereas the model below resolves omission from the composition's declared default ([Settings System section 10.6](../Security/SettingsSystem.md#106-publication-default-ownership-and-retired-setting)).

## 1. One Authored Policy

A user configures repository context, branch context, and publishing once per workflow or batch. Skills, Presets, Advanced mode, API, and raw JSON do not expose independently editable copies, because several editable copies inevitably disagree with nothing to decide which wins. One policy does not mean every step acts alike: a coordinator can create children without publishing, a verification step can stay read-only inside a PR-producing workflow, and a merge-automation child can run a Skill-owned publisher as part of a requested PR-and-merge outcome.

Invariants (referenced by ID in code and other docs):

- **PUBLISH-001:** One authored selection governs the submission and the children in its publication scope; no step, preset input, helper default, or prompt competes with it.
- **PUBLISH-002:** A child inherits the scope's intent, never its coordinator's derived mode `none`, through any number of coordinators.
- **PUBLISH-003:** User-facing Auto selects declared workflow behavior; compiled `auto` is the Skill-owned publishing protocol using the same evidence contract. They are different layers, not two controls or formats.
- **PUBLISH-004:** Explicit `none` is never promoted to Auto, and unsupported combinations fail before mutation rather than weakening the selection or reporting false completion.
- **PUBLISH-005:** Repository, branch, destination, and merge authority come from existing admission and connection owners; publication metadata and fan-out do not broaden them.
- **PUBLISH-006:** Outcomes come from exact-attempt evidence. A process exit, model claim, PR-shaped URL, or successful enqueue is not proof.
- **PUBLISH-007:** Retry, replay, continuation, and cleanup preserve the admitted intent, targets, candidate, and ownership, and never re-resolve a changed default to gain authority.

## 2. Authored Intent and Compiled Behavior

| Layer | Meaning | Authority |
| --- | --- | --- |
| Authored selection | Auto, None, Branch, PR, or PR with merge automation, plus task options | Submitted input |
| Resolved scope intent | Composition default, output roles, finish behavior, pinned definition evidence | Shared compiler |
| Per-execution publication | `none`, `branch`, `pr`, or Skill-owned `auto`, with owner, target, evidence contract | Compiled plan |
| Observed result | Verified push, PR, merge, no-op, blocked, failed, or enqueue outcome | Publication/Skill artifacts |

These are projections of one intent held in the existing snapshots, plan artifacts, lineage, and result contracts; there is no separate publishing database or per-Skill exception registry.

The authored `task.publish.mode` vocabulary is `default | none | branch | pr | pr_with_merge_automation` (`default` displays as **Auto**; omission is equivalent). The compiled `publishMode` is `none | branch | pr | auto`. `pr_with_merge_automation` compiles to `pr` plus merge-automation configuration, `default` never reaches a publisher unresolved, and a recorded authored literal `auto` keeps its Skill-owned meaning. Only trusted compilation emits worker-facing `publishMode = auto`, and every entry point (execution- and task-shaped requests, presets, schedules, MCP) goes through the same compiler, so callers cannot bypass it with compiled fields. A coordinator compiling to `none` never overwrites the authored `pr` or `default` in its snapshot.

Resolution: a scoped child consumes its authenticated parent's frozen intent; a new root keeps an explicit supported selection, while omission and `default` resolve the declared default from pinned metadata and task inputs; deployment, repository, and approval policy may reject the result but never substitute another selection. Recorded histories replay with their frozen decoder.

### Execution Roles

Catalog/plan metadata declares each capability's role: **repository deliverable** (candidate for managed branch/PR publication), **existing-PR operation** (a portable Skill owns effects on an identified PR), **coordinator** (discovers targets and dispatches children, no deliverable of its own), or **read-only/external side-effect step**. Roles are not Create-page selectors and are not inferred from names like `batch` or `orchestrate`. A workflow that edits the repository and queues children has both roles; fan-out does not exempt its own deliverable.

## 3. Auto and Explicit Choices

| Selection | Behavior |
| --- | --- |
| **Auto** | Resolve the workflow's declared behavior and show it before submission. |
| **Do not publish code** | No repository publication in this workflow or its scoped descendants; artifacts still saved. |
| **Push to branch** | Publish eligible deliverables to the selected branch through the managed publisher. |
| **Create pull request** | Publish each deliverable through a generated work branch and PR against the selected base. |
| **PR with merge automation** | Create the PR and run the authorized readiness/review/merge lifecycle. |

Available choices derive from the composition's capabilities.

Auto is deterministic metadata-driven selection, not permission for a model to invent an output strategy. Implementation defaults to managed PR when declared, verification and artifact-only work to no publication, and existing-PR work to the declared Skill-owned protocol. A preset whose default includes merge automation keeps it, and the preview says when the result can merge — Auto is not a read-only mode. Changing a controlling input such as Batch Jira's Run selection recomputes the recommendation only while Auto is selected; explicit choices survive and are revalidated. Unknown or incompatible requirements produce an actionable error, never a most-permissive or silent-None fallback. The resolved default is pinned before child creation; a new schedule occurrence resolves current defaults, while in-flight occurrences and retries stay pinned.

Task options are not publishing controls. `pr-resolver.finishMode = fix_only` still remediates, pushes, and verifies the current head; it does not mean None. The `pr-review-resolve` preset's `review_only` (default false) selects publication None and requests fresh review of the current PR head, finishing at review_complete even with findings, with no resolver, remediation, publication, or merge authority. Review provider, merge method, discovery filters, and dry run likewise cannot grant effects the policy forbids.

### Explicit None

`none` prohibits repository publication throughout the scoped tree, including resolver children and implicit merge phases. A capability whose objective requires a push or merge cannot run under None unless it declares a genuinely non-publishing behavior: `fix_only` does not qualify, review-only does. Review-only admits repository read and `review_request` collaboration from the typed `parentExecutionPlan` of the admitted parent and its immutable collaboration repository snapshot; caller-supplied tokens or ambient connections cannot replace them.

None still allows local workspace work, local Git metadata, artifact capture, issue creation, Jira transitions, and child dispatch when independently authorized; it is not a dry-run switch. It does not authorize a remote recovery push, so a deployment that cannot preserve required work without one rejects the execution before mutation rather than discarding work.

## 4. Repository and Branch Context

A workflow has one canonical repository/source target and one authored branch. Skills and presets receive projected arguments at the trusted adapter boundary; a portable `repo`/`branch` CLI argument can exist outside MoonMind without becoming a form field. Context-bound inputs resolve before validation, are not persisted as overrides, and a conflicting supplied copy is an error. Changing repository or branch invalidates dependent lookups and previews so Reapply cannot resurrect stale values. Bindings are semantic and type-checked per [Input Schema Guidance](../Steps/InputSchemaGuidance.md), never matched by field name, because an issue target, comparison branch, source repository, and publication destination can be distinct roles ([Repository Access and Workspace Design](../RepositoryAccessAndWorkspaceDesign.md)).

For provider-discriminated targets, including Lore, the planner and workspace launcher derive source repository and starting branch from the target, and new submissions reject conflicting `workflow.branch` or `workflow.startingBranch` copies.

| Work | Meaning of branch context |
| --- | --- |
| No publication | Starting branch |
| Managed branch publication | The branch being updated |
| Managed PR publication | Starting/base branch; the head is generated or provider-owned |
| Existing-PR operation | Head and base come from the selected PR |
| PR-resolution batch | Each child uses its discovered PR head and base |

Legacy `task.git.branch` and older aliases are decoded only at the versioned ingress/history boundary. Checkpoint creation, continuation, Edit/Rerun, and resolver targeting follow the same rule: [Checkpoint Branch System](CheckpointBranchSystem.md#89-execution-detail-output-branch) keeps source, work branch, and output distinct, and the [PR Resolver](../Steps/SkillGithubPrResolver.md#41-inputs-skill-args) never treats the checkout branch as a PR locator.

An omitted branch resolves to the repository's authoritative default (the clone's `origin/HEAD`, or the remote's symbolic `HEAD`, persisted), never an assumed `main`; retries keep the resolved value, and an unavailable default requires explicit selection. An explicit base such as `release/1.2` survives discovery and fan-out. For an existing PR, a branch-only lookup must resolve exactly one eligible PR; conflicting references are rejected, and missing, ambiguous, fork-only, or unsupported cross-repository targets fail or are skipped without guessing. A PR batch's own checkout branch is never a child destination.

### Work Branches and Candidates

Managed PR work uses one stable work branch per deliverable, `{clean-title-prefix}-{uuid8}` (lowercased, non-alphanumerics as `-`, prefix at most 40 characters). It is persisted and reused on retries, so a retry never creates another branch or PR.

A later step may start from an earlier step's verified cumulative candidate, but compares against the original authored base; the candidate is never its own base. A later PR step fast-forwards the candidate under an exact-tip lease and reports the authored base instead of stacking a branch. Restored checkouts refresh the exact remote base before counting commits, and extension requires the remote candidate to still equal the accepted head SHA, which is also the push lease, so another actor's update is never silently absorbed. Missing base evidence blocks publication.

When the base already contains the owned candidate, an unchanged later step returns canonical `no_commits` and skips PR discovery. A zero-change result may also show a saved revision is reachable from an advanced base; that is saved-work evidence, not publication, and keeps `remoteVerified` false so exact-head remediation consumers never admit a stale base as a write destination.

Git attribution is chosen at server admission and retained in workflow memo. Workspace preparation and publication read that choice through the worker's existing Temporal client, binding the lookup to the actual Activity workflow/run and its recorded parents. Authored request parameters, including retained legacy descriptors, cannot establish attribution. Confirmed absence keeps the deployment fallback; an unreadable or ambiguous recorded owner blocks instead of silently changing identity. Replay-gated child starts and Continue-As-New carry the admitted memo. This keeps restored agent-created commits and publisher-created commits attributable to the same admitted choice across deployment changes.

## 5. Workflow and Batch Inheritance

```text
Authored scope: Create pull requests
  Coordinator: no direct repository publication; records enqueue evidence
    Implementation child A: managed PR against the selected base
    Implementation child B: managed PR against the selected base
```

Coordinators forward the admitted scope intent, including merge-automation configuration, never their local `none`. Child creation is validated server-side against the authenticated parent and pinned composition. Each child keeps its own execution, target, candidate, result, and cancellation state; shared policy does not imply a shared branch, atomic publication, or that enqueue success equals child success. A `dependsOn` edge to an independent workflow does not change its policy, and a child cannot escape restrictions by claiming to be a new submission.

Static incompatibilities are rejected before launching the parent or creating tracker issues, and each discovered child is validated before dispatch. If an external failure occurs mid-batch, created IDs, skips, and errors are preserved without claiming rollback or success.

Parallel children cannot all push to one branch, so Branch mode is unavailable for such a batch unless it declares a serialized shared-branch handoff; MoonMind does not silently generate per-child branches. PR mode is the normal batch model, within the admitted repository. Completion of another workflow does not make its code available on the next child's branch, so compositions that need predecessor code declare a handoff (verified merge, verified candidate transfer, or serialized branch sequence), and switching to PR-only or None cannot silently remove it.

## 6. Built-in Behavior Matrix

This matrix is the reference for catalog metadata, authoring preview, compiler output, and fan-out validation. Slugs identify coverage; core code does not branch on names.

| Preset or Skill | Auto behavior | Notes |
| --- | --- | --- |
| `batch-workflows` + `skill:jira-verify` | Coordinator and children publish nothing | Keeps Jira query, verification, status-update controls. |
| `batch-workflows` + `preset:jira-implement` / `preset:jira-orchestrate` | Coordinator none; each child creates a PR | Children inherit repository, base, runtime, scope policy. |
| `batch-github-workflows` | Coordinator none; each eligible issue child creates a PR | Queries real open issues, not every number in a range. |
| `github-issue-breakdown-implement` / `-orchestrate` | Parent creates issues/dependent workflows; children create PRs | PR-and-merge requires the child's handoff contract. |
| `jira-breakdown-implement` / `-orchestrate` | Children use PR with merge automation | Merge default is displayed; alternatives keep predecessor handoffs. |
| `document-update-orchestrate` | Children use PR with merge automation | Preserves ordering and dependencies. |
| `batch-pr-resolver` | Parent queues existing-PR coordinators; resolver children use Skill-owned Auto | Trusted target tool qualifies each head/base; fork protections kept. |
| `batch-dependabot-resolver` | Same, for matching Dependabot PRs | Conservative filters, caps, dry run, per-PR/head dedup. |
| `pr-resolver` | Skill-owned Auto repairs and merges when finish mode is `merge` | `fix_only` pushes, never merges; None is incompatible. |
| `fix-comments`, `fix-ci`, `fix-merge-conflicts` | Skill-owned Auto for declared PR/branch effects | Exact remote-head evidence. |
| `pr-review-resolve` (default) | Review/fix existing PR; merge off by default | Merge automation declares the resolution owner before targeting. |
| `pr-review-resolve`, `review_only: true` | No publication; fresh current-head review, review_complete | Only repository read and review_request; CI does not gate it. |
| Standalone implementation presets (Jira/GitHub Implement and Orchestrate, document author/update, MoonSpec Orchestrate) | Managed PR | Assessment, verification, tracker steps stay non-publishing. |
| Standalone assessment, verification, tracker work | No publication | Tracker effects and saving have their own contracts. |

Repository and publish passthrough inputs are supplied through context bindings rather than forms. A preset requiring a PR URL, merged prerequisite, or publishing resolver rejects choices that cannot satisfy it.

### Deduplication and Policy Identity

Retrying a child submission reconciles the existing child first, checking the parent's pinned intent and target; a matching idempotency key with a conflicting policy is not reported as a new child. Dependabot's cross-run identity is repository, PR, and head SHA; an existing resolver at that identity is reported as skipped with its actual policy, and schedule edits neither start conflicting resolvers nor mutate old children.

## 7. Publication Ownership

`MoonMind.UserWorkflow` owns compiled policy, authorization requirements, orchestration, and the evidence-derived outcome; trusted publisher Activities own managed remote mechanics; portable Skills own their declared semantics. The compiler derives `repositoryOperation` per role, so a coordinator gets no write credentials because its descendants produce PRs, and a read-only step cannot erase the workflow's intent.

A plan in which **every** step declares `repositoryOperation: read` (top level or binding inputs) cannot produce publication evidence, so `branch`, `pr`, or `auto` would fail late with `auto_publish_evidence_missing`. Per PUBLISH-004 that combination is rejected at admission, naming the step to change, on the task path, at the shared execution-creation boundary used by rerun, continuation, checkpoint branching, and deployment, and before a schedule is created.

A composition may stage candidate publication, verification, an early PR handoff, and tracker updates, each with one declared owner and target; mixed owners or unrelated repositories need a declared composition or separate workflows.

### One Provider-Neutral Evidence Contract

All publishers emit `moonmind.publish.repository.v1`, defined in [Lore VCS Integration Design section 3.13](LoreVcsIntegrationDesign.md#313-unified-repository-publication-evidence). Managed publication sets `owner = moonmind`; Skill-owned publication sets `owner = agent` with mode `auto`. Owner changes responsibility, not evidence strength. Resolved `none` emits no publication evidence rather than a fabricated no-op. Review-only keeps request-bound reviewer completion and current-head observations as its evidence; a moved head blocks with `stale_revision` rather than retargeting.

Evidence is bound through the terminal contract and artifact provenance to the exact workflow/run/Step Execution/attempt and target, and validated before acceptance; restored or other-attempt artifacts are stale even if revision and remote state match. No producer emits evidence without admitted `connectionRef` and `clientEvidence`. `moonmind.publish.auto.v1` and `acceptedRepositoryEvidence` have frozen readers for recorded histories only.

Git and Lore use provider-specific revisions, and success or no-op needs exact remote proof. A Lore Content-only revision is not No Commit because a generated Git diff is empty. PR outcomes need the confirmed native PR or authoritative mapped projection; a pending Lore projection is `awaiting_external`. Protected Lore merges stay coordinator-owned, never a GitHub merge against generated refs.

### Compiled `auto`: Skill-owned Publishing

Auto-publish capability is declared in resolved Skill metadata:

```yaml
metadata:
  repository:
    supported-providers:
      - git
  publish:
    mode: auto
    owner: agent
    requiresEvidence: true
    evidence-schema: moonmind.publish.repository.v1
```

The resolved bundle is the semantic authority; a matching name cannot confer support on a different bundle, and publishing mode never selects a native substitute (`pr-resolver` runs its resolved bundle through the agent path while native code supplies policy and evidence validation). Missing capability evidence is not repaired by name-based fallback.

The Skill writes `artifacts/publish_result.json` in the unified schema, bound into the terminal contract by MoonMind-local workspace authority (external-provider workspaces use their qualified handoff). Missing or stale evidence gets bounded continuation in the same workspace and Skill snapshot; exhaustion is failure, with work preserved before cleanup. The portable helper `$MOONMIND_ACTIVE_SKILLS_DIR/_shared/publish_evidence.py` (`.agents/skills` outside MoonMind) projects provider observations and admitted connection/client context into the schema; it cannot build a result from bare repository or branch strings, and it has no Temporal or database imports.

| Evidence | Finish outcome |
| --- | --- |
| Verified authorized agent merge | `PUBLISHED_PR`, owner agent |
| Exact branch publication, no PR/merge outstanding | `PUBLISHED_BRANCH` |
| Confirmed exact native/mapped PR | `PUBLISHED_PR` |
| Exact Lore publication, projection pending | `awaiting_external`, `LORE_PROJECTION_PENDING` |
| `no_op_verified` with compatible completed objective | `NO_COMMIT`, not `PUBLISH_DISABLED` |
| Blocked or failed evidence | Publish-stage block/failure |
| Missing, invalid, or stale evidence | Publish-stage failure (`auto_publish_evidence_missing`) |

Success also needs objective-specific evidence: a push alone does not complete a merge-finish resolver. The parent validates the child's `publishEvidence` reference through the shared reader and never repeats the Skill's commit, push, or merge.

### Coordinator Outcomes

Batch coordinators (compiled `none`) produce enqueue evidence such as `batch_pr_resolver_result.json`, `batch_dependabot_resolver_result.json`, `batch-workflows-result.json`, and `skill_outcome.json`. PR batch helpers record `running` before discovery and a terminal result with verified child IDs, skips, and errors after; the evaluator accepts a fully accounted queue or no-op, preserves partial failures, and rejects missing or stale results, because a harness going idle after a permission denial proves nothing. Outcomes display as queued, no targets, partial, or failed, separately from descendant progress.

### Managed Branch/PR Publication

Agents produce the candidate and description; the managed publisher performs commit/push/PR deterministically rather than relying on a prompt, using credentials from the admitted connection rather than ambient Git or `gh auth` state. It refreshes the exact remote base, uses compare-and-set or an exact lease, and verifies the live remote equals the candidate; an unavailable base, unexpected tip movement, or indeterminate comparison yields no evidence and no PR. Lost acknowledgements are reconciled before repeating, and persisted candidate and branch identity prevents duplicate commits or PRs.

In `branch` mode the Omnigent publication boundary passes the authored branch as the destination for every harness, verifies fast-forward ancestry, and uses an exact-tip lease, so concurrent updates or deletion fail rather than being overwritten; a retry already at the remote head returns verified no-change. Pushes to `main`, `master`, detached `HEAD`, unknown states, or protected branches are rejected, PR mode pushes only the work branch, and a refused push is an explicit blocker.

### Publish Saved Work

Saved work (a committed `saved-work-manifest/v1`) can be published later without its source PAT, workspace, or another model run. `POST /api/executions/{workflowId}/retry-publication` with a saved-work body (`savedWorkRef`, `destination`, optional `sourceRunId`, PR title/body, commit message) admits one decision: the manifest closure must be readable by the operator and linked to the exact workflow and source run (the current run if omitted), and its digest, destination, objective (`branch`, `pr`, `draft_pr`), application strategy, and base/head expectations are frozen. Omitting the body keeps the recorded publication-recovery path. Both run as `MoonMind.PublicationRecoveryV1`, admitted by default for all three objectives and adjustable (off, shadow, fewer objectives, canary repositories) through `FEATURE_FLAGS__PUBLICATION_RECOVERY_*`; `actions.canPublishSavedWork` reports the same gate. Workflow Detail's Saved Results section submits it ([Artifact Presentation Contract](../Artifacts/ArtifactPresentationContract.md)).

The form supplies an `admissionGeneration` per new admission, kept in session storage and reused across lost acknowledgements, refusals, and reloads, and rotated only after the matching workflow is known terminal; if it cannot be retained, nothing is submitted. Fresh admission never rewrites the original admission, candidate, or receipts, and never reruns implementation.

The workflow runs only publication Activities from `moonmind/publish`:

1. **Prepare** claims a `publication` use of each saved object before reading it so retention cannot remove it, verifies digests, materializes a fresh directory, observes the base once if no expectation was given, and builds a deterministic candidate. Admission and candidate are stored in history and `saved-work-publication-decision.json` before any effect, so a later run of the same operation reuses the matching decision and completes only unfinished effects.
2. **Push** requires the persisted SHA, uses the admitted head expectation as the only lease, and verifies the remote head; a lost acknowledgement reconciles instead of pushing again.
3. **Pull request** creates a PR only while the head still holds the candidate, adopts an open PR only when its head is the candidate, never edits metadata, and blocks on a closed, merged, or mismatched PR. PR retries never repeat the push.

Strategies: `baseline_delta` applies the recorded delta onto a destination containing the baseline; `additive_import` keeps destination-only files and surfaces conflicts; `empty_initialization` requires an empty destination.

Admission refuses (`409 publication_lore_authoritative`) a GitHub repository that a Lore connection uses as its review projection. Destination authority is `repository-connection:git-default`; a deleted or disabled default fails rather than falling back to another token, and a changed connection, commit, or destination is a new decision.

`saved-work-publication-result.json` records push and PR outcomes separately. An Activity that ended without a result is `unconfirmed` with a reason (for example `publication_activity_timeout:start_to_close`), never omitted. Retries wait at least the provider's cooldown and stop when cancellation is pending; cancellation lets an in-flight attempt finish and still persists the result. Saved artifacts and the source execution are never modified, and cleanup releases only the publication's own claims.

## 8. Pull Request Creation and Metadata

GitHub PR publication uses the admitted API client; a qualified CLI transport may implement it under the same identity but is not a credential fallback. It needs Contents and Pull requests write (plus Workflows write for `.github/workflows/*`); readiness needs commit-status/check access and Issues read when the reaction fallback uses it.

Titles and bodies describe the implemented change, not orchestration. Agents propose them after the final diff and verification; MoonMind validates and publishes them. Jira-backed titles start with the issue key, for example `PROJ-123 Validate delivery records before submission`, and bodies include the issue link, traceability, behavior, verification verdict, tests, and risks. A title like `Move Jira issue PROJ-123 to Code Review` is not valid implementation metadata. An early PR handoff needed by a tracker transition is still managed publication under the same policy and records the confirmed PR before the transition; an unverified URL is never enough.

## 9. Merge Automation

For PR-and-merge intent, successful PR publication starts the parent-owned `MoonMind.MergeAutomation` child, and the workflow stays `awaiting_external` until it finishes; dependents are not released just because the PR opened. Its resolver may compile to Skill-owned `auto` without changing the authored policy and acts only on the admitted PR. Existing-PR workflows adopt their target rather than creating a PR, and a direct resolver does not also get a managed publisher or second merge loop. Review-only uses the same owner's review-request operation under None. Detail payloads show explicit PR-and-merge as `pr_with_merge_automation` and Auto with its explanation, while worker input stays `pr` plus automation configuration.

For Jira-backed work, `jiraIssueKey` is the default post-merge completion target, and an explicit `postMergeJira.issueKey` is validated separately and overrides it. MoonMind never fuzzy-searches summaries or transitions every key in PR text. Completion needs objective evidence on the freshly resolved target, per the portable [acceptance policy](../../.agents/skills/moonspec-verify/references/acceptance-policy.md). Post-merge GitHub finalization checks the PR against a fresh read — repository, number, candidate head, issue reference, merged state, and merge commit must agree — and Jira Review requires a live, open, non-draft PR for the current candidate.

## 10. MoonSpec Verification Gate

Eligibility uses the latest structured verdict and the run-owned accepted publication artifact. `FULLY_IMPLEMENTED` with scope-bound evidence permits publication; `ADDITIONAL_WORK_NEEDED` continues bounded remediation, and after exhaustion a PR-authorized workflow publishes a draft with the remaining-work report, completes with `attention_required: true`, and skips promotion. A pushed branch or draft PR is not `no_commit` and does not make an incomplete objective successful. A read-only verification step defers to the run-owned evidence reference rather than raw `pushStatus`, `branch`, or `headSha`, and the draft targets that same accepted revision.

`NO_DETERMINATION`, `BLOCKED`, and `FAILED_UNRECOVERABLE` block publication unless the contract models recoverable missing evidence. A malformed verdict envelope is not a verifier judgment: MoonMind injects the canonical contract, derives the action from the verdict, and allows at most one report-only corrective re-verify within the existing budget, never rerunning implementation, switching models, or inventing a verdict. A `NO_DETERMINATION` without current-runtime recovery becomes an automation-owned `blocked` continuation rather than an implicit `needs_human`. Contract-invalid evidence downgrades fail-closed with a `downgradeReason`.

A blocked gate records `publicationBlockedBy: "moonspec_verify"` and `failureSummary.type = "moonspec_verification_gate"` in `reports/run_summary.json` and skips publication and Jira handoffs. Environment-class `BLOCKED` or malformed `NO_DETERMINATION` publishes an attention-required draft for the accepted revision; verifier-declared `NO_DETERMINATION` and `FAILED_UNRECOVERABLE` stay fail-closed. Draft publication never turns None or Branch into a PR grant and never enables merging after incomplete verification.

## 11. Runtime Instructions and Provider Boundaries

Runtime instructions follow the compiled role and explain authority without replacing admission, credentials, egress, and publisher controls. Skill-owned auto performs only the resolved Skill's effects and writes current-attempt `publish_result.json`; a non-publishing scope does not push, create PRs, or merge; a coordinator dispatches admitted children with the inherited intent, not its local `none`; managed Branch/PR steps commit the candidate and leave pushing and PR creation to the trusted publisher.

A provider-native runtime such as Jules can supply publication mechanics through its adapter, but its automatic-PR feature cannot implement Branch by creating a PR, implement None by publishing, or manufacture evidence. Its outputs meet the same evidence requirements and never trigger duplicate local publishing.

Publication scan fetches terminate option parsing before remote/ref operands, per-file scans disable external diff and text conversion, publisher authentication uses an in-memory host-scoped helper, and repository verification runs without publisher credentials. These measures do not isolate Git from agent-writable hooks, configuration, or worker-readable files.

### Dependency Publication

Managed and saved-candidate Git publication verify each changed gitlink against its revision's `.gitmodules` URL (relative URLs resolved against the publication remote) by fetching the exact commit into a temporary repository; local overrides, cached refs, and fork-only commits do not count. The parent push uses `--recurse-submodules=no` so local configuration cannot publish dependencies on the consumer's behalf. An unfetchable dependency records `dependency_commit_unavailable` as a prerequisite failure instead of retrying the push, and the same candidate resumes once the dependency is published.

## 12. Historical Contracts

Persisted histories keep their original bytes, digests, compiled behavior, and replay semantics, and drafts, schedules, reruns, and worker admission cross the same versioned boundary. Equal workflow and Skill repository/branch copies collapse to one binding; conflicting copies surface a conflict rather than a silent winner. A recorded literal `auto` keeps its Skill-owned meaning. An explicit None that an older contract promoted to Auto replays under that contract, while a new draft needs a reviewed choice. Mixed per-step policies are preserved historically but need a declared composition in new authoring. Legacy GitHub issue lifecycle shapes are history only ([GitHub Issue Legacy Cutover](GitHubIssueLegacyCutover.md)). Unknown provenance is not permission to infer intent; historical fields may display, labeled, without becoming active inputs. Replay and failed-step recovery keep the admitted contract, while an intentionally changed repository, branch, or objective is new authoring with lineage.
