# Workflow Publishing

**Document Class:** Canonical declarative  
**Viewpoint:** Module Contract Specification  
**Status:** Draft  
**Owners:** MoonMind Engineering  
**Updated:** 2026-09-06  
**Audience:** Workflow, runtime, API, and dashboard contributors and operators  
**Authority:** Authored workflow/batch publication intent, context and branch roles, compiled effect ownership, and publication outcome semantics. The provider-neutral evidence schema is owned by Lore VCS Integration Design section 3.13.  
**Owning Surface:** Workflow admission/compiler, publication orchestration, and repository publisher consumers  
**Related Implementation:** `moonmind/workflows/executions/execution_contract.py`, `moonmind/publish/`, `.agents/skills/_shared/publish_evidence.py`; implementation tracking #1090 and #2619.

**Related Docs:** [Workflow Presets System](WorkflowPresetsSystem.md), [Create Page](../UI/CreatePage.md), [Input Schema Guidance](../Steps/InputSchemaGuidance.md), [Executions API Contract](../Api/ExecutionsApiContract.md), [Workflow Dependencies](WorkflowDependencies.md), [PR Merge Automation](PrMergeAutomation.md), [Repository Access and Workspace Design](../RepositoryAccessAndWorkspaceDesign.md), [Lore VCS Integration Design](LoreVcsIntegrationDesign.md), [Settings System](../Security/SettingsSystem.md), [Checkpoint Branch System](CheckpointBranchSystem.md).

This document defines the long-term contract. It is not a claim that every current seed, helper, API, or runtime already implements it. Implementation sequencing and rollout evidence belong in issues or `docs/tmp/`, not in this specification.

## 1. One Authored Policy

A user configures repository context, branch context, and publishing once for a workflow or batch. Ordinary Skill and Preset settings do not expose independently editable copies, including in Advanced mode. API and raw-JSON authoring obey the same rule.

**One publication policy does not require every step or child to perform the same publication action.** A coordinator can create children without publishing repository changes. A verification step can remain read-only inside a PR-producing workflow. A merge-automation child can run a Skill-owned publisher as part of the parent's requested PR-and-merge outcome.

The following invariants govern every entry point:

- **PUBLISH-001:** One authored publishing selection governs the submission and the children created under its publication scope. No step, preset input, helper default, or agent prompt is a competing selection authority.
- **PUBLISH-002:** A child inherits the scope's publication intent, never its coordinator's derived execution mode `none`. The intent survives every intermediate coordinator.
- **PUBLISH-003:** User-facing Auto selects declared workflow behavior. Compiled execution `auto` remains the Skill-owned publishing protocol, using the same provider-neutral evidence contract as managed publication. These are different layers, not two user controls or two evidence formats.
- **PUBLISH-004:** Explicit new `none` is never promoted to Auto. Unsupported combinations fail before mutation rather than weakening the selection or falsely reporting completion.
- **PUBLISH-005:** Repository, branch, destination, and merge authority are validated through existing admission and connection owners. Publication metadata and a fan-out bearer are not permission to broaden those authorities.
- **PUBLISH-006:** Outcomes come from exact-attempt evidence. A process exit, model claim, PR-shaped URL, or successful enqueue is not proof of publication or completed child work.
- **PUBLISH-007:** Retry, replay, continuation, and cleanup preserve the admitted intent, target identities, candidate, and ownership. They never re-resolve a changed default to acquire new authority.

## 2. Authored Intent and Compiled Behavior

The existing authored-input snapshot and immutable execution plan carry different information:

| Layer | Meaning | Authority |
| --- | --- | --- |
| Authored selection | Auto, None, Branch, PR, or PR with merge automation, plus meaningful task options | User's submitted workflow input |
| Resolved scope intent | The declared default resolved for this composition, applicable output roles, permitted finish behavior, and pinned definition evidence | Shared backend compiler |
| Per-execution publication | `none`, `branch`, `pr`, or Skill-owned `auto`, owner, target, and evidence contract | Compiled plan for the particular execution or step |
| Observed result | Verified push, PR, merge, no-op, blocked, failed, or coordinator enqueue outcome | Authoritative publication/Skill artifacts |

These are projections and compilations of one intent, not independent editable configurations or a new policy service. Reuse the existing workflow snapshots, plan artifacts, parent/child lineage, and result contracts. Do not add a parallel publishing database, coordinator, or per-Skill exception registry.

### Authoring Representation

For the new authoring contract, the existing authored `task.publish.mode` selection uses:

```text
default | none | branch | pr | pr_with_merge_automation
```

`default` is displayed as **Auto**. Omission has the same semantics as `default`. The normalized compiled `publishMode` remains:

```text
none | branch | pr | auto
```

`pr_with_merge_automation` compiles to `pr` plus the existing merge-automation configuration. `default` never reaches a publisher or portable helper as an unresolved execution mode. The legacy authored literal `auto` is decoded under its recorded contract as Skill-owned publication, not silently reinterpreted as the new general default. A fresh resolver request authors `default` or omits the selection and supplies its explicit target. Only trusted compilation emits worker-facing `publishMode = auto`.

The authored snapshot must remain intact when a coordinator compiles to `publishMode = none`. A serialization round trip must not overwrite the authored `pr` or `default` with that local result. Direct execution-shaped requests, task-shaped requests, presets, schedules, and MCP use the same compiler. Public callers cannot bypass it by supplying worker-facing compiled fields.

