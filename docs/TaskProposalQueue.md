# Task Proposal Queue

Status: Updated
Owners: MoonMind Engineering
Last Updated: 2026-02-20
Related: `docs/TaskArchitecture.md`, `docs/TaskQueueSystem.md`, `docs/TaskUiArchitecture.md`, `api_service/static/task_dashboard/dashboard.js`

---

## 1. Summary

MoonMind already has the right primitive for follow-up proposals:

1. A proposal stores a canonical `taskCreateRequest`.
2. The promoted task executes against `taskCreateRequest.payload.repository`.
3. Dedup and notifications are repository-aware.

This update keeps that model and adds a lightweight policy layer so workers can intentionally generate:

1. Project follow-up proposals.
2. MoonMind CI/run-quality proposals.
3. Both, when appropriate.

No proposal-system rewrite is required.

---

## 2. Existing Behavior to Preserve

1. `taskCreateRequest` remains the canonical promote-to-task payload.
2. `taskCreateRequest.payload.repository` remains the execution target after promotion.
3. Dedup key remains based on `(repository + normalized title)`.
4. Notifications continue to include `proposal.repository` and priority.
5. Human review remains required before promotion.

---

## 3. New Proposal Generation Policy

### 3.1 Global policy knobs

Add the following config:

1. `MOONMIND_PROPOSAL_TARGETS=project|moonmind|both`
2. `MOONMIND_CI_REPOSITORY=MoonLadderStudios/MoonMind` (default)

Behavior:

1. `project`: workers only generate proposals targeting the task's project repository.
2. `moonmind`: workers only generate proposals targeting `MOONMIND_CI_REPOSITORY`.
3. `both`: workers may emit both types when signals match.

### 3.2 Per-task override (preferred)

Add `task.proposalPolicy` to the canonical task payload:

```json
{
  "task": {
    "proposalPolicy": {
      "targets": ["project", "moonmind"],
      "maxItems": {
        "project": 3,
        "moonmind": 2
      },
      "minSeverityForMoonMind": "high"
    }
  }
}
```

Rules:

1. `targets` overrides global `MOONMIND_PROPOSAL_TARGETS` for that task run.
2. `maxItems` caps proposal volume to prevent queue noise.
3. `minSeverityForMoonMind` blocks low-signal CI proposals.

If `proposalPolicy` is absent, server/worker fall back to global env defaults.

---

## 4. MoonMind CI Proposal Normalization

For proposals targeting `MOONMIND_CI_REPOSITORY`:

1. Use normalized category `run_quality` (alias: `moonmind_ci` allowed for migration only).
2. Require at least one signal tag from:
   `retry`, `duplicate_output`, `missing_ref`, `conflicting_instructions`, `flaky_test`, `loop_detected`, `artifact_gap`.
3. Keep title format concise and machine-sortable, for example:
   `[run_quality] Reduce duplicate output handling in orchestrator retry path`.

This ensures CI improvement proposals are filterable and consistently triaged.

---

## 5. Priority Routing for CI Proposals

Current gap: `create_proposal()` defaults review priority to `NORMAL`.

Recommended change:

1. Server derives `reviewPriority` from signal metadata when target is `moonmind`.
2. Client-supplied `reviewPriority` is optional and can be accepted, but server rules win on conflict.

Example server mapping:

1. `HIGH`: loop detected, repeated retry exhaustion, missing required references, conflicting instruction execution.
2. `NORMAL`: moderate duplicate output, single retry cluster.
3. `LOW`: cosmetic or informational cleanup.

Fallback option:

1. If no server derivation is implemented yet, create proposal then call existing priority update endpoint.

---

## 6. Origin Metadata Requirements

When a proposal targets MoonMind CI, include explicit trigger context in `origin_metadata`:

```json
{
  "triggerRepo": "org/external-project",
  "triggerJobId": "0e8f1f2f-...",
  "triggerStepId": "verify",
  "signal": {
    "retries": 2,
    "duplicateRatio": 0.78,
    "missingPaths": ["specs/plan.md"]
  }
}
```

Required fields:

1. `triggerRepo`
2. `triggerJobId`
3. `signal`

Optional fields:

1. `triggerStepId`
2. Any detector-specific fields (`loopSpan`, `missingRefs`, `conflictClasses`).

This preserves attribution: the task targets MoonMind, while reviewers can see which external run exposed the issue.

---

## 7. Proposal Creation Rules

Worker-side generation should follow this decision order:

1. Determine effective targets from `task.proposalPolicy.targets` else global `MOONMIND_PROPOSAL_TARGETS`.
2. For project targets, use task repository from canonical payload.
3. For moonmind targets, set proposal repository to `MOONMIND_CI_REPOSITORY`.
4. Emit moonmind proposals only when severity meets `minSeverityForMoonMind`.
5. Enforce `maxItems` limits separately for each target class.

Objective trigger examples for MoonMind proposals:

1. Retry count above threshold.
2. Duplicate output ratio above threshold.
3. Missing required reference paths during execution.
4. Conflicting instruction classes detected in one run.
5. Repeat failure pattern across recent runs.

---

## 8. API and Schema Delta

Minimal additions:

1. Extend canonical task payload schema with optional `task.proposalPolicy`.
2. Keep existing `POST /api/proposals` contract, but support normalized CI metadata and derived priority behavior.
3. Preserve proposal promotion flow unchanged (`POST /api/proposals/{id}/promote`).

Optional but useful:

1. Accept `reviewPriority` in `POST /api/proposals` for explicit callers.
2. Apply server-side guardrails to prevent low-severity MoonMind CI spam.

---

## 9. Migration and Rollout

1. Rename documentation to `docs/TaskProposalQueue.md`.
2. Land schema/config support for `proposalPolicy` and global env knobs.
3. Add worker generation logic for target selection and signal gating.
4. Add CI category/tag normalization and priority derivation.
5. Update dashboard filters to include `repository`, `category=run_quality`, and CI signal tags.

Rollout can be incremental because existing proposal creation and promotion semantics remain valid.

---

## 10. Acceptance Criteria

1. A run can generate project-only, moonmind-only, or dual-target proposals via policy.
2. MoonMind-targeted proposals always use `MOONMIND_CI_REPOSITORY`.
3. MoonMind-targeted proposals include required `origin_metadata` context.
4. CI/run-quality proposals are categorized and tagged consistently.
5. Priority for CI proposals is automatically elevated when high-severity signals are present.
6. Existing proposal promotion and dedup behavior remain intact.

## 11. Release Notes

- Added global `MOONMIND_PROPOSAL_TARGETS`/`MOONMIND_CI_REPOSITORY` knobs plus per-task `proposalPolicy` overrides so workers can route proposals deterministically.
- Worker policy evaluation now enforces severity floors, per-target slot caps, `[run_quality]` titles with tag slugs, and origin metadata enrichment before any MoonMind submission.
- API/service validation rejects malformed MoonMind payloads, derives priority with override provenance, and stores `priority_override_reason` for downstream dashboards.
- Dashboard now exposes repository/category/tag filters, shows derived priority badges with override tooltips, and surfaces origin metadata and signal payloads inline for run-quality triage.
