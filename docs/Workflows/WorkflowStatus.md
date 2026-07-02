# Workflow Status Model

Status: Archived pointer  
Owners: MoonMind Engineering  
Last Updated: 2026-04-04

This file is no longer the canonical source for MoonMind Workflow status design.

Use these docs instead:

- `docs/Temporal/StatusDomainMatrix.md` for the canonical cross-domain status matrix, token ownership, formatting rules, and non-destructive audit action vocabulary
- `docs/Temporal/VisibilityAndUiQueryModel.md` for execution-level `mm_state`, dashboard-status mapping, Memo/Search Attribute rules, and `mm_updated_at`
- `docs/Temporal/WorkflowExecutionProductModel.md` for the canonical Workflow Execution product model
- `docs/Temporal/StepLedgerAndProgressModel.md` for step-level statuses, attempts, checks, and latest-run rules

Why this file was archived:

- it proposed a separate `1:1 DB ↔ dashboard` status strategy that no longer matches the active exact-vs-compatibility model
- it did not separate Workflow status from step status
- it treated `mm_phase` and related state simplification as the primary direction, which is not the current canonical contract

Retention rule:

- keep this file only as a migration pointer during doc cleanup
- do not use it as a source document for new implementation or product decisions
