# Contract: Create Page Authoring Validation

## UI Placement Contract

- The Create page has exactly one primary Steps authoring card identified by `data-canonical-create-section="Steps"`.
- Repository, Branch, and Publish Mode controls are rendered inside that Steps card.
- The controls keep accessible labels equivalent to:
  - `GitHub Repo` or a repository label accepted by existing tests and user copy
  - `Branch`
  - `Publish Mode`
- Publish Mode is not moved into an unrelated execution-context panel, and it is not removed from task submission data.
- The submit action may remain visually adjacent to the controls, but it must not be the only container establishing their semantic ownership.

## Validation Contract

Before submission, the Create page blocks invalid drafts for:
- missing repository when no default repository exists
- unsupported repository shape
- unsupported runtime
- unsupported publish mode
- branch publish mode without a branch
- invalid or incomplete dependency selections
- disabled attachment policy or invalid attachment target bindings

Validation feedback must identify the authoring input that prevents submission.

## Submission Payload Contract

Valid Create page submissions produce a task-shaped request with these invariants:

```json
{
  "type": "task",
  "payload": {
    "repository": "owner/repo",
    "task": {
      "git": { "branch": "selected-branch" },
      "publish": { "mode": "none|branch|pr" },
      "runtime": { "mode": "codex|..." },
      "steps": [],
      "inputAttachments": [],
      "dependsOn": [],
      "authoredPresets": [],
      "appliedStepTemplates": []
    }
  }
}
```

Rules:
- `payload.task.git.branch` is the authored branch field when a branch is present.
- New submissions do not include `targetBranch` in `payload.task`, `payload.task.git`, or equivalent new task-shaped output.
- `payload.task.publish.mode` retains the selected publish semantics after existing self-managed-publish adjustments.
- Attachment refs remain bound to objective or step targets.
- Preset and Jira provenance remain traceable through supported metadata fields.

## Test Contract

Frontend unit tests must prove:
- Repository, Branch, and Publish Mode controls are inside the Steps card.
- Invalid drafts are blocked before `/api/executions` is called.
- A valid combined draft preserves branch, publish mode, attachments, dependencies, and provenance in the submitted payload.

Backend/API or integration tests must prove:
- Execution-visible task parameters preserve `task.git.branch` and reject or remove legacy `targetBranch` according to current backend contract.
- Task input snapshots preserve the normalized task-shaped payload for replay, edit, rerun, and verification evidence.
