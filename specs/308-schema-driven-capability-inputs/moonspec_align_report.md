# MoonSpec Align Report: Schema-Driven Capability Inputs

**Feature**: `308-schema-driven-capability-inputs`
**Source issue**: MM-593
**Date**: 2026-05-06

## Remediations

| Finding | Remediation |
| --- | --- |
| `plan.md` and `tasks.md` mapped `DESIGN-REQ-007` to capability-specific branch removal, but `spec.md` defines `DESIGN-REQ-007` as Jira credential and secret isolation. | Updated `DESIGN-REQ-007` status and task mappings to cover credential isolation, schema-default redaction, and secret-safety verification. Remapped capability-specific branch removal tasks to `DESIGN-REQ-001`, `DESIGN-REQ-002`, and `DESIGN-REQ-008`. |
| `DESIGN-REQ-005` was treated as implemented-unverified security coverage, but `spec.md` defines it as safe Jira issue value, optional enrichment, and trusted validation/enrichment. | Updated `plan.md` status to `partial` and remapped Jira issue value/enrichment tests and implementation tasks to `DESIGN-REQ-005`. |
| The contract and research artifacts used migration/compatibility wording that could imply compatibility aliases in a pre-release internal contract. | Reworded the contract and research notes so schema contracts are authoritative when present and no compatibility aliases or duplicate capability-specific branches are introduced. |
| `quickstart.md` did not include the final MoonSpec verification command represented in `tasks.md`. | Added `/moonspec-verify` as the final verification step. |

## Validation

- Prerequisites: `SPECIFY_FEATURE=308-schema-driven-capability-inputs .specify/scripts/bash/check-prerequisites.sh --json --require-tasks --include-tasks` passed.
- Task shape: 48 sequential tasks, one story phase, zero task-format violations.
- Coverage: all in-scope FR, SCN, SC, and DESIGN-REQ IDs are covered in `tasks.md`; `DESIGN-REQ-009` remains explicitly out of scope.
- Verification wording: `/moonspec-verify` is present in `tasks.md` and `quickstart.md`; `/speckit.verify` is absent from this feature artifact set.
- Placeholder scan: no unresolved placeholders remain in generated feature artifacts, excluding checklist prose that names the placeholder pattern as a completed checklist item.
