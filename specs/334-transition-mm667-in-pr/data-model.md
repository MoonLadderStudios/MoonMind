# Data Model: Transition MM-667 to "In Progress"

This story does not introduce new persistent storage. The "data model" here is the in-memory shape of the inputs to the trusted Jira tools and the outputs that compose the run report.

## Entities

### Jira Issue (MM-667)

| Field | Type | Notes |
|---|---|---|
| `key` | string | Always equals `"MM-667"` for this story; never substituted. |
| `status.name` | string | Pre-transition workflow status name. Used for the no-op check (FR-006) and the `priorStatus` field in the run report. |
| `status.statusCategory.key` | string | Optional; informational only. Not used for matching the target name `"In Progress"`. |

### Workflow Transition (Jira `transitions[*]`)

| Field | Type | Notes |
|---|---|---|
| `id` | string | Identifier passed to `jira.transition_issue`. |
| `name` | string | Transition name (the verb), e.g. `"Start work"`. Not the target status name. |
| `to.name` | string | The **target status name** that this transition leads to. This is what the agent matches against `"In Progress"` (case-insensitive, trimmed). |
| `to.statusCategory.key` | string | Optional; informational only. |
| `fields` | object \| null | Present when discovery is called with `expand_fields=true`. Each entry has a `required` boolean used by the missing-required-fields check. |

### Outcome (Run Report Block)

| Field | Type | Notes |
|---|---|---|
| `issueKey` | string | Always `"MM-667"`. |
| `priorStatus` | string \| null | Status name observed before the transition (or before the no-op check). `null` if `issue_not_found` or `tool_unavailable`. |
| `action` | enum | One of: `transitioned`, `noop_already_in_progress`, `stopped`. |
| `outcome` | enum | One of the ten named outcomes in `research.md` ("Error Surface and Named Outcomes"). |
| `transition.id` | string \| null | Set only when `action="transitioned"`. |
| `transition.name` | string \| null | Set only when `action="transitioned"`. |
| `transition.toStatusName` | string \| null | Set only when `action="transitioned"`. |
| `verifiedFinalStatus` | string \| null | Set when a post-transition `jira.get_issue` call ran. |
| `availableTransitions` | list[{id, name, toStatusName}] | Populated for `stopped:no_matching_transition` and `stopped:ambiguous_transition`. |
| `missingFields` | list[string] | Populated for `stopped:missing_required_fields`. Field IDs only; never field values. |
| `errorClass` | string \| null | Sanitized error class name (e.g. `JiraToolError`). |
| `errorReason` | string \| null | Redacted, ≤ 500 chars; flows through `redact_sensitive_text` and `SecretRedactor`. |

## Relationships and Validation Rules

- **Issue ↔ Transition** is a one-to-many relation per call to `jira.get_transitions`; this story consumes only the transitions for `MM-667` returned in a single discovery response.
- **Outcome.action vs Outcome.outcome** must be consistent:
  - `action="transitioned"` ⇒ `outcome="transitioned"`, `transition.*` set, `verifiedFinalStatus` set, `missingFields=[]`.
  - `action="noop_already_in_progress"` ⇒ `outcome="noop_already_in_progress"`, `transition.*` is `null`, `verifiedFinalStatus` equals `priorStatus`.
  - `action="stopped"` ⇒ `outcome` starts with `stopped:`, `transition.*` is `null`, `verifiedFinalStatus` may be `null` (or set when the failure is `final_status_mismatch`).
- **Mutation guarantee**: `transition.*` set ↔ exactly one `jira.transition_issue` call was issued, with `issue_key="MM-667"`, `transition_id=<the matched id>`, `fields={}`, `update={}`.
- **Single-issue invariant**: Across the entire run, `issueKey` in every Jira tool call equals `"MM-667"`. No other key is read or modified.
- **Secret hygiene**: `errorReason` and any embedded transition payload echo MUST NOT contain secret-pattern strings (`ghp_`, `github_pat_`, `AIza`, `ATATT`, `AKIA`, `Bearer `, `token=`, `password=`, raw private key blocks).

## State Transitions (Outcome Decision Tree)

```text
get_issue(MM-667)
├── error: not found / not visible              → stopped:issue_not_found
├── error: auth / permission                    → stopped:auth_or_permission
├── error: tool unavailable                     → stopped:tool_unavailable
├── error: validation / non-auth 4xx           → stopped:validation_failure
├── error: transient (rate-limit / 5xx)         → stopped:transient_failure
└── ok: priorStatus
    ├── priorStatus matches "In Progress"       → noop_already_in_progress
    └── priorStatus does not match
        get_transitions(MM-667, expand_fields=true)
        ├── error: any of the above             → corresponding stopped:* outcome
        └── ok: transitions[]
            matches = [t for t in transitions if t.to.name lower trimmed == "in progress"]
            ├── len(matches) == 0               → stopped:no_matching_transition
            ├── len(matches) > 1                → stopped:ambiguous_transition
            └── len(matches) == 1
                missing = required_fields(matches[0]) - {} (no defaults)
                ├── missing != []               → stopped:missing_required_fields
                └── missing == []
                    transition_issue(MM-667, matches[0].id, fields={}, update={})
                    ├── error: any tool error   → corresponding stopped:* outcome
                    └── ok
                        get_issue(MM-667)
                        ├── verifiedFinalStatus matches "In Progress"  → transitioned
                        └── verifiedFinalStatus mismatch                → stopped:final_status_mismatch
```

This tree is the canonical reference for the inline matching logic. The run report MUST follow exactly one branch and emit exactly one outcome.
