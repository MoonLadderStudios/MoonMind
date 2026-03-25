# Quickstart: Step Approval Policy

## Enable Approval Policy on a Plan

Add to a plan's JSON policy:

```json
{
  "policy": {
    "approval_policy": {
      "enabled": true,
      "max_review_attempts": 2,
      "reviewer_model": "default"
    }
  }
}
```

## Enable via Workflow Parameters

Include in `initialParameters` when creating a `MoonMind.Run`:

```json
{
  "initialParameters": {
    "approvalPolicy": {
      "enabled": true
    }
  }
}
```

## Enable via Environment Variable

Set `MOONMIND_REVIEW_GATE_DEFAULT_ENABLED=true` in the worker environment. All workflows without explicit plan/workflow-level config will have the gate enabled.

## Skip Specific Tool Types

Exempt `agent_runtime` nodes from review:

```json
{
  "policy": {
    "approval_policy": {
      "enabled": true,
      "skip_tool_types": ["agent_runtime"]
    }
  }
}
```

## Verify Approval Policy is Active

Check the workflow memo during execution — it will show:

```
Executing plan step 2/5: repo.apply_patch (review attempt 1/3)
```

After completion, the finish summary includes review metrics:

```json
{
  "approvalPolicy": {
    "enabled": true,
    "stepsReviewed": 5,
    "totalReviewAttempts": 8
  }
}
```
