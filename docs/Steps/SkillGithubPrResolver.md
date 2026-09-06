# Managed Agent Skill: Github PR Resolver Technical Design

**Document Class:** Canonical declarative  
**Viewpoint:** Module Contract Specification  
**Status:** Draft  
**Owners:** MoonMind Engineering  
**Updated:** 2026-09-06  
**Audience:** Workflow/runtime authors, dashboard and API contributors, and operators  
**Authority:** MoonMind authoring, target binding, runtime handoff, and result integration for PR Resolver. Resolver decisions and procedures belong to the immutable portable Skill bundle, not this integration document.  
**Owning Surface:** PR Resolver admission and ordinary Skill execution/result boundaries  
**Related Docs:** [Workflow Publishing](../Workflows/WorkflowPublishing.md), [Input Schema Guidance](InputSchemaGuidance.md), [Skill System](SkillSystem.md), [PR Merge Automation](../Workflows/PrMergeAutomation.md), [Lore VCS Integration Design](../Workflows/LoreVcsIntegrationDesign.md), [Executions API Contract](../Api/ExecutionsApiContract.md)  
**Related Implementation:** `.agents/skills/pr-resolver/`, `.agents/skills/_shared/publish_evidence.py`, `moonmind/services/skill_step_inputs.py`, `tests/unit/test_pr_resolver_tools.py`.

This document describes target integration behavior. It does not change the executable Skill or claim that current deployments already implement the new authoring/evidence contract.

## 1. Purpose

MoonMind invokes **PR Resolver** through an ordinary `MoonMind.UserWorkflow` and resolved Skill execution in `MoonMind.AgentRun`. The user selects an explicit PR target and the workflow's single publishing policy. The portable Skill collects PR/CI/comment evidence, selects its specialized Skills, and completes the admitted merge or fix-only objective.

The Skill markdown owns its loop and packaged helpers own snapshot collection and merge gating. MoonMind supplies runtime isolation, admission, scheduling, credentials, and validated artifact/result handoffs. It does not run a second semantic resolver.

The checked-in Skill is portable. Its provider-neutral models, normalization, classification, transition, and evidence rules live in `pr_resolver_core`, while provider data collection and command execution live in the Skill bundle. The same resolved bundle is used in direct Codex and MoonMind-managed runs. The core performs no network, filesystem, process, credential, clock, or Temporal operations.

---

## 2. Assumptions and Constraints

* The runtime receives the immutable active Skill set at `$MOONMIND_ACTIVE_SKILLS_DIR`; a conflict-free `.agents/skills` alias is optional and never shadows that set.
* The selected repository/provider and required GitHub operations are independently admitted. An available global token or existing checkout is not target or merge authority.
* Supporting specialized Skills are resolved in the same immutable Skill closure before launch; missing required Skills fail closed.
* New dashboard/API authoring uses `task.publish.mode = default` or omission, displayed as Auto. Trusted compilation resolves the declared Skill-owned publisher and emits worker-facing `publishMode = auto` with owner agent. A new request never uses an old ingress decoder to make authored literal `auto` valid.
* Explicit None is incompatible with the resolver's push/merge objective. `fix_only` still remediates and pushes and does not make None compatible.
* Publishing ownership and implementation hosting are independent. Compiled Auto does not authorize native Temporal replacement of the Skill.
* New publication evidence uses `moonmind.publish.repository.v1` for managed and Skill-owned operations alike. Old `moonmind.publish.auto.v1` and `acceptedRepositoryEvidence` are frozen historical-read contracts only.

---

## 3. Skill packaging

### 3.1 Layout

Shared repo skill mirror:

```text
.agents/skills/pr-resolver/
  SKILL.md
  bin/
    pr_resolve_snapshot.py
    pr_resolve_contract.py
    pr_resolve_finalize.py
    pr_resolve_full.py
    pr_resolve_orchestrate.py
  schemas/
    pr_resolver_snapshot.schema.json
    pr_resolver_result.schema.json
```

`SKILL.md` and the packaged files form the portable implementation. This integration document is not an executable replacement for them. MoonMind materializes the resolved bundle through its ordinary agent runtime.

