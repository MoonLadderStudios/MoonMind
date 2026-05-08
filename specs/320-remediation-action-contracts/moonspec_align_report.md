# MoonSpec Alignment Report: Remediation Action Contracts

**Feature**: `specs/320-remediation-action-contracts`  
**Date**: 2026-05-08  
**Source**: MM-620 canonical Jira preset brief preserved in `spec.md`

## Updated

- `plan.md`: Aligned the integration testing strategy and source tree to require hermetic `integration_ci` coverage for the remediation action service/artifact boundary.
- `research.md`: Updated the integration strategy decision from conditional coverage to required artifact-boundary coverage, with rationale tied to DESIGN-REQ-015, DESIGN-REQ-016, DESIGN-REQ-017, and DESIGN-REQ-026.
- `quickstart.md`: Aligned the focused unit command with `tasks.md` and made integration testing required for the remediation action artifact boundary.

## Key Decisions

- Integration coverage wording: chose required hermetic integration coverage because `tasks.md` includes T011 through T013 and T023 for the durable request/result/verification artifact boundary, and the plan's requirement table already marks the source design mappings as requiring integration proof.
- Artifact regeneration: did not regenerate `spec.md` or `tasks.md` because the spec remains the higher-authority single-story contract and the task list already covered the required integration work. No downstream artifact was stale after the wording alignment.

## Remaining Risks

- Application implementation has not started. The known implementation risks remain the planned MM-620 gaps: result artifact completeness, result status validation, action input validation, raw-operation denial proof, and hermetic artifact-boundary evidence.

## Validation

- `.specify/scripts/bash/check-prerequisites.sh --json --require-tasks --include-tasks`: PASS.
- Artifact gate script: PASS; required artifacts exist, `tasks.md` has 31 sequential tasks, exactly one story phase, MM-620 traceability, unit and integration coverage, and final `/moonspec-verify` work.
- Placeholder scan: PASS; no unresolved placeholders found in generated artifacts. The only `NEEDS CLARIFICATION` occurrence is the checklist item asserting none remain.
