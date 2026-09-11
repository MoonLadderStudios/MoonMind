# GitHub Issue Legacy Cutover

**Document Class:** Canonical declarative  
**Viewpoint:** System / Feature Design View  
**Status:** Accepted  
**Owners:** MoonMind Platform + Workflow Runtime + GitHub Integration  
**Updated:** 2026-09-11  
**Audience:** Workflow, preset, GitHub adapter, recovery, and dashboard contributors and operators  
**Authority:** Conservative reconciliation of pre-lifecycle GitHub issues onto the label-based state machine, retained-history behavior, and coordinated multi-device release. The state machine itself is owned by [GitHub Issue Status State Machine Design](GitHubIssueStatusStateMachineDesign.md). Existing execution, checkpoint, publishing, and merge contracts retain their respective authority.  
**Owning Surface:** Trusted GitHub issue operations and workflow terminal/reconciliation boundaries  
**Related Implementation:** `moonmind/workflows/temporal/github_issue_legacy_cutover.py`, `moonmind/workflows/temporal/github_issue_lifecycle.py`, `moonmind/workflows/temporal/github_issue_reconciliation.py`, and `tests/unit/workflows/temporal/test_github_issue_legacy_cutover_4184.py`.

**Related Docs:** [GitHub Issue Status State Machine Design](GitHubIssueStatusStateMachineDesign.md), [Workflow Presets System](WorkflowPresetsSystem.md), [Workflow Publishing](WorkflowPublishing.md), [Checkpoint Branch System](CheckpointBranchSystem.md), [Workflow Remediation](WorkflowRemediation.md), and [PR Merge Automation](PrMergeAutomation.md).

This document defines desired behavior, not implemented or deployment-tested capability. Implementation tracking and rollout notes belong in issues or temporary execution artifacts, not this design.

## 1. Surviving policy

One policy owner serves every issue-status reader, writer, stored contract, seeded preset, recurring schedule, explicit issue path, PR handoff, runtime publication guard, and pending GitHub effect: `moonmind.workflows.temporal.github_issue_lifecycle`. The machine-readable caller inventory lives beside the implementation (`CALLER_INVENTORY`); this document names the rule, not the file list.

The new path never emits or requires `status: todo`. A legacy `status: todo` label found on an old issue is retained history only: it is never bulk-cleared, never blocks the new path, and never silently approves legacy work. Superseded todo-based transitions and duplicated live status interpretations are eliminated in the same supported release that ships this cutover.

## 2. Bounded read-only legacy assessment

Legacy assessment runs through existing tooling, is bounded (at most 100 comments and 20 PRs per issue per run), read-only, and repeatable. For each legacy signal — in-progress labels without a trusted handoff, old or generic start comments, open Done, unknown status formats, linked PRs, private-only checkpoints, and unsupported attempt versions — it reports exact evidence, known owner or unknown ownership, and a suggested action.

Incomplete evidence is explained, never inferred as no work: absent metadata does not prove no work exists. There is no timestamps-only release, no broad label removal, no PR deletion, and no claim that an unreadable comment means no owner. Exhausted pagination or read budgets produce an explicit incomplete-evidence result, never a clean repository.

## 3. Repair authorization

Repairs run only through the shared evidence/authorization rules: conclusive stop evidence, verified preservation or explicitly absent work, settled pending mutations, and the observed label outcome. Human labels, old comments, useful PRs, and retained attempt history are preserved; conclusively stopped work may receive a portable handoff and appropriate state.

Ambiguous cases — contradictory history, unknown formats, private-only work, multiple competing PRs, manual labels with no trusted handoff — stay blocked for an explicit operator decision. Reopened issues are reassessed from current evidence; old labels are never resurrected and reopened issues are never declared automatically fresh.

## 4. Retained history

Old Activity inputs and tool results at the supported attempt version pass representative replay/compatibility handling. Preset expansions and pending finalization operations follow a controlled drainage path: reread current GitHub evidence, complete conclusive handoffs, abandon obsolete transitions, and hold ambiguous items for an operator decision.

New executions stop writing obsolete shapes (`status: todo`, `status: claiming`, unversioned handoffs, whole-label-set replacement, local-only recovery claims). Frozen workflow inputs are never reinterpreted and no permanent alias stack is kept.

## 5. Coordinated release

All participating devices install matching code, preset definitions, and label/permission readiness while nonparticipating old claimers and publishers are paused or drained. An unknown-format check in new code cannot constrain old code that ignores the format, so mixed old/new behavior is unqualified until old bypasses stop, installation identities verify unique, and operational defaults reconcile.

Operator defaults: available work has no MoonMind status label (no `status: todo` gate); recovery-needed routes to continuation against the existing PR while code-review routes to the established PR follow-up journey; private-only checkpoints are never presented as cross-device recovery; coordination is best-effort and simultaneous duplicate starts remain possible.

## 6. Upgrade and rollback rehearsal

Upgrade and rollback are rehearsed with fixtures before they touch real issues. A rollback never restarts an old selector against active new-format work without a controlled hold/drain. Fixtures prove labels, comments, code references, artifacts, and unrelated deployment settings survive either direction.
