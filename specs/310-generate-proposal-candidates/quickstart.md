# Quickstart: Generate and Validate Proposal Candidates

## Focused Unit Validation

```bash
python -m pytest tests/unit/workflows/temporal/test_proposal_activities.py -q
python -m pytest tests/unit/workflows/task_proposals/test_service.py -q
```

## Required Unit Suite

```bash
./tools/test_unit.sh
```

## Integration/Boundary Validation

No Docker-backed integration service is required for this story. Boundary coverage is provided by focused Temporal proposal activity tests that exercise the real activity methods and mock the trusted proposal service boundary.

## End-to-End Story Check

1. Invoke `proposal.generate` with a workflow id, repository, parent task instructions, a distinct next-step idea, and optional skill/preset provenance.
2. Confirm it returns candidate data only and does not call the proposal service.
3. Invoke `proposal.submit` with valid and invalid candidates.
4. Confirm valid candidates are handed to the proposal service and invalid candidates return redacted errors with no service call.
5. Confirm `MM-596` remains preserved in MoonSpec artifacts and verification output.
