# Data Model: Step Ledger Phase 5

## ApprovalPolicyStepCheck

Phase 5 makes approval policy the first concrete `checks[]` producer.

| Field | Type | Description |
| --- | --- | --- |
| `kind` | `"approval_policy"` | Stable check kind for review-gate output |
| `status` | `pending \| passed \| failed \| inconclusive` | Current/final review verdict state shown in the UI |
| `summary` | string \| null | Bounded operator-facing summary of the latest review state |
| `retryCount` | integer | Number of failed reviews that triggered a retry for the logical step |
| `artifactRef` | string \| null | Artifact containing full review request/verdict/issues payload |

## Review lifecycle on one logical step row

1. Step execution begins: row status becomes `running`, `attempt` increments.
2. Eligible completed execution enters review: row status becomes `reviewing`; `checks[]` contains a pending `approval_policy` row.
3. Review verdict is stored:
   - `PASS`: check becomes `passed`, row later becomes `succeeded`
   - `FAIL` with retries remaining: check becomes `failed`, `retryCount` increments, feedback is injected into the rerun, and the same logical step reruns
   - `INCONCLUSIVE`: check becomes `inconclusive`, but the workflow accepts the execution
4. Full request/verdict/issue detail is stored in a JSON artifact referenced by `artifactRef`

## ReviewEvidenceArtifact

Suggested JSON payload shape written by the workflow:

```json
{
  "logicalStepId": "apply-patch",
  "attempt": 2,
  "reviewAttempt": 2,
  "request": { "...": "bounded copy of review request payload" },
  "verdict": {
    "verdict": "FAIL",
    "confidence": 0.82,
    "feedback": "Tests still fail because imports are missing.",
    "issues": [
      {
        "severity": "error",
        "description": "Missing import",
        "evidence": "stderr tail"
      }
    ]
  }
}
```

The workflow row stores only:

- latest bounded summary
- `retryCount`
- latest `artifactRef`

## UI rendering additions

The existing Checks group adds two small pieces of structured metadata per check row:

1. `Retry count: N`
2. `Review artifact: <artifactRef>` or explicit empty-state copy

The rest of the expanded step surface remains unchanged from Phase 4.
