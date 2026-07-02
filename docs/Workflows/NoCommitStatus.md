# No Commit Workflow Status

Status: Active  
Owners: MoonMind Engineering  
Last updated: 2026-06-28

Canonical for: `no_commit` lifecycle semantics, `NO_COMMIT` finish outcome semantics, publish-mode PR runs that complete without creating a repository commit, and side-effectful workflows that should not be described as "no changes."

Related:

- `docs/Workflows/WorkflowFinishSummarySystem.md` — finish summary artifact and outcome-code contract.
- `docs/Temporal/VisibilityAndUiQueryModel.md` — `mm_state`, exact state, and compatibility dashboard grouping.
- `docs/Api/ExecutionsApiContract.md` — execution API state surface.
- `docs/UI/WorkflowStatusColorSemantics.md` — status color grouping and display rationale.

---

## 1. Purpose

MoonMind workflows can complete useful work without creating a repository commit. The clearest canonical example is a Jira Implement preset run that determines the requested repository work is already implemented and then updates the Jira issue, such as moving it to Done.

Calling that outcome **No Changes** is misleading because the workflow may have performed non-repository side effects. The correct meaning is narrower:

> The workflow completed successfully, but no repository commit was created because there were no repository changes to commit.

Use **No Commit** for that outcome.

---

## 2. Canonical terms

| Layer | Canonical value | Meaning |
| --- | --- | --- |
| Exact lifecycle state | `no_commit` | Terminal completed-without-commit state: workflow completed without creating a repository commit. |
| Finish outcome code | `NO_COMMIT` | Structured finish-summary outcome for the same condition. |
| Publish status | `skipped` | Publish stage intentionally skipped branch/PR creation because no commit was needed. |
| Publish reason code | `no_commit` | Stable machine-readable publish reason. |
| Compatibility dashboard grouping | `completed` | The workflow did not fail or cancel; it is grouped with terminal successful outcomes for coarse filters. |
| Temporal/API close status | `completed` | The execution reached a successful terminal condition. |

`NO_CHANGES` is a legacy alias only. New code, docs, UI labels, and finish summaries should use `NO_COMMIT` / `no_commit`.

---

## 3. When to use `no_commit`

Use `no_commit` when all of the following are true:

1. The workflow reached a normal terminal path.
2. The workflow was in a repository-publishing context, usually `publish.mode = "pr"` or `publish.mode = "branch"`.
3. The repository workspace had no commit-worthy diff at the publish boundary.
4. No branch or pull request was created by the publish stage.
5. The absence of a commit was a valid outcome, not a technical publishing failure.
6. Any non-repository side effects either completed successfully or are represented separately in side-effect output metadata.

Do **not** use `no_commit` when:

- `publish.mode = "none"`; use `PUBLISH_DISABLED` instead.
- The workflow failed before it could determine repository commit eligibility; use `failed` / `FAILED`.
- Commit or PR creation failed for a technical reason; use `failed` / `FAILED` with `finishOutcome.stage = "publish"`.
- A branch or pull request was created; use `PUBLISHED_BRANCH` or `PUBLISHED_PR`.
- The user canceled or force-canceled the workflow; use `canceled` / `CANCELLED`.
- A side-effect that is required for success failed; use `failed` unless the preset explicitly defines that side effect as best-effort.

---

## 4. Canonical Jira Implement example

A Jira Implement preset run may behave like this:

1. The workflow starts from a Jira issue and runs with publish mode PR.
2. The implementation step inspects the repository and determines the requested work is already present.
3. The preset performs a Jira side effect, for example transitioning the issue to Done or writing a verification comment.
4. The publish stage checks the repository and finds no commit-worthy diff.
5. The workflow terminalizes as `no_commit`, not `completed` and not `failed`.

The operator-facing summary should communicate both facts:

```text
No commit was created because the repository already matched the request. Jira was updated successfully.
```

The outcome must not be summarized as:

```text
No changes.
```

That wording hides side effects and makes a Jira transition look invisible.

---

## 5. Required structured result shape

A `no_commit` terminal result should be represented with structured fields rather than summary-string parsing:

```json
{
  "state": "no_commit",
  "closeStatus": "completed",
  "temporalStatus": "completed",
  "dashboardStatus": "completed",
  "finishOutcome": {
    "code": "NO_COMMIT",
    "stage": "publish",
    "reason": "No repository commit was needed."
  },
  "publish": {
    "mode": "pr",
    "status": "skipped",
    "reasonCode": "no_commit",
    "reason": "No repository changes were available to commit or publish.",
    "commitCreated": false,
    "branchPushed": false,
    "prUrl": null
  },
  "sideEffects": [
    {
      "kind": "jira",
      "status": "completed",
      "summary": "Issue transitioned to Done."
    }
  ]
}
```

Rules:

1. `state = "no_commit"` is the exact lifecycle state shown in exact-state UI surfaces.
2. `closeStatus = "completed"` because the workflow reached a valid terminal outcome.
3. `dashboardStatus = "completed"` for broad compatibility grouping.
4. `finishOutcome.code = "NO_COMMIT"` is the canonical outcome-code spelling.
5. `publish.status = "skipped"` must be paired with `publish.reasonCode = "no_commit"` so skipped publication is not confused with dry-run, policy skip, validation failure, or credentials failure.
6. Side effects belong in a separate structured block. Do not infer side effects from `finishOutcome.reason`.
7. `prUrl` must be `null` unless a PR actually exists.

