# Data Model: Post-Merge Jira Completion

## Merge Automation Post-Merge Jira Config

Represents optional runtime configuration controlling Jira completion after merge success.

Fields:

- `enabled`: boolean flag. When true, merge automation evaluates post-merge Jira completion after merge success.
- `issueKey`: optional explicit Jira issue key override. If present, it is the highest-precedence candidate.
- `transitionId`: optional explicit Jira transition ID override.
- `transitionName`: optional explicit Jira transition name override.
- `strategy`: completion strategy. Initial supported value: `done_category`.
- `required`: boolean. When true, unresolved or failed Jira completion prevents merge automation terminal success.
- `fields`: optional transition field payload for workflows requiring fields such as resolution.

Validation rules:

- `transitionId` and `transitionName` may not both be set unless they resolve to the same currently available transition.
- Unsupported `strategy` values fail validation instead of falling back silently.
- `fields` must remain compact and JSON-serializable.

## Jira Issue Candidate

Represents one possible target issue for post-merge completion.

Fields:

- `issueKey`: Jira issue key string.
- `source`: one of `explicit_post_merge`, `merge_automation`, `task_origin`, `task_metadata`, `publish_context`, or `pr_metadata`.
- `validated`: whether the key was fetched successfully through the trusted Jira integration.
- `statusName`: optional current status name from trusted Jira data.
- `statusCategory`: optional current Jira status category key or name.
- `reason`: optional validation failure reason.

Validation rules:

- Empty or malformed keys are invalid.
- Candidate validation must happen through the trusted Jira integration boundary.
- Multiple candidates with the same normalized key collapse into one candidate with preserved source evidence.

## Jira Issue Resolution

Represents the authoritative target selection result.

Fields:

- `status`: one of `resolved`, `missing`, `ambiguous`, or `invalid`.
- `issueKey`: selected issue key when status is `resolved`.
- `source`: selected source when status is `resolved`.
- `candidates`: bounded list of candidate evidence.
- `reason`: operator-visible reason for missing, invalid, or ambiguous outcomes.

State transitions:

- `missing` when no usable candidates exist.
- `invalid` when candidates exist but none validate through trusted Jira.
- `ambiguous` when multiple different candidate keys validate.
- `resolved` when exactly one validated key remains.

## Completion Transition Candidate

Represents a Jira workflow transition available for the selected issue.

Fields:

- `transitionId`: Jira transition ID returned by trusted transition discovery.
- `name`: transition display name.
- `toStatusName`: target status name.
- `toStatusCategory`: target status category key or name.
- `requiredFields`: compact map of transition fields required by Jira.

Validation rules:

- Explicit transition IDs must be present in the current available transition list.
- Explicit transition names require case-insensitive exact match.
- Automatic selection only succeeds when exactly one candidate targets the done category and has no unmet required fields.

## Post-Merge Jira Completion Decision

Represents the final decision for one merge automation completion attempt.

Fields:

- `status`: one of `succeeded`, `noop_already_done`, `blocked`, `failed`, or `skipped`.
- `issueResolution`: compact Jira Issue Resolution.
- `transitionId`: selected transition ID when applicable.
- `transitionName`: selected transition name when applicable.
- `alreadyDone`: boolean.
- `transitioned`: boolean.
- `required`: boolean.
- `reason`: operator-visible reason for no-op, blocked, failed, or skipped outcomes.
- `artifactRefs`: optional refs to persisted evidence artifacts.

State transitions:

- `skipped` when the run is not Jira-backed or post-merge completion is disabled.
- `noop_already_done` when the selected issue is already in a done-category state.
- `succeeded` when the transition is applied successfully.
- `blocked` when required completion cannot safely select an issue or transition.
- `failed` when trusted Jira execution fails after selection.

## Completion Evidence Artifact

Durable operator-visible artifact written by merge automation.

Recommended paths:

- `artifacts/merge_automation/post_merge_jira_resolution.json`
- `artifacts/merge_automation/post_merge_jira_transition.json`

Validation rules:

- Evidence must not include raw credentials, auth headers, cookies, or full Jira account payloads.
- Evidence must include enough data to explain target selection, transition selection, and final outcome.
- Evidence must remain compact enough for artifact output and not be embedded as large payloads in workflow history.
