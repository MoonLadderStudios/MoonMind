# Quickstart: Merge Gate

## Preconditions

- Active feature: `specs/179-merge-gate`
- Jira source: `MM-341`
- Merge automation is enabled only for test requests that explicitly opt in.
- GitHub/Jira readiness checks in automated tests use fakes or activity stubs unless running manual provider verification.

## Focused Unit Validation

Run focused unit tests while implementing:

```bash
./tools/test_unit.sh tests/unit/workflows/temporal/test_merge_gate_models.py
./tools/test_unit.sh tests/unit/workflows/temporal/test_merge_gate_workflow.py
./tools/test_unit.sh tests/unit/workflows/temporal/test_run_merge_gate_start.py
```

Expected coverage:

- Merge-gate input and readiness models reject missing PR identity, unknown status values, unsupported policies, and mismatched head revisions.
- Readiness classification returns blockers for checks running, checks failed, automated review pending, Jira status pending, stale revision, closed PR, policy denial, and unavailable external state.
- Parent `MoonMind.Run` starts a gate only after a confirmed PR publication and only when merge automation is enabled.
- Resolver creation uses pr-resolver with publish mode `none` and is idempotent for retries and duplicate events.

## Temporal Boundary Validation

Run focused Temporal workflow-boundary tests:

```bash
./tools/test_unit.sh tests/unit/workflows/temporal/workflows/test_merge_gate_temporal.py
```

Expected scenarios:

- Parent run finalizes successfully after starting the merge gate.
- Merge gate remains waiting and records sanitized blockers while readiness is incomplete.
- Merge gate creates exactly one resolver follow-up after readiness opens.
- Replays, duplicate readiness events, and repeated polling do not create duplicate resolver runs.
- Closed PRs, stale revisions, policy denial, and unavailable external state block with operator-readable reasons.

## Hermetic Integration Validation

When compose services are available, run the required hermetic integration suite:

```bash
./tools/test_integration.sh
```

Expected integration evidence:

- Workflow registration includes `MoonMind.MergeGate`.
- Activity routing and task queue assignment can execute merge-gate activities with local fakes.
- Projection or detail surfaces can distinguish completed parent runs, waiting gates, and resolver follow-ups without exposing raw provider payloads.

## End-to-End Story Check

1. Submit or simulate a task configured with publish mode `pr` and merge automation enabled.
2. Confirm the parent implementation run publishes a PR.
3. Confirm the parent starts one merge gate for the published PR revision.
4. Confirm the parent run completes without waiting for the gate to open.
5. Simulate incomplete readiness and confirm the gate records blockers without launching pr-resolver.
6. Simulate ready external state for the same head revision and confirm exactly one resolver `MoonMind.Run` is created.
7. Confirm the resolver run selects pr-resolver and publish mode `none`.