---

## 6. Publish-stage behavior

The publish implementation should return a structured publish result, not just a prose note.

Recommended shape:

```python
PublishResult(
    mode="pr",
    status="skipped",
    reason_code="no_commit",
    reason="No repository changes were available to commit or publish.",
    commit_created=False,
    branch_pushed=False,
    pr_url=None,
    branch_name=None,
)
```

The workflow finalization layer then maps that result to:

```text
state: no_commit
closeStatus: completed
finishOutcome.code: NO_COMMIT
finishOutcome.stage: publish
publish.status: skipped
publish.reasonCode: no_commit
```

This avoids parsing strings such as `publish skipped: no local changes` and keeps the UI, API projection, and run-summary artifact consistent.

---

## 7. Interaction with non-repository side effects

`no_commit` says nothing about whether non-repository side effects happened. A workflow may be `no_commit` and still have meaningful effects, including:

- Jira issue transition or comment updates;
- GitHub issue or PR comment updates;
- tracker verification records;
- artifact publication;
- notification or reporting side effects;
- future preset-specific external actions.

Those side effects should be exposed explicitly through bounded side-effect summaries or preset output metadata.

A workflow with required side effects should not be marked `no_commit` if those side effects failed. In that case the correct terminal state is `failed`, with the side-effect failure captured in the failure diagnostic.

---

## 8. UI presentation

Primary label:

```text
No commit
```

Preferred detail copy:

```text
No commit was created because no repository changes were needed.
```

When side effects are known:

```text
No commit · Jira updated
```

Avoid:

- `No changes`
- `Completed` by itself when the workflow was launched in publish mode
- `No publish` without a reason code

Color and grouping are defined in `docs/UI/WorkflowStatusColorSemantics.md`.

---

## 9. Backward compatibility

Existing persisted artifacts, projections, or compatibility clients may still emit `NO_CHANGES` or `no_changes`.

Source traceability: MM-1073 established the canonical `no_commit` / `NO_COMMIT` model; MM-1082 bounds the remaining legacy-alias quarantine and repair path.

Explicit compatibility maps:

```text
LEGACY_WORKFLOW_STATE_ALIASES:
  no_changes -> no_commit

LEGACY_FINISH_OUTCOME_ALIASES:
  NO_CHANGES -> NO_COMMIT
```

Compatibility is allowed only at inbound or durable-history boundaries:

- Temporal Visibility `mm_state` reads may repair `no_changes` to `no_commit`.
- Terminal-state activity inputs may repair legacy workflow histories that pass `state=no_changes`.
- Finish-summary artifacts, memo payloads, and API serialization may repair `finishOutcome.code=NO_CHANGES` and `publish.reasonCode=no_changes`.
- Automation-run repository reads and writes may repair old `automation_runs.status=no_changes` rows to the canonical `no_commit` value.

Direct canonical-domain callers must reject legacy aliases instead of silently accepting them. Provider, billing, model, effort, runtime, credential, and publish-policy values are not part of this compatibility rule and must not be translated through these maps.

Alias observation must log only bounded fields: `domain`, `alias`, and `canonical`. It must not log full finish summaries, search-attribute maps, provider payloads, prompts, credentials, or large artifacts.

### 9.1 Persisted inventory and repair path

Persisted surfaces that may contain legacy no-changes aliases:

| Surface | Legacy value | Repair path |
| --- | --- | --- |
| Temporal Visibility Search Attribute `mm_state` | `no_changes` | Read-time compatibility repairs to `no_commit`; open workflows should write canonical `mm_state=no_commit` on their next lifecycle update. Closed histories are read through the compatibility boundary. |
| `temporal_execution_sources.state` and projection `temporal_executions.state` | `no_changes` if written by an older worker or imported projection | Repository/API sync must coerce legacy values through the workflow-state alias map before storing or serializing. Database repair should update rows to `no_commit` before removing legacy enum values. |
| `automation_runs.status` | `no_changes` | Migration `332_mm1024_no_commit_status` updates existing rows to `no_commit`; repository coercion keeps reads and writes canonical. |
| `finish_summary_json.finishOutcome.code` | `NO_CHANGES` | Finish-summary compatibility rewrites to `NO_COMMIT` before indexing, API serialization, or terminal-state persistence. |
| `finish_summary_json.publish.reasonCode` and related JSON payloads | `no_changes` | Finish-summary compatibility rewrites to `no_commit` when the publish reason is repository-publication absence. |
| Memo `finishSummary` / `finish_summary` payloads | `NO_CHANGES` or `no_changes` nested values | Projection sync repairs memo-derived summaries before storing projection fields or returning API payloads. |

Replay and in-flight safety:

- Existing Temporal histories may replay terminal-state payloads or memo/search-attribute values containing legacy aliases. Those values are accepted only through the named compatibility helpers above.
- New workflow code must emit `no_commit`, `NO_COMMIT`, and publish `reasonCode=no_commit` directly.
- Removing the legacy database enum member requires a coordinated persisted-data repair that first proves no rows or durable JSON payloads still require direct enum decoding.
