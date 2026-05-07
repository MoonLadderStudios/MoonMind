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

## Final MoonSpec Verification

After implementation and required tests pass, run the final read-only verification step:

```bash
/moonspec-verify
```

The verification report should preserve MM-597, the canonical Jira preset brief, and DESIGN-REQ-001 through DESIGN-REQ-008.

## Implementation Evidence - 2026-05-07

Focused MM-597 validation passed:

```bash
python -m pytest tests/unit/workflows/task_proposals/test_service.py tests/unit/workflows/temporal/test_proposal_activities.py tests/unit/api/routers/test_task_proposals.py -q
# 67 passed
```

Required unit suite passed in managed local test mode:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
# Python: 4442 passed, 1 xpassed, 16 subtests passed
# Frontend: 20 files passed; 324 passed, 223 skipped
```

Compose-backed `./tools/test_integration.sh` was not run for this implementation step because no new `integration_ci` test target was added; MM-597 coverage is exercised through the Temporal proposal activity boundary, service/repository unit coverage, API serialization coverage, and the migration declaration test.
