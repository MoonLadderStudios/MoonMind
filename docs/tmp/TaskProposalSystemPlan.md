# Task Proposal System Plan

Status: Active  
Owners: MoonMind Engineering  
Last Updated: 2026-03-27  
Canonical source: `docs/Tasks/TaskProposalSystem.md`

---

## 1. Purpose

Track the implementation phases required to bring the task proposal system to the
desired Temporal-native design described in `docs/Tasks/TaskProposalSystem.md`.

This file is intentionally phase-oriented and disposable. The canonical product
behavior lives in the source doc above.

---

## 2. Desired Outcome

The finished system must provide:

1. proposal generation as a first-class `MoonMind.Run` stage
2. canonical proposal payloads aligned with `/api/executions`
3. end-to-end proposal policy plumbing, including `defaultRuntime`
4. Temporal-native proposal promotion
5. consistent lifecycle, origin, runtime, and status semantics across workflow,
   API, storage, UI, and docs

---

## 3. Phase Plan

### Phase 1. Contract Alignment

Goal: make proposal data model, API contract, and workflow inputs agree on one
canonical proposal shape.

Tasks:

1. Add `defaultRuntime` to `TaskProposalPolicy` and validate it against supported
   task runtimes.
2. Standardize proposal payloads on the Temporal submit contract used by
   `/api/executions`.
3. Remove `agent_runtime` as the documented proposal payload tool type.
4. Preserve raw `task.proposalPolicy` in run `initialParameters`.
5. Normalize origin metadata to snake_case and `origin.source = "workflow"`.
6. Update proposal API schemas to expose promotion linkage and returned execution
   metadata cleanly.

Exit criteria:

1. one canonical proposal payload shape exists
2. one canonical proposal policy shape exists
3. docs, models, and API payloads no longer disagree on origin or runtime names

### Phase 2. Temporal-Native Promotion

Goal: make proposal promotion create real new work again.

Tasks:

1. Reimplement `TaskProposalService.promote_proposal()` without the legacy queue
   backend.
2. Load the stored proposal under lock and reject non-`open` proposals.
3. Merge `taskCreateRequestOverride` into the stored `taskCreateRequest`.
4. Validate the merged payload against the canonical task contract.
5. Route promotion through the same Temporal-backed create flow used by
   `/api/executions`.
6. Persist the created workflow or execution identifier on the proposal record.
7. Return both the updated proposal and the created execution metadata from the
   promote API.

Exit criteria:

1. promoting a proposal creates a new `MoonMind.Run`
2. proposal detail shows the promoted execution linkage
3. no legacy queue create path remains in proposal promotion

### Phase 3. Proposal Stage Hardening

Goal: make the workflow proposal stage carry the correct policy and obey global
operator control.

Tasks:

1. Enforce the workflow-level global proposal enable switch in the run workflow or
   submit path.
2. Stop relying on flattened `proposalTargets`, `proposalMaxItems`, and
   `proposalDefaultRuntime` as the primary proposal-stage contract.
3. Resolve proposal defaults plus per-task overrides inside the proposal stage or
   `proposal.submit`.
4. Stamp `defaultRuntime` onto candidate payloads only when the candidate omits a
   runtime.
5. Ensure finish summary data records requested, generated, submitted, and error
   outcomes consistently.

Exit criteria:

1. operators can disable proposal generation globally
2. proposal policy survives submit into workflow execution intact
3. proposal-stage finish summary data is complete and stable

### Phase 4. Activity-Boundary Cleanup

Goal: move proposal side effects onto the correct worker boundary.

Tasks:

1. Keep `proposal.generate` on the LLM-capable activity fleet.
2. Move proposal submission and control-plane writes onto an integrations or
   control-plane-capable activity fleet.
3. Audit activity catalog and worker topology docs to reflect the boundary.
4. Verify retries, idempotency, and auth expectations at the new boundary.

Exit criteria:

1. LLM work and proposal storage are separated by queue responsibility
2. proposal submission no longer depends on the LLM fleet for control-plane writes

### Phase 5. UI and Observability Alignment

Goal: make Mission Control and execution APIs display proposal-stage behavior
accurately.

Tasks:

1. Add `proposals -> running` to execution and dashboard status normalization.
2. Normalize `completed` as the canonical successful terminal state everywhere.
3. Surface supported task runtimes from backend config on all proposal promotion
   UI flows.
4. Add proposal-stage and promoted-execution metadata to detail views where
   applicable.
5. Align list and detail views with workflow-origin proposal filtering.

Exit criteria:

1. proposal stage is visible in list and detail status surfaces
2. promotion shows the created execution linkage
3. stale runtime names and stale success-state names are removed from UI contracts

### Phase 6. Regression Coverage

Goal: add workflow-boundary and API coverage for compatibility-sensitive paths.

Tasks:

1. Add API tests for proposal promotion success and invalid-state rejection.
2. Add tests covering runtime override precedence during promotion.
3. Add workflow-boundary tests for proposal policy propagation into the proposal
   stage.
4. Add regression coverage for global proposal disable behavior.
5. Add compatibility tests for origin metadata naming and status projection.

Exit criteria:

1. promotion and policy propagation are covered at the API and workflow boundary
2. regressions in proposal-stage status, policy, or promotion are caught by tests

---

## 4. Suggested Execution Order

1. Phase 1
2. Phase 2
3. Phase 3
4. Phase 5
5. Phase 4
6. Phase 6

Rationale:

1. contract alignment must land before code paths are hardened
2. promotion is the biggest functional gap
3. UI and observability should not finalize until the workflow and promotion
   contracts are stable

---

## 5. Open Risks

1. promotion changes touch compatibility-sensitive execution-create contracts
2. proposal policy plumbing affects persisted workflow inputs and in-flight runs
3. UI status cleanup spans multiple adapters and docs that previously used stale
   state names
4. proposal submission queue movement requires worker-topology coordination

---

## 6. Completion Rule

This plan is complete when the canonical behavior in
`docs/Tasks/TaskProposalSystem.md` is implemented and the temporary migration tasks
above are no longer needed. At that point, remove this file.