### 3.2 Skill-owned host contract

The portable contract declares `implementation.contract = pr-resolver-core/v1`, supports the cli host, and is not native-host eligible. Built-in, deployment, repository, and local sources execute their exact immutable resolved content. A known name/provenance never authorizes replacement with MoonMind.PRResolver or GitHub-adapter readiness logic.

The former native resolver remains registered only for histories that already recorded it. The existing run-pr-resolver-skill-owned-execution-v1 cutover keeps new executions on the ordinary agent path.

### 3.3 Required Runtime Capabilities

MoonMind provides governed repository and GitHub capabilities, isolated workspace, current admitted credentials, timeout/cancellation supervision, and artifact storage. The portable Skill initiates its own semantic reads/remediation/merge through the supported boundary; integration Activities do not duplicate its classification or loop.

Provider support is explicit in resolved metadata. A GitHub-only resolver is not automatically eligible for a Lore-authoritative source merely because that source has a generated GitHub PR. Qualified Lore work follows provider-authoritative revision, projection, and coordinator merge rules in LoreVcsIntegrationDesign, never a push/merge to generated GitHub refs.

---

## 4. Skill Interface

### 4.1 Inputs (Skill Args)

New MoonMind submissions supply one explicit structured PR locator through the selected Skill's task inputs. The ordinary `pr` input accepts a PR number, URL, or supported explicitly entered head-branch locator. Where a portable schema exposes a separate `branch` alternative, the UI treats it as an alternative locator, not a simultaneous generic checkout setting. Conflicting locators fail before mutation.

`repo` is projected from the single admitted repository/target context, not another editable Skill repository. PR resolution verifies locality/authorization and derives the exact head/base identities. It never infers a target from free-text instructions, the repository's default branch, or an arbitrary non-default checkout branch.

`git.startingBranch`, `git.branch`, `startingBranch`, and `targetBranch` are not PR-selector inputs in new MoonMind authoring. Old payloads using them are accepted only by the frozen decoder for already-recorded histories. A reconstructed new draft needs proven target provenance or explicit selection; it cannot revive the old fallback chain. These restrictions do not remove standalone portable CLI arguments outside MoonMind.

| Arg | Type | Default | Meaning in the supported interface |
| --- | --- | --- | --- |
| `repo` | string or null | Portable outside-MoonMind default only | `owner/repo` projected by MoonMind from admitted context. Standalone CLI behavior remains owned by the Skill. |
| `pr` | string or null | Required locator in new MoonMind authoring | Explicit PR number, URL, or a supported head-branch locator resolved to one eligible PR. |
| `branch` | string or null | null | Alternative portable PR locator when pr is absent; never an additional workflow checkout/publish branch. |
| `mergeMethod` | enum | squash | merge, squash, or rebase where supported. |
| `reviewProvider` | string | empty | Provider-neutral automated reviewer; configured provider semantics remain Skill-owned. |
| `requireFreshReview` | bool | false | Require current-head review under the selected provider contract. |
| `finishMode` | enum | merge | merge or fix_only. Only omission takes the default; invalid values fail without granting merge authority. |
| `maxIterations` | int | 5 | Bounded Skill loop. |
| `finalizeMaxRetries` | int | 60 | Orchestration retries, including finalize waits/remediation. |
| `finalizeBackoffSeconds` | int | 30 | Initial bounded exponential-backoff delay. |
| `finalizeMaxSleepSeconds` | int | 120 | Per-sleep cap. |
| `finalizeMaxElapsedSeconds` | int | 7200 | Skill orchestration wall-clock cap. |

The Skill enforces its loop values. Runtime timeout/intervention remains an outer envelope, not another implementation of retry semantics. In a scoped child, target and finish inputs are derived from the admitted parent contract and cannot broaden it.

### 4.2 Outputs

Snapshot and result projections may be stored as `artifacts/pr_resolver_snapshot.json` and `artifacts/pr_resolver_result.json`. The portable terminal result at `var/pr_resolver/result.json` and publication output at `artifacts/publish_result.json` supply objective and repository evidence respectively.

