# Verification: Step Ledger Checkpoint Durability

**Jira issue**: MM-646
**Feature**: `345-step-ledger-checkpoint-durability`
**Date**: 2026-05-13

## Source Traceability

The canonical input remains the Jira preset brief preserved in `spec.md`: MM-646, "Step ledger & resume checkpoint durability in MoonMind.Run", sourced from `docs/Tasks/TaskArchitecture.md` sections 8.1, 8.5, 12.1, and Invariant 15 with original coverage IDs DESIGN-REQ-019 and DESIGN-REQ-023.

Implementation and tests cover FR-001 through FR-010, SCN-001 through SCN-007, SC-001 through SC-008, and DESIGN-REQ-001 through DESIGN-REQ-007.

## TDD Evidence

Red-first unit command:

```bash
./tools/test_unit.sh tests/unit/workflows/tasks/test_prepared_context.py tests/unit/workflows/temporal/test_step_ledger.py tests/unit/workflows/temporal/workflows/test_run_step_ledger.py tests/unit/workflows/temporal/workflows/test_run_resume_from_failed_step.py tests/unit/workflows/temporal/test_temporal_service.py
```

Result before production changes: failed for the intended missing MM-646 behavior, including missing `build_resume_prepared_artifact_refs` and `mark_step_checkpoint_evidence` imports.

Red-first focused integration command:

```bash
./tools/test_unit.sh tests/integration/temporal/test_backend_resume_eligibility.py tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py
```

Result before production changes: failed for the intended missing MM-646 behavior, including missing `mark_step_checkpoint_evidence`.

## Implementation Evidence

- `moonmind/workflows/tasks/prepared_context.py`: added compact prepared ref extraction for Resume evidence.
- `moonmind/workflows/tasks/__init__.py`: exported the prepared ref helper.
- `moonmind/schemas/temporal_models.py`: added bounded step ledger checkpoint and preservation fields, prepared refs on the snapshot model, and inline checkpoint payload rejection.
- `moonmind/workflows/temporal/step_ledger.py`: added semantic output ref checks, preservation eligibility markers, bounded ineligible reasons, preserved-step checkpoint state, and prepared refs on ledger snapshots.
- `moonmind/workflows/temporal/workflows/run.py`: records prepared refs on parent evidence, projects runtime checkpoint refs, refreshes checkpoint preservation after successful step completion, and exposes prepared refs in the step ledger query.
- `moonmind/workflows/temporal/service.py`: existing Resume checkpoint hydration and validation paths were exercised by the new service tests; no service code changes were required.

FR-009 was not already fully covered before this implementation; fallback implementation and tests for parent-owned delegated child/runtime checkpoint projection were kept in scope.

## Test Results

Focused unit validation after implementation:

```bash
./tools/test_unit.sh tests/unit/workflows/tasks/test_prepared_context.py tests/unit/workflows/temporal/test_step_ledger.py tests/unit/workflows/temporal/workflows/test_run_step_ledger.py tests/unit/workflows/temporal/workflows/test_run_resume_from_failed_step.py tests/unit/workflows/temporal/test_temporal_service.py
```

Result: `149 passed`; frontend suite invoked by the runner also passed with `20 passed`, `343 passed | 229 skipped`.

Focused integration-boundary validation after implementation:

```bash
./tools/test_unit.sh tests/integration/temporal/test_backend_resume_eligibility.py tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py
```

Result: `8 passed`; frontend suite invoked by the runner also passed with `20 passed`, `343 passed | 229 skipped`.

Full unit verification:

```bash
./tools/test_unit.sh
```

Result: `4938 passed, 1 xpassed, 115 warnings, 16 subtests passed`; frontend suite invoked by the runner also passed with `20 passed`, `343 passed | 229 skipped`. The warnings are pre-existing suite warnings unrelated to MM-646.

Hermetic integration verification:

```bash
./tools/test_integration.sh
```

Result: blocked by the managed runtime environment. Docker compose started the pytest image build path, then the Docker daemon returned:

```text
Error response from daemon: <html><body><h1>403 Forbidden</h1>
Request forbidden by administrative rules.
</body></html>
```

Smallest next step: rerun `./tools/test_integration.sh` in an environment where Docker compose image builds are allowed and the buildx plugin is available.

## Quickstart Validation

The quickstart unit and focused integration scenarios were exercised through the commands above:

- prepared input refs are recorded as compact refs and exposed on parent step ledger evidence.
- successful step output refs and checkpoint refs produce `resumePreservation.reason == "complete"`.
- completed steps missing output refs are marked ineligible with bounded reasons.
- Resume checkpoint payload validation rejects inline checkpoint payloads.
- delegated child/runtime output and checkpoint refs project into parent-owned evidence.

Full compose-backed integration remains blocked by the environment-level Docker 403 noted above.

## Review Notes

- Payloads remain ref-only for checkpoint evidence; no large or binary checkpoint content is stored inline.
- New preservation reasons are bounded machine-readable strings: `complete`, `missing_output_refs`, `missing_state_checkpoint`, and `not_completed`.
- No compatibility aliases or hidden fallback contract layers were added.
- Existing `contracts/step-ledger-checkpoint-evidence.md`, `data-model.md`, and `quickstart.md` already matched the implemented behavior; no wording changes were required.

## Final Moon Spec Verification

`/speckit.verify` was not run in this managed step because the active skill snapshot for this turn is `moonspec-implement` only and Skills On Demand is disabled. Run the `moonspec-verify` step after this implementation step to complete T034.
