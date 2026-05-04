# Contract: Create-page Submit Preset Auto-Expansion

## Scope

This contract defines the user-visible Create-page interaction and final task payload invariants for submit-time Preset auto-expansion.

## Trigger Contract

Submit-time auto-expansion is triggered only by:
- Primary Create click.
- Primary Update click.
- Primary Rerun click.

Submit-time auto-expansion is not triggered by:
- Selecting a Preset.
- Loading a Preset descriptor.
- Importing Jira or other external context.
- Uploading attachments.
- Manual Preset Preview.
- Navigation within the Create page.

## Expansion Request Shape

For each unresolved Preset step in authored order, the Create page sends an expansion request equivalent to:

```json
{
  "version": "selected-or-empty-for-system-resolution",
  "inputs": {
    "issueKey": "MM-123"
  },
  "context": {
    "repository": "MoonLadderStudios/MoonMind",
    "repo": "MoonLadderStudios/MoonMind",
    "branch": "main",
    "publishMode": "pr",
    "targetRuntime": "managed",
    "submitIntent": "create"
  },
  "options": {
    "enforceStepLimit": true,
    "intent": "submit-auto-expand"
  }
}
```

Rules:
- The request uses the selected Preset key and scope from the authored step.
- The request uses the selected version when present; otherwise, catalog resolution decides the latest visible active version or fails.
- The request uses current Preset input values and current task context.
- The request may include attachment refs only when expansion or generated field mapping requires them.
- The request must not infer another Preset from objective text, hidden user data, or unrelated draft fields.

## Expansion Response Handling

Expected successful response properties:
- `steps`: generated executable Tool and/or Skill steps.
- `warnings`: non-blocking warnings unless marked or interpreted as requiring review.
- `capabilities`: required capabilities contributed by the Preset.
- `appliedTemplate`: Preset provenance and applied-template metadata when available.

Rules:
- Generated steps replace the unresolved Preset placeholder only in the frozen submission copy.
- Multiple unresolved Presets expand and replace in authored order.
- Generated steps must preserve `source` provenance when returned.
- Generated steps must be equivalent to manual Apply output.
- Non-blocking warnings may be shown in submit progress or final feedback.
- Warnings requiring review block final submission and present the manual review path.

## Final Payload Invariants

The final create/update/rerun payload:
- Contains no step with `type: "preset"`.
- Contains only executable Tool and/or Skill step shapes.
- Contains no incompatible stale type-specific fields.
- Preserves available Preset provenance.
- Does not rely on live Preset lookup for runtime correctness.
- Preserves enough authoritative task input data for edit/rerun reconstruction.

## Failure Contract

The Create page blocks final submission and creates no create/update/rerun side effect when:
- Expansion is unavailable.
- Expansion is unauthorized.
- Required Preset inputs are invalid.
- Version resolution fails.
- Attachment retargeting is ambiguous.
- Required publish/merge constraints cannot be applied safely.
- Expansion returns a blocking warning.
- Expansion request is stale or cancelled.
- Expansion or final executable validation fails.

Failure UI:
- Shows a relevant error on or near the affected Preset step.
- Preserves the rest of the visible draft.
- Keeps manual Preview and Apply available.
- Ignores stale expansion responses.

## Guarding Contract

During submit-time expansion and final submission:
- The primary submit button is disabled or otherwise guarded.
- Duplicate clicks cannot create duplicate tasks.
- A later submit attempt supersedes earlier expansion responses.
- Cancellation or navigation causes later expansion results from the cancelled attempt to be ignored.

## Existing Authoritative Boundary

Task validation remains authoritative and continues to reject unresolved Preset steps. The Create-page convenience path is not the only enforcement boundary.
