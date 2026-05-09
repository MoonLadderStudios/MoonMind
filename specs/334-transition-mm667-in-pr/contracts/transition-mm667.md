# Contract: Tool Calls and Run Report for MM-667 → "In Progress"

This contract pins down the trusted-tool call sequence and the run-report shape. The agent MUST conform to this contract; deviations are bugs. All tool calls go through MoonMind's trusted Jira surface — no raw HTTP.

## Trusted Tool Calls (in order)

1. `jira.get_issue`
   ```json
   {
     "issue_key": "MM-667"
   }
   ```
   Required because the inline no-op check (FR-006) needs the current status before discovery. Response carries `status.name`, used to populate `priorStatus`.

2. `jira.get_transitions` (skipped only when step 1's `priorStatus` already matches `"In Progress"`)
   ```json
   {
     "issue_key": "MM-667",
     "expand_fields": true
   }
   ```
   `expand_fields=true` is required so the response carries `transitions[*].fields` metadata; without it, the missing-required-fields check (FR-007) cannot be performed.

3. `jira.transition_issue` (skipped on no-op or any `stopped:*` outcome)
   ```json
   {
     "issue_key": "MM-667",
     "transition_id": "<matched transition id>",
     "fields": {},
     "update": {}
   }
   ```
   `fields={}` and `update={}` are mandatory: the spec forbids any field mutation beyond the workflow status (FR-008). Only the matched `transition_id` is supplied.

4. `jira.get_issue` (verification, after step 3 only)
   ```json
   {
     "issue_key": "MM-667"
   }
   ```
   Response's `status.name` populates `verifiedFinalStatus`.

No other Jira tools are called in this story. Specifically: `jira.edit_issue`, `jira.add_comment`, and `jira.create_subtask` MUST NOT be called.

## Selection Logic (between calls 2 and 3)

```text
candidates = [
  t for t in response.transitions
  if str(t.to.name).strip().lower() == "in progress"
]

if len(candidates) == 0: emit stopped:no_matching_transition
elif len(candidates) > 1: emit stopped:ambiguous_transition
else:
  missing = [
    field_id
    for field_id, meta in (candidates[0].fields or {}).items()
    if isinstance(meta, dict) and meta.get("required")
  ]
  if missing: emit stopped:missing_required_fields with the missing list
  else: proceed to call 3 with transition_id = candidates[0].id
```

## Run Report Shape

```json
{
  "issueKey": "MM-667",
  "priorStatus": "<string|null>",
  "action": "transitioned | noop_already_in_progress | stopped",
  "outcome": "transitioned | noop_already_in_progress | stopped:no_matching_transition | stopped:ambiguous_transition | stopped:issue_not_found | stopped:missing_required_fields | stopped:auth_or_permission | stopped:tool_unavailable | stopped:transient_failure | stopped:final_status_mismatch",
  "transition": {
    "id": "<string|null>",
    "name": "<string|null>",
    "toStatusName": "<string|null>"
  },
  "verifiedFinalStatus": "<string|null>",
  "availableTransitions": [
    {"id": "<string>", "name": "<string>", "toStatusName": "<string>"}
  ],
  "missingFields": ["<fieldId>"],
  "errorClass": "<string|null>",
  "errorReason": "<redacted string ≤ 500 chars|null>"
}
```

### Field-Level Rules

- `issueKey` is always the literal string `"MM-667"`.
- `availableTransitions` is empty unless the outcome is `stopped:no_matching_transition` or `stopped:ambiguous_transition`.
- `missingFields` is empty unless the outcome is `stopped:missing_required_fields`.
- `errorReason` is empty for `transitioned` and `noop_already_in_progress` outcomes.
- All string values pass through the existing `redact_sensitive_text` / `SecretRedactor` filter before serialization. No secret-pattern token (`ghp_`, `github_pat_`, `AIza`, `ATATT`, `AKIA`, `Bearer `, `token=`, `password=`) appears anywhere in the report.

## Acceptance Trace

| Spec ID | Tool calls used | Run-report fields populated |
|---|---|---|
| SCN-001 (transition path) | 1, 2, 3, 4 | `action="transitioned"`, `outcome="transitioned"`, `transition.*`, `verifiedFinalStatus="In Progress"` |
| SCN-002 (already in-progress no-op) | 1 only | `action="noop_already_in_progress"`, `outcome="noop_already_in_progress"`, `priorStatus="In Progress"`, `verifiedFinalStatus="In Progress"` |
| SCN-003 (no matching transition) | 1, 2 | `action="stopped"`, `outcome="stopped:no_matching_transition"`, `availableTransitions` populated |
| SCN-004 (ambiguous transition) | 1, 2 | `action="stopped"`, `outcome="stopped:ambiguous_transition"`, `availableTransitions` populated with the candidates |
| Edge: tool unavailable | 0 | `action="stopped"`, `outcome="stopped:tool_unavailable"`, `errorClass`, `errorReason` |
| Edge: not found | 1 | `action="stopped"`, `outcome="stopped:issue_not_found"`, `errorClass`, `errorReason` |
| Edge: auth/permission | 1 | `action="stopped"`, `outcome="stopped:auth_or_permission"`, redacted error |
| Edge: transient | varies | `action="stopped"`, `outcome="stopped:transient_failure"` |
| Edge: post-transition mismatch | 1, 2, 3, 4 | `action="stopped"`, `outcome="stopped:final_status_mismatch"`, `verifiedFinalStatus=<observed>` |
| Edge: required fields | 1, 2 | `action="stopped"`, `outcome="stopped:missing_required_fields"`, `missingFields` populated |
