# Quickstart: Step Ledger Checkpoint Durability

## Unit Test Strategy

Use helper/model tests first:

```bash
./tools/test_unit.sh tests/unit/workflows/tasks/test_prepared_context.py \
  tests/unit/workflows/temporal/test_step_ledger.py \
  tests/unit/workflows/temporal/workflows/test_run_step_ledger.py \
  tests/unit/workflows/temporal/workflows/test_run_resume_from_failed_step.py \
  tests/unit/workflows/temporal/test_temporal_service.py
```

Planned unit coverage:

- prepared refs are extracted and recorded without inline content.
- step result artifacts become semantic output refs for preservation evidence.
- state checkpoint refs are attached idempotently by logical step id and attempt.
- completed steps missing output refs or state checkpoints receive bounded ineligible reasons.
- resume checkpoint payloads reject missing state checkpoint evidence and large/binary inline payloads.

## Integration Test Strategy

Use hermetic Temporal/API coverage where possible:

```bash
./tools/test_integration.sh
```

Focused integration candidates:

```bash
./tools/test_unit.sh tests/integration/temporal/test_backend_resume_eligibility.py
./tools/test_unit.sh tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py
```

Planned integration coverage:

- successful preparation records reusable prepared input refs in parent evidence.
- successful step completion records semantic output refs and, for mutating steps, a parent-owned state checkpoint ref.
- repeated checkpoint writes for the same step boundary remain idempotent.
- large/binary checkpoint payloads stay behind artifact refs.
- a failed source run with one completed step lacking refs/checkpoints marks that step Resume-ineligible.
- a delegated child step that returns output/checkpoint refs is projected into parent-owned evidence.

## End-to-End Story Validation

1. Submit or simulate a `MoonMind.Run` with prepared inputs and multiple steps.
2. Complete preparation and at least one successful step.
3. Ensure parent evidence contains prepared refs, semantic output refs, and state checkpoint refs.
4. Force a later step failure.
5. Verify Resume eligibility uses the durable refs and checkpoints, not logs or UI reconstruction.
6. Verify any completed step missing required evidence is marked ineligible with a bounded reason.
7. Confirm `MM-646` and the Jira preset brief remain present in final verification evidence.
