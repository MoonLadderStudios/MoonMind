# Workflow Remediation

**Document class:** Canonical declarative system / feature design.
**Status:** Normative for the journey described here.
**Owners:** MoonMind Platform and dashboard.
**Updated:** 2026-10-17

## 1. Purpose

Workflow Remediation starts an ordinary MoonMind workflow about one failed **target** run. The remediation finds out why the target failed from the evidence the target left, fixes the cause where it is safe to, and says how to keep the failure from recurring, either in the target's repository or in MoonMind itself. The target's failure stays recorded as it happened: a repair shows up as the remediation's own result and, once the operator retries the target, as the target's new run.

[Run History and New Run Semantics](../Temporal/WorkflowRunHistoryAndNewRunSemantics.md#7a-retry-from-a-step) owns retry from a step, [Verification Cadence](RemediationVerificationCadence.md) owns repair attempts inside one workflow, and [Checkpoint Branches](CheckpointBranchSystem.md) own corrective branches.

## 2. Retry or remediate

| Situation | Use |
| --- | --- |
| Transient failure: provider outage, rate limit, timeout, lost host | **Retry from failed step** on the target; nothing needs to change |
| The failure needs diagnosis, a code or instruction fix, or a MoonMind change | **Remediate** |
| The task itself should change | **Edit** and rerun |

A remediation often ends by recommending a retry: once its fix lands, retrying the target from the failed step re-executes only the work that failed.

## 3. Architecture

- **A remediation is a `MoonMind.UserWorkflow`.** It uses normal admission, steps, artifacts, controls, publication, and Omnigent execution. There is no remediation runtime or scheduler.
- **The relationship is a link, not a dependency.** `execution_remediation_links` records the remediation workflow, the target workflow and pinned run, mode, authority mode, status, and context artifact. Unlike `dependsOn`, it can point at a run that stays failed. A link grants no permission and is not transitive.
- **Any visible run can be a target.** The link's target key references the execution projection (`temporal_executions`). Runs started by Temporal Schedules have no API source row, only that projection, and are remediable like API-created runs.
- **Evidence is delivered, not fetched.** An Omnigent agent has no MoonMind API or MCP access, so the evidence it needs is embedded in the remediation context and placed in its workspace.
- **Observation is separate from control.** Logs, diagnostics, issue text, and agent output are untrusted evidence. Retrying, canceling, or editing the target stays with the operator.

## 4. Creating a remediation

**Remediate** on a failed, stuck, or intervention-required workflow opens Create with the pinned target workflow and run, repository, branch, Runtime, Profile, publication (`pr` with a repository, otherwise save-only), the `remediate-workflow` skill and default instructions. Everything remains editable before submit. The tab-scoped draft saves the authored submission so reload can reconcile a lost acknowledgment without duplicating the repair. It lasts until confirmed creation, discard or its original two-hour expiry; missing, altered, cross-tab or expired drafts are refused rather than partially submitted.

Choose the repository for the fix you want: the target's repository to repair the target's work, or `MoonLadderStudios/MoonMind` to change MoonMind so the failure does not recur. One remediation works in one repository.

Submission is ordinary `POST /api/executions` with a `workflow.remediation` object, for example:

```json
{
  "target": {"workflowId": "mm:target", "runId": "target-run"},
  "mode": "snapshot_then_follow",
  "authorityMode": "observe_only",
  "trigger": {"type": "manual"}
}
```

`POST /api/executions/{workflowId}/remediation` is a convenience route through the same admission. Admission rejects a target that is missing or not a workflow id, the remediation itself, a target that is not a `MoonMind.UserWorkflow` or is itself a remediation, a `runId` other than the target's current run, step selectors or AgentRun ids that do not belong to the target, unsupported modes or policies, and `admin_auto` authority, which stays closed.

## 5. Remediation context

Admission builds `reports/remediation_context.json` before the remediation starts. The artifact is restricted, linked to the remediation run as `remediation.context`, and recorded on the link. It contains:

- `target`: workflow id, pinned run, title, summary, and state;
- `failureEvidence`: from the target's finish summary, its `failure`, `failureSummary`, `finishOutcome`, `lastStep`, `operatorSummary`, `publish`, and manifest summaries; its `authoredWork` (repository, instructions, and steps); its step ledger as `steps`, with each step's status, summary, and last error; and `documents` embedding the recovery manifest, incident reconstruction, and failed-step diagnostics;
- `failureEvidence.unavailable`: evidence that could not be read or embedded, with the reason;
- the selected steps, evidence references, live-follow availability, and the policies the remediation was admitted with.

The context is bounded (64 KB per embedded document, 4,000 characters per string, 60 steps) and redacted. A failed step-ledger query or artifact read is recorded as unavailable evidence; it does not fail the remediation. The target's evidence is read with the target workflow's artifact scope, since its lifecycle manifests are owned by its workflow principal.

For an Omnigent remediation run, the plan-bound request adds the context artifact to the step's input refs. The host materializes it under `.moonmind/attachments/`, which is excluded from Git, and the first message lists materialized input paths when the host reports them. The task-input snapshot is sealed before the context exists, so the remediation link, not the snapshot, records which context belongs to the run.

## 6. The remediate-workflow skill

The [`remediate-workflow`](../../.agents/skills/remediate-workflow/SKILL.md) skill reads the context, classifies the cause as target work, MoonMind, or transient, and then:

- changes nothing for a transient failure and recommends retrying the target from the named step;
- fixes target work in the target repository, with a test that would have caught it;
- fixes MoonMind in a MoonMind workspace, reproducing the failure in a focused test first;
- proposes a concrete fix, without editing unrelated code, when the cause lives in another repository.

It ends with the cause and its evidence, what changed and how it was verified, and the operator's next step.

## 7. Results

The remediation's result is its own: its publication (for example a pull request), its report, and its link status. A prevention pull request does not prove the target is repaired, and the target stays failed until the operator retries or reruns it. Target detail lists inbound remediations, and remediation detail shows its target.

## 8. Authority and safety

- `observe_only` allows diagnosis and repository work without typed remediation actions. `approval_gated` allows typed actions only with a recorded operator approval. `admin_auto` is closed until a separately authorized implementation exists; no draft, schedule, model response, or test enables it.
- Evidence is redacted before it enters the context, and remediation artifacts are restricted. A remediation receives no raw host shell, Docker socket, SQL, storage keys, or secret reads.
- A remediation never retries, cancels, or edits its target, and a link never widens what the remediation may read or change.

## 9. Typed actions, approvals, and verification

MoonMind also keeps typed remediation operations: an action registry (`remediation_actions.py`), evidence tools served as MCP tools (`remediation_tools.py`), persisted operator approvals bound to exact intent and single use, exclusive target mutation locks, post-action verification bound to the exact result (`remediation_verification.py`), and lifecycle artifacts written by `RemediationLifecyclePublisher`. Omnigent remediation agents do not receive MoonMind MCP tools, so these operations are not part of the Omnigent journey above; the dashboard's approval endpoints still decide recorded requests.

## 10. Acceptance

Tests exercise admission for API-created and schedule-started targets, the context's embedded evidence and bounds, the context's delivery into the Omnigent request, and the Create draft's skill and repository defaults. Documentation changes are reviewed, not unit-tested.
