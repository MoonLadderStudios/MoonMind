# Workflow Remediation Checkpoint Alignment

Status: Desired-state design addendum
Owners: MoonMind Platform + dashboard
Last Updated: 2026-07-03

This addendum aligns Workflow Remediation with Step Executions, Checkpoint Branches, and the Omnigent adapter. It is intended to be folded into `docs/Workflows/WorkflowRemediation.md` after review.

## Core decision

Remediation should not create a second evidence or repair-attempt substrate. It should remain a `MoonMind.UserWorkflow` that targets another Workflow Execution, but it should reuse the existing Step Execution, checkpoint, Checkpoint Branch, artifact, and adapter evidence systems.

The remediation context artifact should be a bounded index over existing evidence, not a parallel evidence store. It names the target, pinned run, relevant Step Execution manifests, checkpoint refs, recovery and incident manifests, branch refs, adapter capture refs, logs, diagnostics, and policy snapshots.

## Create-page first flow

The dashboard `Remediate` action should open `/workflows/new` with a remediation draft instead of immediately submitting a hidden remediation run. The Create page should prefill:

- target `workflowId` and pinned `runId`;
- selected step or checkpoint refs when available;
- repository `MoonLadderStudios/MoonMind` for MoonMind platform/prevention work;
- default mode `snapshot_then_follow`;
- default authority `approval_gated`;
- default action policy `admin_healer_default`;
- branch and publish controls through the normal Create page fields.

The operator can then edit instructions, runtime, model, branch, and publish mode before submitting through the normal `POST /api/executions` path with `task.remediation`.

## Checkpoint-backed repair

Corrected-instruction remediation should use Checkpoint Branches. When remediation needs to run code or workflow work with different instructions, branch, publish mode, runtime, or model, it should call a Checkpoint Branch operation rather than silently editing the original workflow input or overloading failed-step Resume.

A remediation-created branch should record:

- source workflow id and pinned source run id;
- logical step id and Step Execution ordinal when applicable;
- checkpoint boundary and checkpoint ref;
- immutable instruction artifact ref and digest;
- workspace policy;
- runtime context policy;
- publish mode;
- remediation workflow/run provenance;
- idempotency key.

Failed-step recovery remains the path that preserves original inputs and resumes from validated checkpoint evidence. It must not accept edited instructions, alternate branch settings, or publish-mode changes. Those belong to a Checkpoint Branch or a fresh workflow created through Create.

## Evidence priority

A remediation context builder should resolve evidence in this order when available:

1. target execution detail;
2. failed-run recovery manifest;
3. incident reconstruction manifest;
4. Step Execution manifests;
5. checkpoint artifacts/read models;
6. Checkpoint Branch records and branch-turn context bundles;
7. adapter capture manifests and provider diagnostics;
8. step ledger projection;
9. managed-run observability summaries, logs, and diagnostics;
10. execution-linked artifacts and summaries.

The builder may fall back to historical logs and summaries, but branch repair is allowed only when checkpoint validation succeeds.

## Omnigent alignment

For Omnigent-backed target work, remediation consumes MoonMind artifacts harvested by the Omnigent adapter: normalized stream artifacts, snapshots, transcripts, workspace manifests, optional patch refs, PR metadata, and diagnostics.

Omnigent session ids, file ids, resource ids, runner ids, and provider URLs are runtime binding or diagnostics metadata. They are not MoonMind evidence authority and should not replace artifact refs.

Omnigent v1 uses a streaming-gateway activity that returns terminal results. Therefore remediation should not send hidden follow-up messages into a parent Omnigent session in v1. Corrective execution should create a fresh Checkpoint Branch turn with a new Omnigent session. Same-session continuation should wait for typed v2 activities such as `integration.omnigent.send_message` and `integration.omnigent.harvest_session`.

## Typed action surface

Remediation actions should be typed and control-plane mediated. The action registry should include execution lifecycle actions, Checkpoint Branch actions, managed-session actions, provider-profile lease actions, workload actions, and adapter diagnostic refresh actions.

Recommended branch-oriented actions:

- `checkpoint_branch.create_from_remediation_context`
- `checkpoint_branch.continue`
- `checkpoint_branch.compare`
- `checkpoint_branch.publish`
- `checkpoint_branch.promote`
- `checkpoint_branch.archive`

Actions that inspect or affect deployment-local runtime state should go through typed MoonMind or adapter tools, not direct host/container/provider access from the agent.

## Updated v1 recommendation

A practical v1 should ship with:

1. manual Create-page remediation flow;
2. pinned target run identity;
3. `reports/remediation_context.json` as a ref-only overlay;
4. evidence reuse from recovery, incident, Step Execution, checkpoint, branch, adapter, and observability artifacts;
5. Checkpoint Branch creation for corrected-instruction repair;
6. `observe_only` and `approval_gated` as the default safe authority modes;
7. conservative typed actions for pause/resume, branch creation, session clear/interrupt, stale lease eviction, and adapter capture refresh;
8. Omnigent v1 fresh-session branch repair only;
9. full audit artifacts and remediation summary linkage.

## Acceptance criteria delta

Workflow Remediation is aligned with these systems when:

- `task.remediation` can be submitted from Create;
- `Remediate` opens Create with an editable draft;
- context building indexes existing artifacts instead of duplicating them;
- checkpoint-backed repair uses Checkpoint Branch APIs;
- provider-specific evidence is consumed through MoonMind artifact refs;
- Omnigent same-session continuation is blocked until typed v2 lifecycle activities exist;
- dashboard surfaces show remediation-created branches alongside remediation links.