The latter's new-write payload is `moonmind.publish.repository.v1`, as owned by [Lore VCS Integration Design section 3.13](../Workflows/LoreVcsIntegrationDesign.md#313-unified-repository-publication-evidence). It includes the canonical provider/revision, admitted connection/client, action/status, scan, and exact remote proof. The shared writer and validators move together; renaming an old payload's schema label is not conversion. The output path can remain stable without retaining a parallel schema.

The existing terminal-contract/artifact ownership boundary binds those refs to the exact current workflow/run/Step Execution/attempt and resolved target. A restored old artifact, process exit, or assistant claim is not current completion evidence.

The resolver's own result includes target identity, actions, merge/blocked disposition, and mergeAutomationDisposition: merged, already_merged, review_clean, reenter_gate, request_review, manual_review, or failed. These semantic fields do not become duplicate fields in the provider-neutral publication payload.

`reenter_gate` is a handoff to the actual enclosing MergeAutomation owner, not evidence the PR is resolved. Skill-authored gated-continuation/v1 includes the retry deadline. The workflow waits on that contract without reimplementing review/CI semantics. Standalone or parent-mismatched handoffs fail closed; detached polling after agent exit is unsupported.

New handoffs bind owner workflow/run/type, resolver child workflow/run, step execution reference, and head SHA. The direct finalizer preserves the existing review-grace expiresAt as notBefore instead of restarting it. Recorded legacy untimed evidence uses only its frozen fallback and reports legacy_continuation_fallback_used.

`request_review` uses gated-continuation/v2 with configured provider, exact head, step reference, and progress signature, never arbitrary request text. The validated parent maps that provider to its registered command, posts through the idempotent review-request Activity, and owns the durable external wait.

`review_clean` is terminal success only for fix_only at the same clean gate that would allow the admitted merge finish. Fixes may have been pushed, but no merge was performed. The unified publication evidence must prove the exact remote revision and a permitted non-merge action; the resolver's own result and live PR observation prove the target remains unmerged. Do not require the retired schema's merged boolean in the new repository evidence. Contradictory merge claims or unavailable remote/no-merge proof block with the established UNAUTHORIZED_MERGE_EVIDENCE or applicable evidence diagnostic. Structured results distinguish review_clean from merged even when both exit zero.

### 4.3 Automated review evidence

The portable Skill, not MoonMind's UI or scheduling gate, owns automatedReview freshness classification for the current head. Its result captures freshReviewForHead, requestPending, request/comment and completion identities/times, and progressSignature under the resolved Skill contract.

An older-commit review is not fresh for a new head. ProgressSignature includes head plus sorted actionable/deferred comment IDs so the parent can enforce the declared no-progress handoff without implementing the Skill's comment classification.

---

## 5. Data Collection (Snapshot)

The resolved bundle owns PR metadata, complete comment/review/thread collection, CI/check state, and rereads after changes. It resolves supporting helpers from the same immutable active Skill set. The canonical procedures and query fields live in that bundle, not a native integrations clone of the resolver.

MoonMind may validate initial target identity and read compact external scheduling state under its own typed contracts. Those checks do not replace the Skill's fresh semantic snapshot or authorize a merge.

---

## 6. Decision Engine

Blocker ordering, specialized Skill delegation, review freshness, retries, no-progress decisions, and final merge checks are defined once in `SKILL.md` and the portable helpers. This document does not prescribe a second decision algorithm. Missing required supporting Skills fail closed; MoonMind does not substitute a general-purpose native repair path.

---

## 7. Fix Execution Strategies (Instruction Composition)

MoonMind launches the ordinary resolved Skill execution with its exact closure and target. The agent follows the selected supporting Skill, such as fix-merge-conflicts, fix-ci, or fix-comments, from `$MOONMIND_ACTIVE_SKILLS_DIR`. A repository-owned `.agents/skills` directory cannot shadow the active snapshot.

Specialized repair logic and its remote effects remain Skill-owned within the admitted publication/finish policy. Changing the generic workflow branch or inserting a nested publish_mode input cannot retarget those effects.

---

