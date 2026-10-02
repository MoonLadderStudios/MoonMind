# Remaining Workflow Authority Follow-ups

Status: Open, bounded follow-up scope
Reviewed: 2026-10-02
Owning contracts: [Workflow Publishing](../Workflows/WorkflowPublishing.md),
[PR Merge Automation](../Workflows/PrMergeAutomation.md),
[Canonical Turn Command Boundary](../Omnigent/CanonicalTurnCommandBoundary.md)

MoonMind remains a single-operator application with first-party Omnigent UI and
scoped machine callers. These follow-ups must preserve supported authoring,
recovery, concurrent work, and retained Temporal histories. They do not authorize
new human roles, an additional authority registry, or a second publication owner.

## 1. Same-repository PR handoff provenance

Finding: `5e7a168aa24881919db956eda1410c56`, partially mitigated and still open.

The parent now requires a canonical PR URL in its authored repository. That
prevents combining a result URL's PR number with another repository. It does not
establish that a same-repository PR belongs to the admitted task.

The remaining behavior was reproduced through the production
`_record_execution_context` and `_build_merge_gate_start_payload` methods: an
ordinary output summary containing a same-repository PR URL, together with an
output `headSha`, produces a merge-gate target without an accepted published
head. Readiness checks alone do not prove the task's authority over that target.

### Follow-up through existing owners

- Extend the existing managed publication evidence at the create/adopt boundary
  to carry an atomic, remotely verified repository, PR, head, and base tuple bound
  to the admitted request. Reuse `acceptedRepositoryEvidence` and the existing
  publication contract rather than introducing another evidence store.
- Have the provider-native result owner establish equivalent task-bound evidence
  from its existing provider session/request. A generic metadata field is not
  provider attestation.
- Have existing-PR admission carry the explicitly adopted, verified target through
  its existing request contract. Supported adoption must not require creating a
  duplicate PR or pretending to be a managed publisher.
- Make the parent merge handoff consume the accepted tuple. Keep ordinary
  summary/metadata as display evidence. Recover missing durable context through
  the existing owner and remote observation without repeating uncertain effects.

### Automated acceptance

- An unrelated, merge-ready PR in the same repository cannot become the target
  through a summary, diagnostics text, or forged publication metadata.
- Managed PR creation/adoption, provider-native publication, and explicit
  existing-PR operations retain their supported outcomes.
- A later verifier cannot replace an accepted PR/head/base tuple with incidental
  output or combine fields from different attempts.
- Lost acknowledgements reconcile the original publication; continuation and
  replay preserve the accepted candidate and do not duplicate mutations.

## 2. Per-step Omnigent plan admission

Finding: `f19e64fdb264819184c59d0b04e4cb1b`, partially mitigated and still open.

The workflow now prevents authored node/runtime parameters from replacing,
minting, or erasing its API-admitted typed `omnigentExecutionPlan` binding.
Normal top-level Omnigent requests resolve stored profiles and compile immutable
plan authority. Generic plan resolution also reloads profile versions and checks
their digest; a snapshot claim alone is not equivalent to a verified agent launch.

The remaining supported path is mixed-runtime authoring. In
`api_service/api/routers/executions.py`, `_resolve_step_runtime_selections`
explicitly admits per-step runtime overrides, while immutable Omnigent plan
compilation currently depends on the top-level selected runtime. Raw operator
admission can also retain nested Omnigent selection and caller snapshots. Its
admission was reproduced, but arbitrary provider execution was not established.

### Follow-up through existing owners

- Extend the existing API/Omnigent plan compiler to admit resolved per-step
  Omnigent selections, preserving each selected profile and authored constraints.
  Derive the required plan evidence from the already resolved selections.
- Carry that authority through the existing typed request and turn-command
  boundary; reject genuinely conflicting plan/profile claims there.
- Do not replace supported mixed-runtime authoring with a blanket missing-plan
  error. Do not infer a new selection from a mutable default during recovery.

### Automated acceptance

- A non-Omnigent workflow with an explicitly selected Omnigent step works through
  normal API admission without manually supplied internal binding fields.
- Different legitimate step profiles retain their requested identities; forged
  snapshots or typed bindings cannot select a different admitted plan/profile.
- Omitted and documented default selections compile consistently.
- Retry, recovery, ContinueAsNew, capacity handoff, and retained no-plan histories
  preserve their recorded semantics and recoverable work.

## 3. Recovery producer-binding questions

The historical cross-user premises in `3f6eb3aff2948191927ec1d2951c2346` and
`54118ad546008191921b429de36d8903` do not establish an escalation in the current
single-operator model. Scoped fan-out rejects raw execution requests and normal
envelopes omit caller-supplied root recovery/profile state. Raw operator requests
can still supply recovery metadata, so the assumption that all such state is
created by the recovery API remains a distinct integrity concern.

Keep `b2e20bbd07cc8191b50a7c737517ce74` open for producer-binding review. Supplied
`omnigentCheckpoint` output can reach checkpoint creation, while recovery checks
nested shape and selected-step identity without proving full nested source
identity. Plan-bound checkpoints do receive additional checks. A supported
malicious-output journey has not been reproduced.

The next useful proof is a provider-free journey across result projection,
checkpoint creation, and the existing recovery service: substitute a different
source workflow/run/checkpoint and verify rejection while legitimate recovery
still succeeds. If that establishes a gap, repair the existing producer/consumer
binding rather than adding a recovery framework or unrelated human access rules.
