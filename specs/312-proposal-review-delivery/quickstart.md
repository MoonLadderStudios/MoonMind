# Quickstart: Proposal Review Delivery

## Scope

Validate MM-598 as a single runtime story: proposals are delivered to GitHub Issues or Jira issues as review artifacts while MoonMind keeps the stored proposal snapshot as the executable source of truth.

## Focused Unit Iteration

```bash
python -m pytest \
  tests/unit/workflows/task_proposals/test_service.py \
  tests/unit/workflows/task_proposals/test_delivery.py \
  tests/unit/workflows/temporal/test_proposal_activities.py \
  tests/unit/api/routers/test_task_proposals.py \
  -q
```

Expected coverage:
- GitHub issue renderer includes title prefix, labels, hidden marker, evidence links, reviewer commands, dedup marker, and stored-snapshot notice.
- Jira issue renderer emits ADF description, configured labels/fields, workflow/action trigger metadata, evidence links, dedup marker, and stored-snapshot notice.
- Delivery service creates or updates one issue per provider/destination/dedup target.
- Duplicate local records and provider metadata cause update/link/comment behavior, not duplicate issue creation.
- Provider decision events accept only bounded controls and never replace the stored snapshot from edited issue text.
- Provider policy denial and provider errors are sanitized.

## Full Unit Suite

```bash
./tools/test_unit.sh
```

Use this before final implementation handoff to confirm the broader backend and frontend unit suite still passes.

## Integration Strategy

If implementation adds compose-backed persistence or activity/API boundary coverage, run:

```bash
./tools/test_integration.sh
```

Integration coverage should stay hermetic:
- no real GitHub or Jira credentials
- fake provider clients or trusted service stubs
- local database fixtures only
- `integration_ci` marker only when compose-backed dependencies are required

## End-To-End Story Validation

1. Seed or create a proposal candidate for a GitHub-configured destination.
2. Run proposal submission/delivery with fake provider client.
3. Confirm one GitHub Issue payload is created or updated and the delivery record stores external URL/key.
4. Repeat the same proposal and confirm no duplicate issue is created.
5. Seed or create a proposal candidate for a Jira-configured destination.
6. Run proposal submission/delivery with fake Jira service.
7. Confirm one Jira issue payload is created or updated with ADF review description and delivery metadata.
8. Simulate provider reviewer commands or workflow-state events for promote, dismiss, defer, and priority.
9. Confirm decisions are idempotent, actor/policy checked, redacted, and applied only to the stored MoonMind snapshot.
10. Confirm task run details or finish summary expose proposal delivery status and external issue links.

## Final Traceability Checks

```bash
rg -n "MM-598|DESIGN-REQ-001|DESIGN-REQ-014|DESIGN-REQ-015|DESIGN-REQ-016|DESIGN-REQ-027|DESIGN-REQ-031" specs/312-proposal-review-delivery
```

Expected result: all identifiers remain present in `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/`, `quickstart.md`, later `tasks.md`, and final verification evidence.
