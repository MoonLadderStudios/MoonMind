# Quickstart: Surface Proposal Outcomes

## Prerequisites

- Run from repository root.
- Use local managed-agent test mode for unit tests:

```bash
export MOONMIND_FORCE_LOCAL_TESTS=1
```

## Focused Unit Test Plan

Add failing tests first, then implement until they pass:

```bash
python -m pytest \
  tests/unit/workflows/temporal/workflows/test_run_proposals.py \
  tests/unit/workflows/task_proposals/test_service.py \
  tests/unit/api/routers/test_task_proposals.py \
  tests/unit/api/routers/test_task_dashboard_view_model.py \
  tests/unit/agents/codex_worker/test_worker.py \
  -q
```

Expected coverage:
- requested/generated/submitted/delivered counts in proposal summaries
- redacted validation errors and provider delivery failures
- external issue links and dedup updates in summary/detail payloads
- `mm_state=proposals` remains visible and maps to running
- promoted execution IDs become run-detail-visible promotion links

## Focused Frontend Test Plan

After JS dependencies are prepared:

```bash
npm run ui:test -- \
  frontend/src/entrypoints/task-detail.test.tsx \
  frontend/src/entrypoints/mission-control.test.tsx \
  frontend/src/entrypoints/tasks-list.test.tsx
```

Expected coverage:
- execution detail and Mission Control show proposal provider, external key, delivery status, last sync timestamp, dedup status, compact task summary, and promotion link
- task status filtering still includes `proposals`
- normal UI copy/navigation does not make a standalone MoonMind proposal queue the primary review path

## Focused Integration Test Plan

Extend the hermetic proposal review delivery boundary:

```bash
python -m pytest tests/integration/temporal/test_proposal_review_delivery.py -q
```

Expected coverage:
- one proposal-capable run produces delivered, duplicate, malformed, failed-delivery, and promoted outcomes
- finish summary, exported run summary, API detail payload, and Mission Control-facing payloads expose matching compact outcome data
- malformed candidates do not promote
- duplicate provider delivery remains idempotent

## Final Verification Commands

Before completing implementation in a later step:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
./tools/test_integration.sh
```

If `./tools/test_integration.sh` is blocked by unavailable Docker in the managed environment, record the exact blocker and preserve focused integration evidence.

## End-to-End Story Check

1. Submit or simulate a proposal-capable run with proposal generation requested.
2. Include one delivered proposal, one duplicate/dedup update, one malformed candidate, one provider delivery failure, and one promoted proposal.
3. Confirm finish summary and exported run summary include requested/generated/submitted/delivered counts, links, dedup updates, and redacted failures.
4. Confirm execution detail and Mission Control show provider, external key, delivery status, last sync, dedup status, compact task summary, and promotion result links.
5. Confirm GitHub or Jira remains the normal human review surface.
