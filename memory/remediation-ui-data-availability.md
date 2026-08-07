---
name: remediation-ui-data-availability
description: Which remediation-link/approval fields are actually backed by real data today, bounding what the remediation UI (issue #3623) can truthfully surface
metadata:
  type: project
---

For the Omnigent remediation UI (GitHub MoonLadderStudios/MoonMind#3623), two non-obvious backend facts bound what the frontend can honestly render:

1. In `moonmind/workflows/temporal/service.py`, `list_remediations_for_target` / `list_remediation_targets` only attach **`checkpoint_branch_links`** (from `WorkflowCheckpointBranch`/`...Operation` tables) to each link. The other rich fields on `RemediationLinkSummaryModel` (`selected_steps`, `current_target_state`, `allowed_actions`, `evidence_degraded`, `live_observation`, `lock_outcome`, `approval_state`) are read via `getattr` in `_serialize_remediation_link_summary` (`api_service/api/routers/executions.py`) and resolve to None/defaults in production unless the service dynamically attaches them; tests inject them via `SimpleNamespace`.

2. Issue #3620's **durable approval owner does not exist yet**. The approval request is derived on the fly (`_remediation_approval_state_from_link`), and `record_remediation_approval_decision` only writes an intervention-audit line (no decision row). The approval endpoint `POST /api/executions/{workflowId}/remediation/approvals/{requestId}` already accepts `{decision, comment}`.

**Why:** several #3623 acceptance items (diagnosis, prevention, cleanup, per-evidence-class freshness, action expected-state/policy-decision/idempotency/before-after/delivery-outcome, backend action-readiness gating, approval expiration/expected-state/policy) have no data producer in the current repo — adding those API schema fields would be dead scaffolding, violating the simplicity gate.

**How to apply:** when extending remediation surfaces, first confirm a canonical producer exists (DB column, attached branch links, or run-summary artifact). Fields tied to prevention/verification/action-detail depend on #3620/#3621/#3622 landing first. See [[remediation-create-draft-flow]].
