# Contract: Skills On Demand Request

## Activity

`agent_skill.request_on_demand`

## Request

```json
{
  "current_snapshot_ref": "skillset-active",
  "requested_skills": [
    {
      "name": "jira-issue-updater",
      "version": "1.0.0"
    }
  ],
  "reason": "Need Jira update workflow for this step",
  "runtime_id": "codex",
  "step_id": "step-123",
  "active_snapshot": {
    "snapshot_id": "skillset-active",
    "resolved_at": "2026-05-08T00:00:00Z",
    "skills": []
  }
}
```

## Result: Activated

```json
{
  "status": "activated",
  "code": null,
  "message": "Activated 1 requested Skill.",
  "active_snapshot_id": "skillset-active",
  "parent_snapshot_ref": "skillset-active",
  "snapshot_id": "skillset-derived",
  "resolved_skillset_ref": "artifact-manifest",
  "activation_summary": "Skills On Demand activated 1 requested Skill. Newly active Skills: jira-issue-updater.",
  "materialization": {
    "mode": "workspace_mounted",
    "visible_path": ".agents/skills",
    "manifest_ref": "artifact-manifest"
  },
  "metadata": {
    "requested_skills": ["jira-issue-updater"],
    "activated_skills": ["jira-issue-updater"],
    "created_by": "skills_on_demand"
  }
}
```

## Result: No Change

```json
{
  "status": "no_change",
  "code": "already_active",
  "message": "All requested Skills are already active.",
  "active_snapshot_id": "skillset-active",
  "parent_snapshot_ref": "skillset-active",
  "snapshot_id": null,
  "resolved_skillset_ref": "artifact-parent-manifest",
  "activation_summary": "All requested Skills are already active in the current snapshot.",
  "materialization": null,
  "metadata": {
    "requested_skills": ["jira-issue-updater"],
    "activated_skills": []
  }
}
```

## Result: Denied

```json
{
  "status": "denied",
  "code": "invalid_request",
  "message": "current_snapshot_ref is required when Skills On Demand is enabled.",
  "active_snapshot_id": "skillset-active",
  "parent_snapshot_ref": "skillset-active",
  "snapshot_id": null,
  "resolved_skillset_ref": null,
  "activation_summary": null,
  "materialization": null,
  "metadata": {
    "requested_skills": [],
    "denied": true
  }
}
```

## Safety Rules

- Denied results never return a derived snapshot id.
- Activated and no-change results return compact refs and summaries, not Skill bodies.
- `requires_approval` is reserved and must not be emitted by this v1 story.
- The previous active snapshot remains authoritative unless the result is `activated`.
