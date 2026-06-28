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
| Exact lifecycle state | `no_commit` | Terminal, success-adjacent state: workflow completed without creating a repository commit. |
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

Compatibility rule:

```text
NO_CHANGES -> NO_COMMIT
no_changes -> no_commit
```

UI and API adapters should render legacy `NO_CHANGES` as `No commit` when the reason is repository-publication absence. New workflow histories should emit `NO_COMMIT` directly.