## 8. Merge Behavior

The portable finalizer refreshes and verifies the exact PR head, current checks/reviews, and its full resolved merge contract before any admitted merge. MoonMind's gate supplies scheduling readiness and durable ownership, not an alternative merge authorization.

Fix-only withholds merge and proves the unmerged result. None rejects the publishing objective instead of running it locally and claiming success. Validated unified repository evidence and the resolver's objective result are both required.

---

## 9. Dashboard integration

### 9.1 New authored UserWorkflow request

The following excerpt belongs inside a normal create request with the canonical provider-discriminated repository/source target and runtime selection. It intentionally omits those providing schemas rather than reintroducing a string repository or task.git alias:

```json
{
  "task": {
    "instructions": "Resolve the selected PR using the admitted finish policy.",
    "skill": {
      "name": "pr-resolver",
      "inputs": {
        "pr": "123",
        "mergeMethod": "squash",
        "finishMode": "merge"
      }
    },
    "publish": { "mode": "default" }
  }
}
```

Omitting publish.mode is equivalent to default. The selected transport's normal Tool/Skill selector normalization still applies; this excerpt does not introduce an alternative API envelope. It supplies an explicit task target and no independent repository/branch override.

The backend pins the definition and target, resolves context bindings, checks scope/finish compatibility and provider support, and emits compiled worker `publishMode = auto`. Only that trusted output uses auto. New authored literal auto or a copied compiler payload cannot be accepted by pretending it is historical.

A batch child receives its parent's frozen publishing/finish intent and validated per-PR target, not the coordinator's local None, a workspace default, or a freshly interpreted child recommendation. The single UI control explains whether fixes will be pushed and whether merging is enabled.

---

## 10. Observability and Artifacts

Project actual target, semantic disposition, exact current-attempt publication evidence ref, gate handoff, and safe diagnostics. Preserve the difference between pushes, no-op, review-clean, merge, blocked, and continuation. A PR URL is a target/reference, not proof a merge happened.

Authored Auto, compiled Skill-owned Auto, and observed outcome remain separate. An adopting coordinator's None is not displayed as user prohibition of its descendants. None is not labelled dry run.

---

## 11. Security Gates

The resolved Skill's workspace and merge guards remain mandatory. MoonMind enforces target locality, current credentials, publication scope, provider compatibility, bounded runtime, exact-attempt evidence, and supported confinement before accepting effects/results.

Restoring a checkpoint cannot restore old target-selection aliases or expired grants. New repository evidence requires actual admitted connection and client provenance. Historical readers cannot be used for fresh outputs, and no GitHub write to a generated Lore projection is a supported fallback.

---

## 12. Canonical Skill Instructions

`.agents/skills/pr-resolver/SKILL.md` and its packaged portable files are the executable semantic authority. Changes to resolver behavior begin there. This document owns only MoonMind integration with that behavior and references the publication, target, and evidence contracts provided by their owning modules.

---

## 13. Verification

The production authoring/target/compiler/AgentRun/result boundaries must prove:

- The resolved cli Skill runs without selecting the historical native resolver; supporting files come from the active immutable set.
- New default and omitted publication requests resolve identically to compiled auto, while explicit None and fresh authored auto fail under the appropriate new contract.
- An explicit PR locator is required. Legacy git.startingBranch/git.branch and checkout context cannot select a PR for new requests. Ambiguous/conflicting/fork-only unsupported targets fail before effects.
- Repository is bound once, and each batch child uses the actual selected PR's head/base under the frozen scope.
- Managed and Skill-owned publishers/readers share moonmind.publish.repository.v1, actual connection/client evidence, and exact-attempt ownership. Legacy formats are frozen historical readers only.
- Fix-only proves no merge without relying on a retired publication boolean. Merge-required work cannot complete on a push or ungated continuation.
- The existing snapshot/Skill tool tests and integration journey exercise real handoffs, not only a schema example or wrapper exit.

Historical recorded payloads retain original bytes, digests, and replay interpretation. Reconstructing a new resolver draft uses reviewed target/default provenance or requires explicit selection rather than perpetuating old authoring aliases.
