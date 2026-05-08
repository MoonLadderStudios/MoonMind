# Contract: `moonmind.skills.query`

## Request

```json
{
  "query": "jira",
  "runtime_id": "codex",
  "current_snapshot_ref": "skillset-active",
  "max_results": 20
}
```

Optional boundary callers may provide compact active snapshot context when it is already available to the activity/service. That context is used only for `in_current_snapshot` calculation and must not be persisted or mutated by query handling.

## Successful Response

```json
{
  "status": "ok",
  "message": "Returned 1 Skill metadata result.",
  "results": [
    {
      "name": "jira-issue-updater",
      "title": "Jira Issue Updater",
      "description": "Update Jira issues through MoonMind trusted Jira tools.",
      "latest_version": "1.0.0",
      "source_kind": "built_in",
      "supported_runtimes": ["codex"],
      "eligible": true,
      "in_current_snapshot": false,
      "eligibility_summary": "Eligible for this runtime and deployment policy."
    }
  ],
  "metadata": {
    "result_count": 1,
    "denied": false
  }
}
```

## Denied Response

```json
{
  "status": "denied",
  "code": "feature_disabled",
  "message": "Skills On Demand is disabled for this deployment.",
  "results": [],
  "metadata": {
    "result_count": 0,
    "denied": true,
    "denial_code": "feature_disabled"
  }
}
```

## Field Rules

- `results` must never include Skill body text.
- `results` must never include `content_ref`, `content_digest`, source file paths, artifact ids, or arbitrary read handles.
- `results.length` must never exceed the accepted `max_results`.
- Ineligible results may appear only with `eligible: false` and a safe `eligibility_summary`.
- Query calls must not create, mutate, or materialize Skill snapshots.
