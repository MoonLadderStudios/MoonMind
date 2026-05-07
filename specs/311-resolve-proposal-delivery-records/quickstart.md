# Quickstart: Resolve Proposal Policy and Delivery Records

## Focused Unit Validation

Run focused tests while implementing policy, service, and activity changes:

```bash
python -m pytest tests/unit/workflows/task_proposals/test_service.py tests/unit/workflows/temporal/test_proposal_activities.py -q
```

Add or update tests for:
- explicit candidate values over defaults
- destination allowlist rejection
- capacity, severity, and tag gate rejection
- successful defaulted delivery decision
- project repository preservation
- MoonMind run-quality repository rewrite after gates pass
- local-record and provider-metadata dedup paths
- open duplicate update/link/comment instead of duplicate record creation
- snake_case workflow origin metadata
- provider-specific metadata separated from canonical fields

## Required Unit Suite

```bash
./tools/test_unit.sh
```

## Integration / Boundary Validation

If implementation changes persisted proposal fields, repository duplicate behavior, API serialization, or migrations, add a hermetic DB-backed test and run the relevant integration command:

```bash
./tools/test_integration.sh
```

If no compose-backed integration is needed, still include boundary-style unit tests that exercise `TemporalProposalActivities.proposal_submit` with a mocked trusted proposal service and a real `TaskProposalService` where practical.

## End-to-End Story Check

1. Submit candidates with explicit policy values and defaults; confirm explicit values are preserved unless rejected by policy.
2. Submit project-targeted and MoonMind run-quality candidates; confirm repository target decisions are deterministic and recorded.
3. Submit duplicate candidates; confirm open duplicates are updated, linked, or commented on instead of creating duplicate reviewer-facing records.
4. Inspect the persisted delivery record; confirm canonical fields, selected provider/external fields, dedup identity, origin identity, and provider metadata separation.
5. Confirm workflow-origin metadata uses `origin.source = "workflow"`, `origin.id = workflow_id`, and snake_case metadata keys.
6. Confirm `MM-597` remains preserved in MoonSpec artifacts and final verification output.