### Default Precedence and Retirement of the Workspace Fallback

The target removes `workflow.default_publish_mode` from active new-authoring resolution. Its environment aliases `WORKFLOW_DEFAULT_PUBLISH_MODE` and `MOONMIND_DEFAULT_PUBLISH_MODE` are retired with it. It is not retained as a lower-priority fallback, renamed recommendation, or hidden source of an apparently explicit form value.

There is one resolution rule:

1. A scoped child consumes its authenticated parent's frozen scope intent. It cannot author an override or consult a workspace fallback.
2. For a newly authored root, an explicit supported selection is preserved. Omission and `default` both resolve the reviewed composition's declared default from the same pinned metadata and task inputs.
3. Deployment, repository, and approval policy validate the requested effects. They may reject an incompatible result, but do not substitute another selection. Missing or conflicting declared defaults are actionable errors, not a reason to consult the retired setting.

Existing operator intent must not be silently discarded during this change. The [Settings System retirement contract](../Security/SettingsSystem.md#106-publication-default-ownership-and-retired-setting) records configured overrides and environment values through the existing settings migration/audit owner. A definition, draft, or saved input whose recorded legacy resolution proves that the workspace value supplied its publishing intent can be reconstructed with that value as an explicit selection, with provenance and normal compatibility validation. Existing explicitly authored choices remain unchanged. A configured fallback without sufficient per-input provenance requires visible operator review before affected new/defaulted or unattended launches use composition Auto. Clearing the retired override alone is not proof that existing schedules were reviewed.

Recorded histories use their frozen old decoder and effective values, never the current workspace setting. No new request can claim to be historical to recover the old fallback. The coordinated implementation removes the active catalog descriptor, new-write setting/environment producers, UI hydration, API/preset/helper fallback readers, and tests of that superseded default together. Diagnostic historical rows may remain under the Settings System's bounded removal policy; they do not participate in new execution resolution.

### Execution Roles

A resolved capability or composition declares its role through the existing catalog/plan metadata:

- **Repository deliverable:** produces a candidate for managed branch/PR publication.
- **Existing-PR operation:** a resolved portable Skill owns allowed effects on an identified existing PR and produces its terminal evidence.
- **Coordinator:** discovers or creates targets and dispatches bounded child work without a repository deliverable of its own.
- **Read-only or external side-effect step:** produces evidence, reads a repository, or performs independently authorized tracker operations without repository publication.

These roles are not extra Create-page selectors. They are not inferred from names containing `batch` or `orchestrate`. A workflow that edits the repository and queues children has both responsibilities; fan-out does not exempt its own deliverable from publishing policy.

## 3. Auto and Explicit Choices

| User-facing selection | Target behavior |
| --- | --- |
| **Auto** | Resolve the selected workflow's declared behavior and show the result before submission. |
| **Do not publish code** | Do not publish repository changes in this workflow or its scoped descendants. Preserve artifacts under the independent save contract. |
| **Push to branch** | Publish eligible deliverables to the selected branch through the managed publisher. |
| **Create pull request** | Publish each independent deliverable through its generated work branch and PR against the selected base. |
| **PR with merge automation** | Create the PR and run the existing authorized readiness/review/merge lifecycle. |

The available choices are capability-derived. One shared control does not imply every choice is supported by every composition.

### Auto Resolution

Auto is deterministic, metadata-driven selection, not permission for a model to invent an output strategy. The compiler resolves the selected preset/Skill content, task options, output roles, and policy before dispatch. Ordinary implementation defaults to managed PR publication when declared by its composition. Verification and artifact-only work default to no repository publication. Existing-PR resolution uses the declared Skill-owned protocol.

A preset with an established merge-automation default retains that default and exposes its consequences. Auto is not an unconditional safe/read-only mode. The preview must say when the selected behavior can merge.

Changing a controlling input such as Batch Jira's Run selection recomputes the recommendation only while Auto is selected. An explicit None, Branch, or PR choice survives a Run change and is validated against the new composition. Unknown or incompatible publication requirements produce an actionable error, not a most-permissive, last-step-wins, silent-None, or retired-workspace-default fallback.

A resolved scope default is pinned before child creation. Children do not independently consult the latest catalog to reinterpret the ancestor's Auto. A new schedule occurrence may resolve a newly selected definition only through the schedule's declared definition-update policy; an in-flight occurrence and its retries remain pinned.

### Meaningful Task Options Are Not Duplicate Publishing Controls

`pr-resolver.finishMode` distinguishes `merge` from `fix_only`. `fix_only` still remediates, pushes, verifies the current head, and checks the same gates. It does not mean None. The ordinary label should communicate **Merge when ready**, while the single publication control explains that Auto operates on the existing PR.

Review provider, verification enablement, merge method, discovery filters, and dry run remain meaningful capability inputs. They cannot grant effects that the publication policy forbids. A second generic publish-mode dropdown inside a Skill or Preset is not a meaningful task option.

### Explicit None

A new explicit `none` prohibits repository publication throughout the scoped tree, including a resolver child or an implicit merge-automation phase. It is not merely an instruction to skip the final managed publisher.

A capability whose successful objective requires a push or merge cannot execute under None unless it has a separately declared, genuinely non-publishing behavior compatible with that objective. The current PR-resolution objective therefore requires correction rather than automatic promotion or a false local-only success. `fix_only` is not that non-publishing behavior.

None does not prohibit local workspace work, local Git metadata, artifact capture, issue creation, Jira status changes, or child dispatch when those operations are independently declared and authorized. It is not a dry-run or universal read-only switch. A dry-run batch discovers and reports proposed children without enqueuing them; None can still enqueue compatible non-publishing children.

Artifact saving remains required under the workspace/result contract. None does not implicitly authorize a remote recovery push. A deployment that cannot preserve required work without an otherwise prohibited push must reject that unsupported execution before mutation, rather than discard work or evade None through a recovery branch. See the source/save and recovery contracts for the separately admitted durability mechanism.

## 4. Repository and Branch Context

### One Binding, Not Copied Defaults

The ordinary workflow has one canonical repository/source target and one authored branch context. Skills and presets receive projected arguments from that context at the trusted adapter boundary. A portable `repo` or `branch` CLI argument can remain available outside MoonMind without becoming another MoonMind form field.

Context-bound inputs are resolved before required-field validation and expansion. They are not independently persisted as authored overrides. A supplied conflicting copy is an error. Updating the repository or branch invalidates dependent target lookups, previews, and generated bindings. Reapply must not resurrect a stale value embedded in old generated instructions.

Binding declarations are semantic and type-checked as specified in [Input Schema Guidance](../Steps/InputSchemaGuidance.md). Never guess equivalence from a field name. An issue target, a comparison branch, a source repository, and a publication destination can represent genuinely distinct roles. Where such a distinction is supported, it is explicitly named, independently authorized, and not presented as a duplicate generic repository or branch control. Repository-independent and later-publication work retains the roles defined in [Repository Access and Workspace Design](../RepositoryAccessAndWorkspaceDesign.md).

### Branch Roles

| Work | Meaning of branch context |
| --- | --- |
| No publication | Starting branch for repository work, when a repository is used |
| Managed branch publication | The branch being worked on and updated |
| Managed PR publication | Authored starting/base branch; the PR head is generated or provider-owned |
| Existing-PR operation | Head and base are resolved from the selected PR target |
| PR-resolution batch | Each child uses its own discovered PR head and base |

A new request has one canonical authored branch in its repository/source target, not simultaneous `branch`, `startingBranch`, `targetBranch`, and Skill-input authorities. Legacy `task.git.branch` and older aliases are handled only by the versioned ingress/history boundary. They cannot compete with the canonical target in new authoring. The same rule applies to checkpoint creation, continuation, Edit/Rerun, and resolver target selection: [Checkpoint Branch System](CheckpointBranchSystem.md#89-execution-detail-output-branch) keeps source evidence, derived work branch, and verified output distinct; [PR Resolver integration](../Steps/SkillGithubPrResolver.md#41-inputs-skill-args) never treats the generic checkout branch as an implicit PR locator.

An omitted repository branch is resolved through the selected repository's authoritative default branch, never an unconditional `main` fallback. An explicitly selected base survives target discovery and fan-out. Discovery cannot replace `release/1.2` with the remote default merely because it fetched repository metadata.

For publication, that authoritative default is the repository default recorded by clone in `origin/HEAD`. Workspaces without that record resolve the remote's symbolic `HEAD` and persist it before publishing. Publication never assumes a default branch name, and retries retain the resolved selection even if the remote default changes. An unavailable default requires explicit branch selection.

For an existing PR, the ordinary target input is the PR reference. A branch-only lookup may be an alternative target locator, but must resolve exactly one eligible PR. Repository/head/base information then becomes resolved read-only target context. A conflicting explicit repository or PR reference is rejected. Missing, ambiguous, fork-only, or unsupported cross-repository targets fail or are truthfully skipped under the discovery contract; no guessing or fallback checkout is allowed.

A PR batch displays that branches come from the discovered PRs. Its coordinator's own checkout branch, if any, is not a destination for its children. Changing target-derived branch identity does not change the inherited publication policy.

### Work Branches and Candidates

For managed PR work the planner creates or obtains one stable work/head branch for the independent deliverable. The ordinary generated name is:

```text
{clean-title-prefix}-{uuid8}
```

The title prefix is lowercased, non-alphanumeric characters become `-`, and it is truncated to 40 characters. With no useful prefix, the UUID fragment alone is allowed. The selected head is persisted and reused on retries; a retry does not generate another branch or PR.

A previous publishing step may establish a verified cumulative candidate. A later step starts from that candidate when the plan calls for it but still compares against the original authored base. The candidate branch must never become its own publication base. Candidate-only clones and restored checkpoints refresh the exact remote base through the admitted publication authority before measuring commits. Unavailable base evidence blocks publication.

## 5. Workflow and Batch Inheritance

For a batch whose selected outcome is PR publication:

```text
Authored scope: Create pull requests
  Coordinator: no direct repository publication; records enqueue evidence
    Implementation child A: managed PR against the selected base
    Implementation child B: managed PR against the selected base
```

An intermediate coordinator forwards the same admitted scope intent. It does not forward its local `none`, drop merge-automation configuration, or consult a helper-local default. Child creation is validated server-side against the authenticated parent and pinned composition. Client-supplied parent IDs or copied JSON are not authority.

Each child retains independently inspectable execution, target, candidate, result, and cancellation state. Inheriting policy does not give all children the same PR head branch, merge their workspaces, imply atomic publication, or make coordinator enqueue success mean child success.

The scope follows declared child creation, including children produced by nested orchestration. A `dependsOn` edge to an independently authored workflow does not make that workflow inherit or change policy. An independently authorized new submission can establish a new scope, but a child helper cannot escape restrictions by claiming to be such a submission.

Static incompatibilities are rejected before launching the parent or creating tracker issues. Dynamic discovery resolves only actual eligible targets and validates each planned child before dispatch. If an external failure occurs after some children were created, preserve the exact created IDs, accepted policy/target evidence, skips, and errors. Do not claim an atomic rollback or successful whole batch.

### Parallel Branch Publication

Parallel independent children cannot all push to the same authored branch. Branch mode is unavailable for such a batch unless its declared execution contract supplies a serialized shared-branch handoff with fresh remote checks, exact expectations, and conflict handling. Do not silently generate per-child destination branches while claiming to update the selected branch.

PR mode is the ordinary independent-output batch model. Each child uses an isolated head and the shared authored base. Multi-repository batches are not implied by this simplification: the ordinary batch stays within the admitted repository context. A genuinely supported multi-repository operation requires an explicit typed target mapping and per-target admission, not hidden per-child repository overrides.

### Dependencies and Required Code Handoffs

Waiting for another workflow to complete is not evidence that its changes are available on the next child's starting branch. A composition whose children need predecessor code must declare its handoff: verified merge into the common base, verified candidate/checkpoint transfer, or a qualified serialized shared-branch sequence.

Changing PR-and-merge to PR-only or None cannot silently remove that handoff. Reject the incompatible choice before effects when known, or retain an explicit wait/blocker at the governed handoff. The publish selector does not manufacture stacked-PR or shared-workspace support. Dependency and merge-automation owners retain their existing lifecycle semantics.

## 6. Built-in Behavior Matrix

This matrix is the declarative target for catalog metadata, authoring preview, compiler output, and fan-out validation. Slugs identify coverage, not permission to add name-based branches in core code.

| Preset or Skill | Auto behavior | Context and restrictions |
| --- | --- | --- |
| `batch-workflows` with `skill:jira-verify` | Coordinator and children publish no code | Preserve Jira query, verification, and optional status-update controls. |
| `batch-workflows` with `preset:jira-implement` | Coordinator does not publish; each implementation child creates a PR | Inherit the selected repository and base, runtime, and resolved scope policy. |
| `batch-workflows` with `preset:jira-orchestrate` | Coordinator does not publish; each orchestrating implementation child creates a PR | The child is a repository-producing workflow, not automatically a non-publishing coordinator. |
| `batch-github-workflows` with either supported Implement or Orchestrate run | Coordinator does not publish; each eligible issue child creates a PR | Preserve the selected base; query real open Issue objects rather than fabricating every number in the range. |
| `github-issue-breakdown-implement` | Parent creates issues/dependent workflows; children create PRs | One policy replaces `publish_mode`; preserve ordered target and dependency evidence. |
| `github-issue-breakdown-orchestrate` | Parent creates issues/dependent workflows; children create PRs | Support the same PR-and-merge extension as its Implement sibling when the child's handoff contract is satisfied; do not merely extend the enum without the payload/lifecycle. |
| `jira-breakdown-implement` and `jira-breakdown-orchestrate` | Parent creates issues/dependent workflows; children use PR with merge automation | Preserve the declared merge default and display it. Alternative choices must preserve required predecessor-code handoffs. |
| `document-update-orchestrate` | Parent discovers documents; children use PR with merge automation | Discovery source and child workspace context agree; preserve ordering and dependency semantics. |
| `batch-pr-resolver` | Parent discovers and queues; children use Skill-owned Auto on existing PRs | No coordinator PR; each child has its own PR head/base. Preserve eligibility and fork protections. |
| `batch-dependabot-resolver` | Same existing-PR behavior for matching Dependabot PRs | Preserve conservative filters, caps, dry run, and per-PR/head deduplication. |
| `pr-resolver` | Skill-owned Auto repairs the selected PR and merges when its declared finish mode is `merge` | `fix_only` still pushes, never merges, and succeeds only at the clean gate. None is incompatible with this publishing objective. |
| `fix-comments`, `fix-ci`, `fix-merge-conflicts` | Skill-owned Auto for their declared existing-PR/branch effects | Preserve exact remote-head evidence and portable Skill semantics. |
| `pr-review-resolve` | Review/fix the existing PR; optional final merge is off by default | Coordinator publication is `none`; the existing review/merge owner invokes resolver work under the scope's admitted effects. Parent `none` is not a tree-wide user selection. |
| Standalone implementation presets, including Jira/GitHub Implement, Jira/GitHub Orchestrate, document author/update, and MoonSpec Orchestrate | Managed PR publication under their declared default | Read-only assessment, verification, or tracker steps remain non-publishing inside the same composition. |
| Standalone assessment, verification, or tracker-only work | No repository publication | Tracker effects and artifact saving have independent contracts. |

The main issue-batch Run selection changes Auto's recommendation without creating a second publish override. Repository and publish passthrough inputs disappear from MoonMind's ordinary forms and are supplied through context bindings at execution.

None, Branch, PR, and PR-and-merge are not universally supported options. A preset requiring a PR URL, a merged prerequisite, or a publishing resolver must reject choices that cannot fulfill that contract. Dry run remains a distinct discovery/dispatch option.

### Deduplication and Policy Identity

Retrying the same logical child submission reconciles the existing child before creating another. The parent's pinned intent and target derivation are checked on reuse. A matching idempotency key with a conflicting admitted policy or target cannot be reported as a newly accepted child under the changed settings.

Dependabot's cross-run identity remains based on repository, PR, and head SHA. An already admitted resolver at that identity is reported as existing/skipped with its actual policy. A schedule-policy edit does not create concurrent conflicting resolvers, mutate an old child, or silently change the meaning of the key. A separately authorized replacement requires an explicit lifecycle disposition through existing execution controls.

## 7. Publication Ownership

`MoonMind.UserWorkflow` owns compiled policy, authorization requirements, orchestration, and evidence-derived product outcome. Trusted publisher Activities/adapters own managed remote mechanics. Portable Skills own their declared Skill publication semantics.

The compiler derives `repositoryOperation` and required capabilities for each execution role. A batch coordinator does not receive repository-write credentials simply because descendants produce PRs. Nor can a read-only step remove the whole workflow's publication intent. Descendant write authority is admitted at the relevant child boundary.

One policy does not require one final push. A supported composition can include staged candidate publication, verification, an early PR handoff, and later tracker updates. Each effect has one declared owner and stable target. Arbitrary mixed publication owners, unrelated repositories, or incompatible outputs require a compatible declared composition or separate workflows, not user-authored per-step overrides.

### One Provider-Neutral Evidence Contract

All new managed and agent-owned repository publishers emit `moonmind.publish.repository.v1` as defined by [Lore VCS Integration Design section 3.13](LoreVcsIntegrationDesign.md#313-unified-repository-publication-evidence). That providing contract owns field names, provider-discriminated repository/branch/revision references, statuses/actions, `connectionRef`, `clientEvidence`, security scanning, and remote proof. This document consumes it and does not define a second payload shape.

Managed publication sets `owner = moonmind`; Skill-owned publication sets `owner = agent` with compiled mode `auto`. Owner changes effect responsibility, not evidence strength. Resolved `none` emits no repository-publication evidence and is not forced to fabricate a remote no-op. Coordinator/other side-effect artifacts remain separate objective evidence.

The unified artifact and its accepted result reference are bound through the existing terminal contract and artifact provenance to the exact workflow/run/Step Execution/attempt and immutable target. Validate that association before accepting the result. Restored or other-attempt artifacts are stale even if revision, Skill name, and remote state match. Do not recreate the retired schema's fields inside the new payload or let a self-asserted attempt identifier substitute for trusted artifact ownership.

Git and Lore use their provider-specific revisions. Successful publication and no-op require the canonical exact remote proof; Lore Content-only revisions are not No Commit because a generated Git diff is empty. PR outcome additionally requires the exact confirmed native PR or the authoritative mapped projection. Pending Lore projection stays `awaiting_external`; it is not PR success. Protected Lore merges remain coordinator-owned, never a GitHub merge against generated refs.

The shared portable writer, every built-in mutating Skill, managed publishers, terminal validators, workflow/gate/result consumers, and seeded connection/client evidence adopt this schema together. No producer can emit it without valid admitted `connectionRef` and `clientEvidence`. New writes of `moonmind.publish.auto.v1` and `acceptedRepositoryEvidence` are removed at that coordinated boundary. Frozen readers remain only for already-recorded histories and original digests, not as an alternate live fallback.

### Compiled `auto`: Skill-owned Publishing

Repository auto-publish capability is declared by resolved Skill metadata:

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

A Lore-aware Skill declares Lore support only when qualified. A matching name or built-in origin cannot confer support on a different resolved bundle.

The resolved Skill bundle is the semantic authority. Publishing mode never selects or authorizes a native substitute implementation. `pr-resolver` executes its resolved Skill bundle through the ordinary agent path. Native integration supplies substrate, policy, scheduling, and evidence validation, not a second resolver.

Compiled `auto` is valid only for a capability with declared agent-owned publishing and a satisfiable evidence contract. Managed Branch/PR choices are incompatible unless the capability explicitly supports that owner and objective. Absence of capability evidence cannot be repaired by a name-based fallback in the new contract.

The portable `artifacts/publish_result.json` path may remain the Skill output location, but its new-write payload is the unified schema above. MoonMind-local workspace authority binds that output into the current terminal contract. External-provider workspaces use their qualified provider-owned handoff for the same evidence and never receive a workspace-file contract they cannot satisfy. Missing, malformed, or stale evidence receives bounded continuation in the same authoritative workspace and Skill snapshot. Exhaustion remains failure, with preservation through the admitted recovery/save contract before destructive cleanup.

The portable helper remains `$MOONMIND_ACTIVE_SKILLS_DIR/_shared/publish_evidence.py`, with `.agents/skills` as the outside-MoonMind fallback. Its existing semantic operations, such as write-pushed, write-merged, write-no-op, write-blocked, write-failed, and from-pr-resolver-result, project provider-qualified observations and admitted immutable repository/connection/client context into the one schema. It cannot write a compliant new result from only a repository string, branch string, or old merge/push booleans, invent a connection, or copy the legacy payload with a changed schema label. The portable interface remains independent of Temporal/database imports; MoonMind supplies its context at the adapter boundary.

The canonical schema owns the full allowed status/action vocabulary, including review-request actions. Success still requires the Skill's objective-specific evidence. A verified push alone does not complete a resolver whose finish is merge; fix-only requires its distinct verified no-merge result.

| Evidence and required external state | Finish outcome |
| --- | --- |
| Authorized agent merge verified by the provider/Skill result and unified evidence | `PUBLISHED_PR`, owner agent, compiled mode auto |
| Verified exact branch publication without a required PR/merge outstanding | `PUBLISHED_BRANCH` |
| PR publication with confirmed exact native/mapped PR target | `PUBLISHED_PR` |
| Exact Lore publication with required projection pending | `awaiting_external`, `LORE_PROJECTION_PENDING` |
| Canonical `no_op_verified` and compatible completed objective | `NO_COMMIT`, not `PUBLISH_DISABLED` |
| Blocked or failed evidence | Publish-stage block/failure |
| Missing, invalid, or stale evidence | Publish-stage failure; `auto_publish_evidence_missing` remains applicable to missing required Skill-owned evidence, not a second schema |

The parent loads the unified `publishEvidence` artifact reference from the child result or its `outputRefs` and validates it through the shared reader before deciding the outcome. Auxiliary terminal-projection lag cannot replace verified remote success with a finalization failure. The parent never repeats the Skill's commit, push, or merge.

### Coordinator and Other Non-Repository Outcomes

Batch coordinators produce enqueue/target evidence such as `batch_pr_resolver_result.json`, `batch_dependabot_resolver_result.json`, `batch-workflows-result.json`, and `skill_outcome.json`. Their own compiled mode is `none`. They are not required to manufacture remote-head publication evidence.

Display their objective outcome as children queued, verified no targets, partial, or failed, separately from descendant publication progress. `PUBLISH_DISABLED` describes local publication disposition only where appropriate; it must not imply the whole batch was forbidden to publish or that child work completed.

### Managed Branch/PR Publication

Agents produce the candidate and semantic work description. The managed publisher performs authorized commit/push/PR mechanics deterministically, not through a prompt-only guarantee. The agent is not a second final publisher. Destination credentials are resolved through the admitted repository connection/role and are not recovered from ambient machine-level Git or `gh auth` state.

After successful provider-conditional publication, including an already-current remote revision, the publisher emits the same `moonmind.publish.repository.v1` artifact with owner moonmind. Its admitted target, original base revision, published revision, changes/scan refs, connection/client evidence, and exact remote proof are validated together. Consumers retain a run-owned reference to that accepted artifact and its target/candidate association, not a second `acceptedRepositoryEvidence` object or raw `push_*` metadata.

Before comparison, refresh the exact remote base. Use the provider's admitted compare-and-set or exact publication lease, then verify the live remote revision equals the candidate. An unavailable base, incompatible expected-tip movement, mismatching revision, or indeterminate comparison cannot produce accepted evidence or allow PR creation. Reconcile a lost acknowledgement before repeating an effect. Persist candidate and branch identity so retries do not generate new commits or duplicate PRs.

In managed `branch` publication, the shared Omnigent publication boundary passes the resolved authored branch as the publication destination for every harness. It must not generate a replacement job branch. Publication verifies fast-forward ancestry and uses an exact remote-tip lease; concurrent remote updates or branch deletion must fail publication rather than overwrite or recreate the branch. A retry whose local head already equals the selected remote head returns verified no-change evidence for that same branch.

Before pushing, reject hard-protected `main`, `master`, detached `HEAD`, and unknown Git branch states, as well as applicable repository/provider protection. For PR mode the separate work branch is pushed, never the authored base. A refused push is an explicit publication blocker, not successful publication or a mere warning that permits downstream handoff.

## 8. Pull Request Creation and Metadata

For GitHub, managed PR publication uses the admitted hosting API client and repository authority. A qualified CLI transport may implement the same operation under the same selected identity; it is not a credential fallback. Unsupported provider/transport/mode combinations fail before work depends on them.

The GitHub permission bundle includes Contents and Pull requests write access, plus Workflows write when changing `.github/workflows/*`. Readiness evaluation separately needs commit-status/check access and Issues read when the declared reaction fallback uses it. Permission observations do not themselves grant MoonMind authorization.

PR titles and bodies describe the implemented change, not orchestration mechanics. Agents propose the semantic title/body after seeing the final diff and verification. MoonMind validates and uses it through the authoritative publisher, records the confirmed PR identity, and passes that evidence downstream.

For Jira-backed work the title includes the canonical issue key, normally:

```text
<ISSUE-KEY> <implemented capability>
```

Examples:

```text
MM-597 Validate delivery records before submission
MM-489 Render shimmer band and halo layers
MM-398 Add Jira Orchestrate blocker preflight
```

The body includes the authoritative issue link, source/MoonSpec traceability when available, implemented behavior, verification verdict, tests, and remaining risks. Titles such as `Move Jira issue MM-597 to Code Review` or instructions from the first orchestration step are invalid implementation metadata.

An early PR handoff required by a later tracker transition remains managed publication under the same policy. Read-only/tracker steps do not independently publish. The handoff uses the completed candidate and validated metadata, records the confirmed PR before any dependent transition, and is not an instruction-level override of the user's policy.

Provider-native publication must preserve confirmed PR identity, head/base, readiness, and metadata through the same result contract. An unverified URL is not sufficient.

## 9. Merge Automation

For PR-and-merge intent, successful managed PR publication starts the existing parent-owned `MoonMind.MergeAutomation` child. The originating UserWorkflow remains `awaiting_external` while its readiness/review/resolver lifecycle runs. Dependencies requiring its completed objective are not released merely because the PR was opened.

The automation's resolver may compile to Skill-owned `auto` without changing the authored policy. It acts only on the admitted PR and within the selected merge/finish authority. An existing-PR review workflow adopts its resolved target instead of creating another PR. A direct resolver does not also get a competing managed publisher or duplicate merge-automation loop.

Detail payloads preserve the single authored selection and explain its effective behavior. Explicit PR-and-merge is displayed as `pr_with_merge_automation`; Auto remains Auto with its resolved explanation. Internal worker input stays `pr` plus automation configuration. No second editable `mergeAutomationSelected` flag is introduced. Active/terminal automation state belongs in the existing status object.

For Jira-backed work, preserve canonical `jiraIssueKey` in automation input. When present, the existing default post-merge Jira completion targets that key. An explicitly configured `postMergeJira.issueKey` is separately validated and overrides it. Never use fuzzy summary search or transition every key found in PR text. PR metadata is only a strict fallback when stronger authoritative context is absent.

An already-completed issue requires objective evidence on its freshly resolved completion target through the trusted transition boundary. The remote default is used unless an explicit completion target is configured. Initial assessment, clean worktrees, and pushed candidate commits cannot establish landing. The portable [acceptance policy](../../.agents/skills/moonspec-verify/references/acceptance-policy.md) owns these decisions; native publication and status owners validate its evidence contract and target identity.

Post-merge GitHub finalization validates the merge owner's tracked PR against a fresh GitHub read: repository, PR number, candidate head, issue reference, merged state, and the merge commit on the intended target must agree. This uses the existing merge gate's terminal evidence; an assessment or a PR URL alone cannot authorize completion. Explicit completion targets survive the merge handoff. Jira Review likewise requires a live, open, non-draft PR for the current issue and published candidate; required acceptance evidence must match that candidate's content and freshness.

## 10. MoonSpec Verification Gate

Publication eligibility uses the latest structured verification verdict and the run-owned accepted unified repository-publication artifact.

`FULLY_IMPLEMENTED` with valid subject/scope-bound objective evidence permits the policy's candidate publication. Issue completion additionally requires evidence on the intended completion target; each downstream side effect retains its own authority and postconditions. `ADDITIONAL_WORK_NEEDED` continues bounded remediation while budget remains. After exhaustion, a PR-authorized workflow may publish the prescribed draft handoff with remaining-work verdict/report, then fail with `attention_required: true` and skip promotion/trusted handoffs. A pushed branch or draft PR is not `no_commit` and does not make an incomplete objective successful.

A read-only verification step has no accepted publication evidence of its own. Inconclusive evidence at the stopping step defers to the atomic run-owned reference to validated `moonmind.publish.repository.v1` evidence and its exact candidate/target. Raw `pushStatus`, `branch`, or `headSha` from step metadata is not that evidence. A definitive authorization, contamination, or no-candidate refusal is not overridden by another projection.

The draft target is that same accepted published revision and, where applicable, its exact provider projection. Do not decide feasibility from one head and create the PR from mutable later step metadata. Refresh and comparison retain the original authored base.

`NO_DETERMINATION`, `BLOCKED`, and `FAILED_UNRECOVERABLE` block publication unless the existing contract explicitly models recoverable missing evidence. Malformed verdict envelopes are distinct from verifier judgments: inject the canonical verdict/action contract, retain a drifting raw action as diagnostics, derive the canonical action from the verdict, and use bounded corrective verification attempts before spending implementation-remediation cycles. Contract-invalid evidence downgrades fail-closed with a `downgradeReason` identifying the violating field.

A blocked gate records `publicationBlockedBy: "moonspec_verify"`, report refs, and `failureSummary.type = "moonspec_verification_gate"` in `reports/run_summary.json`, and skips downstream publication/Jira handoffs.

The existing environment-class draft option `workflow.moonspec_environment_blocked_publish_action` / `WORKFLOW_MOONSPEC_ENVIRONMENT_BLOCKED_PUBLISH_ACTION` defaults to `fail`. Its `draft_pr` setting can permit an annotated attention-required draft for the declared environment-class `BLOCKED` or malformed/degraded `NO_DETERMINATION` result. Verifier-declared `NO_DETERMINATION` and `FAILED_UNRECOVERABLE` remain fail-closed. This gate option can narrow or implement an already authorized PR policy; it cannot turn explicit None or Branch into a PR grant or enable merging after incomplete verification.

## 11. Runtime Instructions and Provider Boundaries

Instructions are derived from the compiled step role and policy. They explain authority but do not enforce it in place of admission, credentials, egress, and publisher controls.

For Skill-owned auto:

> Perform only the repository effects required by the resolved Skill and admitted finish policy. Produce current-attempt publish_result.json using moonmind.publish.repository.v1 and the Skill's objective-specific terminal evidence before reporting success.

For an explicit non-publishing scope:

> Do not push repository changes, create a PR, or merge. Preserve work and required artifacts through the admitted save contract. Independently declared tracker or dispatch operations remain governed by their own authority.

For a non-publishing coordinator:

> Do not publish a repository deliverable for this coordinator. Dispatch only admitted children using the inherited scope intent and record exact enqueue outcomes. Do not pass this execution's local none as the children's policy.

For managed Branch/PR work:

> Prepare and commit the candidate as the declared step requires. Do not independently push or create a PR. The trusted publisher owns those effects and records their verified outcome.

A provider-native runtime such as Jules can supply qualified publication mechanics through its adapter. The selected policy remains authoritative. A provider's automatic-PR feature cannot implement Branch by silently creating a PR, implement None by publishing, or manufacture evidence for an unsupported Skill-owned protocol. Provider-native outputs use the same exact-target/evidence requirements and never trigger duplicate local publishing. Unsupported combinations are rejected before execution; no new runtime-selection control or alternate billing route is introduced.

## 12. Historical Contracts and Reconstruction

Persisted histories keep their original bytes, digests, compiled behavior, and replay semantics. New authoring does not keep permanent per-step override aliases merely for convenience.

| Historical configuration | New-authoring reconstruction |
| --- | --- |
| Equal workflow and Skill repository/branch values | Collapse to the single context binding after validating equivalence. |
| Conflicting copies | Preserve evidence and surface a conflict. Never choose a winner silently. |
| Non-publishing batch parent plus child `pr` override | Reconstruct one authored PR intent with a derived non-publishing coordinator, when provenance proves that meaning. |
| Historical literal `auto` | Preserve Skill-owned meaning, never reinterpret as generic default. Fresh resolver authoring uses default/omission and an explicit PR target. |
| Legacy explicit None promoted to Auto | Replay under the recorded old contract. A new draft requires an explicit reviewed choice under the new contract. |
| Recorded omitted mode resolved through the old workspace fallback | Preserve the proven effective intent as an explicit new selection with provenance and validation; unknown origin requires review under the Settings retirement contract. |
| `moonmind.publish.auto.v1` or `acceptedRepositoryEvidence` | Frozen readers for already-recorded histories only. No new producer, live dual-reader fallback, or relabeling old evidence as a new attempt. |
| Mixed independent per-step publication policies | Preserve the historical execution. New authoring requires a compatible declared composition or separate workflows. |
| Legacy GitHub issue lifecycle shapes (`status: todo`, `status: claiming`, unversioned handoffs) | Retained history only. No new execution emits them; pending items follow the cutover drainage path owned by [GitHub Issue Legacy Cutover](GitHubIssueLegacyCutover.md). |
| Old starting/target branch pair | Reconstruct only when equivalent to one supported branch role; otherwise show an actionable reconstruction warning. |

Unknown provenance is not permission to infer intent. Read-only historical displays may show old fields, clearly labeled historical, without making them active inputs. Saved drafts, schedule definitions, reruns, and worker-version admission cross the same versioned boundary. Editing a schedule changes future admitted occurrences, not already queued children. Replay and failed-step recovery preserve the old admitted contract; an intentionally changed repository, branch, or publishing objective is newly admitted authoring with lineage.

## 13. Conformance

The production compiler, schema forms, preset expansion, child API, runtime adapters, and result projections must demonstrate the same behavior:

- One editable repository/branch/publishing source across Create, Apply/Reapply, unexpanded Submit, API, MCP, schedules, Edit/Rerun, continuation, and remediation authoring.
- Omission and explicit `default` produce equivalent admitted behavior and preview; authoring Auto never leaks as unresolved worker mode. Retired workspace/environment defaults cannot alter either path.
- Configured historical None/Branch/PR fallbacks retain proven effective intent or block for review, including unattended schedules and restored settings. Removal produces auditable disposition, not silent loss of operator choice.
- Main issue batches, both breakdown families, document fan-out, both PR batches, direct resolvers, and the review loop satisfy the matrix using actual production boundaries.
- A coordinator with a PR intent has local `none` and PR children, including through another coordinator. Explicit root None cannot be bypassed by a child or merge phase.
- Non-default bases survive issue discovery. PR batches resolve distinct head/base targets. Cross-repository, conflicting, stale, fork-only, and ambiguous targets retain safe dispositions. Recovery/resolver new writes reject old branch aliases.
- Read-only assessment/verification and tracker steps do not erase cumulative publication policy or candidate evidence. Conflicting publication owners fail before effects.
- Parallel shared-branch publication is rejected unless the declared serial handoff is proven. Dependency completion alone never substitutes for required predecessor-code availability.
- Retries, lost enqueue/push/PR acknowledgements, policy-conflicting idempotency reuse, and Dependabot schedule edits cannot duplicate effects or mislabel an existing child's authority.
- Git/Lore managed and Skill-owned publishers, shared helper, terminal validators, gate/result consumers, and connection/client bindings use the one new-write evidence schema. Old schemas are historical-only; missing connection/client/attempt/remote proof fails before acceptance.
- Publication evidence is exact-attempt and objective-specific. Coordinator enqueue evidence is not remote-head evidence, and neither is fabricated to make a gate pass. Lore Content-only publication and pending projection cannot become a false no-op or PR success.
- None, save failure, canceled/failed compute, and publication failure preserve the correct independent save/recovery outcome. No prohibited recovery push or destructive cleanup occurs.
- Historical reconstruction, mixed API/worker versions, stale defaults, and changed definition digests cannot silently change authority.

Source review and documentation coverage are not runtime, browser, or deployed conformance evidence. Required regressions belong in the existing selected test suites; protected live-provider qualification remains separately identified.
