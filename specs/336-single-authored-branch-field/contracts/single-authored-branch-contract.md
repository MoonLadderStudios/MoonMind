# Contract: Single Authored Branch Field

## Authored Submission Contract

New task submissions use this branch shape:

```json
{
  "type": "task",
  "payload": {
    "publishMode": "branch",
    "task": {
      "git": {
        "branch": "feature/example"
      },
      "publish": {
        "mode": "branch"
      }
    }
  }
}
```

Required behavior:
- `task.git.branch` is the only active authored branch field.
- `task.git.targetBranch`, `payload.targetBranch`, `task.targetBranch`, `task.git.startingBranch`, `payload.startingBranch`, and `task.startingBranch` are not valid active authored submission fields.
- `publishMode` and `task.publish.mode` preserve selected publish behavior.
- If publish mode requires a branch and `task.git.branch` is missing or blank, the submission is invalid.

## Legacy Reconstruction Contract

Legacy snapshots may contain historical fields:

```json
{
  "draft": {
    "startingBranch": "main",
    "targetBranch": "feature/legacy",
    "publishMode": "branch"
  }
}
```

Required behavior:
- Safe `startingBranch` values may normalize to the reconstructed authored `branch`.
- `targetBranch` is historical metadata only.
- Target-only legacy snapshots must not prefill active authored branch from `targetBranch`.
- Two-branch branch-publish snapshots that cannot collapse to one authored branch must surface a reconstruction warning.
- Any edited or rerun submission emitted from the reconstructed draft must omit `startingBranch` and `targetBranch`.

## Runtime Preparation Contract

Runtime preparation receives the canonical task view after authored-submission normalization.

Required behavior:
- Runtime preparation reads active branch intent from `task.git.branch`.
- Runtime preparation does not accept `task.git.targetBranch` as active input.
- Runtime-owned working/head branch metadata may be generated and emitted as diagnostics or publish metadata.
- Runtime-owned branch metadata must be distinguishable from authored task input.

## Error And Warning Contract

Invalid new authored branch payloads fail with a field-specific error that names the legacy field when one is present.

Legacy reconstruction warnings include:
- A human-readable message.
- A stable reason code suitable for tests.
- Enough historical branch metadata for audit/debug display without using it as active input.
