# Quickstart: Remediation Mission Control Panels

## Prerequisites

- Local Python and Node/npm dependencies available through the repo test scripts.
- For frontend-focused iteration, allow `./tools/test_unit.sh` to prepare `node_modules` when needed.
- No provider credentials are required for planned unit or hermetic integration checks.

## Unit Test Strategy

Run focused frontend tests during iteration:

```bash
./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx
```

When backend route/service serialization changes, run focused Python unit tests:

```bash
./tools/test_unit.sh tests/unit/workflows/temporal/test_temporal_service.py tests/unit/workflows/temporal/test_remediation_context.py
```

Before completing implementation, run the full required unit suite:

```bash
./tools/test_unit.sh
```

## Integration Test Strategy

If backend API response shapes or route behavior changes, add or update hermetic integration coverage and run:

```bash
./tools/test_integration.sh
```

The integration check should prove the task detail contract can obtain remediation links and approval decisions through MoonMind-owned API surfaces without external provider credentials.

## Test-First Scenarios

1. Render remediation creation from every eligible Mission Control surface named in the spec.
2. Submit a remediation request and assert canonical `task.remediation` includes pinned run, selected steps, authority, mode, action policy, evidence policy, and manual trigger.
3. Render inbound target-side remediation relationships with status, authority, latest action, resolution, lock scope, and lock holder.
4. Render outbound remediation target relationships with target link, pinned run, selected steps, current target state, evidence bundle, allowed actions, approval state, and lock state.
5. Render active live observation with observation label, sequence cursor, reconnect state, epoch state, and durable fallback.
6. Render unavailable live follow with logs, diagnostics, summaries, and artifact fallback states.
7. Render approval handoff with action, preconditions, blast radius, approve/reject controls, and persisted decision state.
8. Render degraded cases for missing target, rerun target, historical merged logs, partial artifacts, lock conflict, precondition failure, and failed remediator final summary.

## End-To-End Story Check

Create representative target/remediation fixtures, open the target task detail and remediation task detail, then verify that an operator can understand:
- how to start remediation,
- what target/run is pinned,
- what remediation links exist in both directions,
- what evidence and live observation are available,
- what actions are allowed,
- whether approvals or locks block mutation,
- what degraded or terminal outcome applies.

## Final Verification

After implementation and task completion, run MoonSpec verification against:

```text
specs/324-remediation-mission-control-panels/spec.md
```

The verification must compare behavior against `MM-624`, the preserved Jira preset brief, and source design IDs DESIGN-REQ-010, DESIGN-REQ-024, DESIGN-REQ-025, and DESIGN-REQ-028.
