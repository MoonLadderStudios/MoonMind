# Contract: Skill Activation Refresh

## Scope

This contract covers the managed-runtime boundary for MM-615 after an on-demand Skill request has been approved and a derived Skill snapshot is ready to materialize.

## Activity Boundary

`agent_skill.request_on_demand` returns a compact `SkillsOnDemandRequestResult`.

Required successful activation shape:

```json
{
  "status": "activated",
  "code": null,
  "message": "Skills On Demand activated 1 requested Skill. Newly active Skills: example.",
  "active_snapshot_id": "skillset-active",
  "parent_snapshot_ref": "skillset-active",
  "snapshot_id": "skillset-derived",
  "resolved_skillset_ref": "artifact-or-snapshot-ref",
  "activation_summary": "Skills On Demand activated 1 requested Skill. Newly active Skills: example.",
  "materialization": {
    "mode": "workspace_mounted",
    "visible_path": "/run/.agents/skills",
    "manifest_ref": "artifact-or-manifest-ref"
  },
  "metadata": {
    "requested_skills": ["example"],
    "activated_skills": ["example"],
    "created_by": "skills_on_demand",
    "denied": false,
    "activation_timing": "atomic|next_turn|controlled_steer_point",
    "materialization_verified": true
  }
}
```

Required failure shape:

```json
{
  "status": "denied",
  "code": "materialization_failed|runtime_refresh_failed",
  "message": "Safe operator-facing failure summary.",
  "active_snapshot_id": "skillset-active",
  "parent_snapshot_ref": "skillset-active",
  "snapshot_id": null,
  "resolved_skillset_ref": null,
  "activation_summary": null,
  "materialization": null,
  "metadata": {
    "requested_skills": ["example"],
    "denied": true,
    "denial_code": "materialization_failed|runtime_refresh_failed"
  }
}
```

## Invariants

- `status: activated` is valid only after materialization and manifest/content verification have completed.
- `materialization_failed` means no runtime refresh or activation update should be treated as active.
- `runtime_refresh_failed` means materialization completed but the runtime-visible update failed; the previous active snapshot remains active.
- `activation_timing` must tell the managed runtime whether the derived snapshot is active immediately or must be loaded on the next turn or controlled steer point.
- Skill bodies, hidden catalog content, secrets, and arbitrary unrestricted artifact references must not appear in serialized results.
- External agents must not receive this activation contract in v1 unless equivalent authenticated controls, bounded metadata, immutable refs, governed materialization, and audit controls exist.

## Compatibility Notes

This is a Temporal-facing activity payload surface. Any field additions must preserve worker-bound invocation compatibility for in-flight runs or be introduced with an explicit versioned cutover plan.
