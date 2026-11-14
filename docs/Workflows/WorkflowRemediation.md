# Workflow Remediation

**Document class:** Canonical declarative system / feature design.
**Status:** Normative for the journey described here.
**Owners:** MoonMind Platform and dashboard.
**Updated:** 2026-11-04

## 1. Purpose

Workflow Remediation starts an ordinary MoonMind workflow about one **target** run or a selected security finding. It uses saved evidence to diagnose and fix causes in the target repository or MoonMind. The original failure stays recorded. Repair is the remediation's result; recovering the target requires an operator retry.

[Run History and New Run Semantics](../Temporal/WorkflowRunHistoryAndNewRunSemantics.md#7a-retry-from-a-step) owns retry from a step, [Verification Cadence](RemediationVerificationCadence.md) owns repair attempts inside one workflow, and [Checkpoint Branches](CheckpointBranchSystem.md) own corrective branches.

## 2. Retry or remediate

| Situation | Use |
| --- | --- |
| Transient failure, or a prerequisite that has since been fixed | **Retry from failed step** using the target's retained inputs and progress |
| The failure needs diagnosis, a code or instruction fix, or a MoonMind change | **Remediate** |
| The task itself should change | **Edit** and rerun |

A remediation can recommend retrying the failed step after its fix lands. **Continue remediation** is different: it resumes an admitted verifier repair loop with saved remaining work. It is unavailable when the target has no such admitted stopping point.

## 3. Architecture

- **A remediation is a `MoonMind.UserWorkflow`.** It uses normal admission, steps, artifacts, controls, publication, and Omnigent execution. There is no remediation runtime or scheduler.
- **The relationship is a link, not a dependency.** `execution_remediation_links` records the remediation workflow, the target workflow and pinned run, mode, authority mode, status, and context artifact. Unlike `dependsOn`, it can point at a run that stays failed. A link grants no permission and is not transitive.
- **Any visible run can be a target.** The link's target key references the execution projection (`temporal_executions`). Runs started by Temporal Schedules have no API source row, only that projection, and are remediable like API-created runs.
- **Evidence is delivered, not fetched.** An Omnigent agent has no MoonMind API or MCP access, so the evidence it needs is embedded in the remediation context and placed in its workspace.
- **Observation is separate from control.** Logs, diagnostics, issue text, and agent output are untrusted evidence. Retrying, canceling, or editing the target stays with the operator.

## 4. Creating a remediation

On Overview, Steps and Artifacts, eligible workflows offer **Remediate** in **Workflow actions**. It opens editable Create with the pinned target workflow and run, repository, branch, evidence selection, Runtime, Profile, publication (`pr` with a repository, otherwise save-only), the `remediate-workflow` skill and instructions. Opening it starts no run; submission retains ordinary admission and authority checks. The tab-scoped draft preserves the authored submission for reload and lost-acknowledgment reconciliation until creation, discard or its original two-hour expiry. Missing, altered, cross-tab or expired drafts are refused rather than partially submitted.

Create authors and explains an observe-only snapshot. The run can neither follow live logs nor request target actions, so Create offers no follow mode, authority, action policy, or action toggles. An older unsubmitted draft reloads as this diagnosis; an attempted submission replays unchanged.

Choose the repository for the fix you want: the target's repository to repair the target's work, or `MoonLadderStudios/MoonMind` to change MoonMind so the failure does not recur. One remediation works in one repository.

Submission is ordinary `POST /api/executions` with a `workflow.remediation` object, for example:

```json
{
  "target": {"workflowId": "mm:target", "runId": "target-run"},
  "mode": "snapshot",
  "authorityMode": "observe_only",
  "trigger": {"type": "manual"}
}
```

`POST /api/executions/{workflowId}/remediation` is a convenience route through the same admission. Admission rejects a target that is missing or not a workflow id, the remediation itself, a target that is not a `MoonMind.UserWorkflow` or is itself a remediation, a `runId` other than the target's current run, step selectors or AgentRun ids that do not belong to the target, unsupported modes or policies, and `admin_auto` authority, which stays closed. The modes are `snapshot` and `snapshot_then_follow`; only the operator follow API can follow a live target.

## Security finding handoff

The source workflow's Artifacts tab lists retained security comparisons and validated agent reviews. Selecting a finding or hypothesis opens ordinary Remediation Create with the comparison/finding or review/hypothesis identities pinned, `security-investigate` selected, and publication disabled by default. Successful assessment execution can still contain a vulnerability. The collection links selected findings back to source evidence; remediation detail shows the admitted comparison beside the existing candidate/checkpoint and publication history.

Admission verifies the selected evidence's exact source workflow/run, readable artifact dependencies and content seal. Agent reviews retain the original candidate, hypothesis, examined subject, threat model, limitations and linked retests; review coverage does not certify a fix. It records original finding evidence, scope, comparison state, decisions, review/expiry and changed intelligence in the existing context. Browser-authored dispositions cannot replace this snapshot. Draft reloads and authored retries preserve the selection; normal checkpoint recovery preserves candidate work and publication reconciliation owns uncertain effects.

Assessment scope is evidence, not remediation authority. Source edits and PR publication use their separate admission; infrastructure-as-code follows the ordinary code path, while direct infrastructure mutation is unsupported. Lifecycle completion and successful CI do not imply `remediated_verified`. Retests remain with the existing security comparison owner, which distinguishes incomplete coverage, failed/inconclusive retests, source/environment mismatch, upstream closure and changed intelligence. The handoff introduces no additional repair loop or resource controller.

## 5. Remediation context

Admission builds `reports/remediation_context.json` before the remediation starts. The artifact is restricted, linked to the remediation run as `remediation.context`, and recorded on the link. It contains:

- `target`: workflow id, pinned run, title, summary, and state;
- `failureEvidence`: from the target's finish summary, its `failure`, `failureSummary`, `finishOutcome`, `lastStep`, `operatorSummary`, `publish`, and manifest summaries; its `authoredWork` (repository, instructions, and steps); its step ledger as `steps`, with each step's status, summary, and last error; and `documents` embedding the recovery manifest, incident reconstruction, and failed-step diagnostics;
- `failureEvidence.unavailable`: evidence that could not be read or embedded, with the reason;
- the selected steps, evidence references, live-follow availability for the operator follow API, and the policies the remediation was admitted with.

The context is bounded (64 KB per embedded document, 4,000 characters per string, 60 steps) and redacted. A failed step-ledger query or artifact read is recorded as unavailable evidence; it does not fail the remediation. The target's evidence is read with the target workflow's artifact scope, since its lifecycle manifests are owned by its workflow principal.

Both direct and supervised Omnigent execution add the admitted context to the step's required input refs before dispatch; a missing context stops new work. Cleanup and confirmed results remain readable without fresh input resolution. The host materializes the context under `.moonmind/attachments/`, excluded from Git, and the first message lists the paths. The task-input snapshot is sealed before the context exists, so the remediation link records which context belongs to the run.

## 6. The remediate-workflow skill

The [`remediate-workflow`](../../.agents/skills/remediate-workflow/SKILL.md) skill reads the context, classifies the cause as target work, MoonMind, or transient, and then:

- changes nothing for a transient failure and recommends retrying the target from the named step;
- fixes target work in the target repository, with a test that would have caught it;
- fixes MoonMind in a MoonMind workspace, reproducing the failure in a focused test first;
- proposes a concrete fix, without editing unrelated code, when the cause lives in another repository.

It ends with the cause and its evidence, what changed and how it was verified, and the operator's next step.

## 7. Results

The skill records an execution-bound `artifacts/remediation-result.json` verdict before publication. A blocked or failed diagnosis, or missing/invalid outcome evidence, fails the remediation and prevents new automatic publication. The generic runtime can repair incomplete evidence with bounded same-session turns; the canonical supervisor and legacy coordinator fail closed. Confirmed earlier publication remains recorded. An evidence-backed transient diagnosis or concrete proposal can succeed without code changes. Provider turn completion alone does not establish success.

The remediation's publication, report, and link status describe its own result. A prevention pull request does not prove the target is repaired; the target stays failed until the operator retries or reruns it. Target detail lists inbound remediations, and remediation detail shows its target.

## 8. Authority and safety

- `observe_only`, which Create authors, allows diagnosis and repository work without typed remediation actions. `approval_gated`, authored only through the API, lets an operator request typed actions, each needing a recorded approval; the remediation run gets no tools. `admin_auto` is closed until a separately authorized implementation exists; no draft, schedule, model response, or test enables it.
- Evidence is redacted before it enters the context, and remediation artifacts are restricted. A remediation receives no raw host shell, Docker socket, SQL, storage keys, or secret reads.
- A remediation never retries, cancels, or edits its target, and a link never widens what the remediation may read or change.

## 9. Typed actions, approvals, and verification

MoonMind also keeps typed remediation operations: an action registry (`remediation_actions.py`), evidence tools served as MCP tools (`remediation_tools.py`), persisted operator approvals bound to exact intent and single use, exclusive target mutation locks, post-action verification bound to the exact result (`remediation_verification.py`), and lifecycle artifacts written by `RemediationLifecyclePublisher`. Omnigent remediation agents do not receive MoonMind MCP tools, so these operations are not part of the Omnigent journey above; the dashboard's approval endpoints still decide recorded requests. Workflow Detail labels requestable kinds **Operator API actions**, offered only for links with an action policy.

## 10. Acceptance

Tests exercise admission for API-created and schedule-started targets, the context's embedded evidence and bounds, the context's delivery into the Omnigent request, observe-only Detail → Create → submit with the draft's skill and repository defaults, and the operator-API action projection. Documentation changes are reviewed, not unit-tested.
