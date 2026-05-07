# Quickstart: Process Verified Tracker Decisions

## Focused Unit Tests

Run focused unit tests while implementing:

```bash
python -m pytest \
  tests/unit/workflows/task_proposals/test_delivery.py \
  tests/unit/workflows/task_proposals/test_service.py \
  tests/unit/api/routers/test_task_proposals.py \
  tests/unit/workflows/temporal/test_proposal_activities.py \
  -q
```

Expected red-first coverage before production edits:
- Provider authenticity rejects unsigned or invalid GitHub/Jira decision events.
- Actor authorization blocks unapproved reviewer decisions.
- Provider decision parser accepts promote, dismiss, defer, reprioritize, and request revision.
- Edited issue body text and Jira ADF never replace the stored proposal snapshot.
- Provider approval creates one run from the stored snapshot and preserves explicit skill selectors, authored presets, and step source provenance.
- Duplicate provider approval creates zero additional runs.
- Dismiss, defer, reprioritize, and request revision create zero runs and record audit metadata.
- Runtime override controls are validated before run creation.
- Rejected decision and provider error metadata is sanitized.

## Integration / Boundary Tests

Run focused boundary tests:

```bash
python -m pytest tests/integration/temporal/test_proposal_review_delivery.py -q
```

Add or update hermetic coverage for:
- Verified provider approval through the trusted ingestion boundary to proposal promotion and run creation.
- Duplicate provider event replay with no duplicate run.
- Unverified or unauthorized provider event with no proposal state mutation beyond sanitized rejection audit.
- Non-executing provider decisions persisted with external state and no run creation.
- Recovery inspection/sync/promote surface, if implemented as API behavior.

If new tests are marked `integration_ci`, run:

```bash
./tools/test_integration.sh
```

## Full Unit Verification

Before leaving implementation, run:

```bash
./tools/test_unit.sh
```

## End-to-End Story Validation

1. Create or load a delivered proposal with an external provider identity and stored `taskCreateRequest`.
2. Submit a verified authorized provider approval event with bounded controls.
3. Confirm exactly one MoonMind.Run is created from the stored snapshot.
4. Replay the same provider event and confirm no additional run is created.
5. Submit dismiss, defer, reprioritize, and request-revision events and confirm each records audit state without run creation.
6. Submit an unverified or unauthorized event and confirm no run is created and secrets are absent from persisted/output metadata.
7. Confirm MM-599 remains preserved in `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/`, `quickstart.md`, `tasks.md`, and final verification evidence.
