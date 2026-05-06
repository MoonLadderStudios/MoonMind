# MoonSpec Breakdown Summary

## MM-593 - 2026-05-06

Source design: Jira preset brief for MM-593.

Coverage gate result: PASS - every major design point is owned by at least one story.

Recommended first generated spec: `specs/308-schema-driven-capability-inputs/spec.md`.

Generated/selected specs:

- `specs/308-schema-driven-capability-inputs/spec.md` - Schema-Driven Capability Inputs. Independent test: select a capability with schema/UI metadata and verify generated fields, Jira issue picker behavior, validation, and absence of capability-specific Create-page branches.

Deferred or existing specs:

- Preview/apply preset behavior: `specs/278-preview-apply-preset-steps`, `specs/284-preview-apply-preset-executable-steps`, `specs/291-preview-apply-preset-steps`.
- Flattened executable submission/provenance: `specs/292-submit-flattened-executable-steps-with-provenance`.
- Submit-time preset auto-expansion: `specs/295-submit-preset-auto-expansion`.
- Recursive preset expansion guardrails: reconcile with existing composable preset expansion coverage before creating another spec.

Coverage matrix: DESIGN-REQ-001 through DESIGN-REQ-005 are owned by `308-schema-driven-capability-inputs`; DESIGN-REQ-006 is owned by existing preview/apply specs; DESIGN-REQ-007 is owned by `292-submit-flattened-executable-steps-with-provenance`; DESIGN-REQ-008 is owned by `295-submit-preset-auto-expansion`; DESIGN-REQ-009 remains a follow-up or existing composable expansion reconciliation story.
