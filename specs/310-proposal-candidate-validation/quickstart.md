# Quickstart: Proposal Candidate Validation

## Focused Unit Tests

Run proposal activity and service validation tests:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_proposal_activities.py tests/unit/workflows/task_proposals/test_service.py tests/unit/workflows/temporal/workflows/test_run_proposals.py
```

Expected result:
- Generation returns candidates without service/repository side effects.
- Candidate submission accepts `tool.type=skill` and rejects `tool.type=agent_runtime` before side effects.
- Skill selectors and reliable provenance are preserved when present and not fabricated when absent.
- Workflow proposal stage schedules `proposal.generate` and `proposal.submit` as distinct activities.

## Full Unit Suite

Before final verification, run:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

## Hermetic Integration Suite

When Docker is available in the execution environment, run:

```bash
./tools/test_integration.sh
```

If Docker is unavailable in a managed-agent container, record the exact blocker and rely on unit/workflow-boundary evidence plus final verification.

## End-to-End Story Check

1. Prepare a run payload with `task.proposeTasks=true`, explicit task or step skill selectors, and reliable `authoredPresets` / `steps[].source` evidence.
2. Execute proposal generation and confirm it returns candidates only, with no proposal records or external issue side effects.
3. Submit candidates through `proposal.submit` with a trusted service factory.
4. Verify valid canonical candidates are stored, invalid candidates return redacted errors, and rejected candidates do not call the proposal service.
5. Confirm `MM-596` and DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-017, DESIGN-REQ-018, DESIGN-REQ-019, and DESIGN-REQ-032 remain traceable in MoonSpec artifacts and final evidence.
