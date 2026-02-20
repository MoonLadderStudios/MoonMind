# Contract: Canonical Task Publish PR Text Resolution

## Scope

Applies to canonical `type="task"` publish stage behavior when `task.publish.mode` is `branch` or `pr`.

## Inputs

- `task.publish.commitMessage`
- `task.publish.prTitle`
- `task.publish.prBody`
- `task.instructions`
- `task.steps[].title`
- `task.runtime.mode`
- `task.publish.prBaseBranch`
- Resolved `startingBranch` and `workingBranch`
- `jobId`

## Resolution Rules

1. **Commit message**
- If `publish.commitMessage` is non-empty, use it verbatim.
- Else use deterministic fallback commit message.

2. **PR title (`publish.mode=pr`)**
- If `publish.prTitle` is non-empty, use it verbatim.
- Else derive in order:
  1. First non-empty step title.
  2. First sentence/line from `task.instructions`.
  3. Deterministic fallback title.
- Derived title must avoid full UUID values.

3. **PR body (`publish.mode=pr`)**
- If `publish.prBody` is non-empty, use it verbatim.
- Else generate summary + metadata footer:

```md
---
<!-- moonmind:begin -->
MoonMind Job: <job-uuid>
Runtime: <codex|gemini|claude>
Base: <base-branch>
Head: <head-branch>
<!-- moonmind:end -->
```

4. **Branch correlation**
- Base branch = `publish.prBaseBranch` when provided, else resolved `startingBranch`.
- Head branch = resolved `workingBranch`.

## Validation Expectations

- Unit tests assert override precedence and fallback ordering.
- Unit tests assert generated metadata markers/keys and full UUID presence in body.
- Unit tests assert full UUID not embedded in derived title.
