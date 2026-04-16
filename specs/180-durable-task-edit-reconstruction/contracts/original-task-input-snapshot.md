# Contract: Original Task Input Snapshot

## API Response Additions

`GET /api/executions/{workflowId}` adds:

```json
{
  "taskInputSnapshot": {
    "available": true,
    "artifactRef": "art_...",
    "snapshotVersion": 1,
    "sourceKind": "create",
    "reconstructionMode": "authoritative",
    "disabledReasons": {},
    "fallbackEvidenceRefs": []
  }
}
```

Rules:

- `taskInputSnapshot` is compact and never embeds the snapshot body.
- `actions.canUpdateInputs` and `actions.canRerun` are false when reconstruction is unavailable for a mode that requires an editable draft.
- `actions.disabledReasons.canRerun` or `actions.disabledReasons.canUpdateInputs` contains a bounded operator-readable reason when disabled by reconstruction state.
- Existing `inputArtifactRef` remains a task/runtime input ref and must not be overloaded as the original snapshot ref unless it is explicitly linked as `input.original_snapshot`.

## Snapshot Artifact

Artifact create/write contract:

- `content_type`: `application/vnd.moonmind.task-input-snapshot+json;version=1`
- `artifact_type` or metadata `artifact_class`: `input.original_snapshot`
- `link_type`: `input.original_snapshot`
- `retention_class`: `long`
- `redaction_level`: `none` unless the draft contains restricted attachment metadata, in which case preview policy follows existing artifact rules

Minimal payload:

```json
{
  "snapshotVersion": 1,
  "source": {
    "kind": "create",
    "submittedAt": "2026-04-16T00:00:00Z"
  },
  "draft": {
    "taskShape": "skill_only",
    "runtime": "codex_cli",
    "providerProfile": "profile-id",
    "model": "gpt-5.4",
    "effort": "medium",
    "repository": "owner/repo",
    "startingBranch": "main",
    "targetBranch": "feature/example",
    "publish": {"mode": "pr"},
    "instructions": "",
    "primarySkill": {
      "name": "moonspec-orchestrate",
      "inputs": {"request": "Define durable task edit reconstruction model"}
    },
    "steps": [],
    "appliedTemplates": [],
    "dependencies": [],
    "attachments": [],
    "storyOutput": null,
    "proposeTasks": false,
    "proposalPolicy": null
  },
  "largeContentRefs": {},
  "attachmentRefs": [],
  "lineage": {},
  "excluded": {
    "schedule": "Schedule controls are creation-only and are not editable through task edit/rerun."
  }
}
```

## Field-By-Field Reconstruction Matrix

| Create-form value | Current observed persistence | Current classification | Canonical source | Rerun | Active edit |
|---|---|---|---|---|---|
| Primary instructions | `inputParameters.task.instructions`, `inputArtifactRef`, sometimes absent | Original when inline/artifact, otherwise missing | Snapshot `draft.instructions` or `largeContentRefs` | Editable | Editable while workflow accepts updates |
| Multi-step instructions | `inputParameters.task.steps[].instructions`, generated plan nodes | Original only when from submitted task; plan nodes are derived | Snapshot `draft.steps[]` | Editable | Editable before safe point rules |
| Selected primary skill | `inputParameters.task.tool`, `inputParameters.task.skill`, `taskSkills`, execution `targetSkill` | Mixed original/derived presentation | Snapshot `draft.primarySkill.name` plus resolved skillset ref metadata | Editable selection; original resolved ref read-only | Editable selection when supported; resolved ref read-only |
| Skill inputs | `inputParameters.task.inputs`, tool inputs, template-expanded values | Mixed original/normalized | Snapshot `draft.primarySkill.inputs` and step inputs | Editable | Editable |
| Runtime command selection | Runtime step inputs when present | Original if submitted; otherwise derived | Snapshot typed runtime selection | Editable | Editable if update contract supports it |
| Applied template identity | `inputParameters.task.appliedStepTemplates` | Original-ish but can be lossy | Snapshot `draft.appliedTemplates[].slug/version/scope` | Editable by reapplying or detaching | Editable by reapplying or detaching |
| Template inputs and feature request | Often frontend-only or normalized into instructions | Original, frequently missing today | Snapshot `draft.appliedTemplates[].inputs` and `featureRequest` | Editable | Editable |
| Customized template steps | `inputParameters.task.steps[]` if preserved | Original when preserved | Snapshot step binding metadata and customized flags | Editable | Editable |
| Runtime | execution `targetRuntime`, `inputParameters.targetRuntime`, task runtime | Original/normalized | Snapshot `draft.runtime`; execution field for display | Editable | Usually read-only or editable only before dispatch |
| Provider profile | execution `profileId`, task runtime profile | Original/normalized | Snapshot `draft.providerProfile` | Editable | Usually read-only once runtime started |
| Model | `requestedModel`, `model`, task runtime model | Requested is original; resolved is derived | Snapshot requested model; resolved model read-only display | Editable | Editable only before runtime dispatch |
| Effort | execution `effort`, task runtime effort | Original | Snapshot `draft.effort` | Editable | Editable only before runtime dispatch |
| Repository | payload repository, search attr `mm_repo` | Original plus presentation | Snapshot `draft.repository`; search attr display | Editable | Read-only after workspace materialization unless backend supports change |
| Starting branch | task git, execution field | Original | Snapshot `draft.startingBranch` | Editable | Read-only after clone/checkout begins unless backend supports safe point |
| Target branch | task git, execution field | Original | Snapshot `draft.targetBranch` | Editable | Editable before publish branch exists |
| Publish settings | task publish, execution `publishMode` | Original/normalized | Snapshot `draft.publish` | Editable | Editable before publish/finalization |
| Dependencies | `inputParameters.task.dependsOn`, dependency rows | Original plus normalized dependency graph | Snapshot selected IDs; dependency rows read-only audit | Editable on rerun | Read-only for active edit unless workflow supports dependency recalculation |
| Attachments | artifact links `input.attachment`, step payload refs | Original refs and artifact metadata | Snapshot `attachmentRefs` plus artifact links | Editable by add/remove refs, subject to read validation | Editable by add/remove refs before consumed |
| Story-output settings | `inputParameters.storyOutput` or task story output | Original/normalized | Snapshot `draft.storyOutput` | Editable | Editable before story-output phase |
| `proposeTasks` | `inputParameters.proposeTasks`, `task.proposeTasks` | Original/normalized | Snapshot `draft.proposeTasks` | Editable | Editable before proposals phase |
| `proposalPolicy` | `inputParameters.task.proposalPolicy` | Original/normalized | Snapshot `draft.proposalPolicy` | Editable | Editable before proposals phase |
| Priority/max attempts | create request top-level parameters | Original execution control | Snapshot top-level controls or execution parameters | Editable | Read-only unless update supports execution policy |
| One-time delay schedule | payload schedule, schedule route result, scheduledFor | Creation-only scheduling | Snapshot `excluded.schedule` and execution audit display | Omitted | Omitted |
| Recurring schedule definition | recurring task definition service | Schedule-management object | Recurring schedule editor, not task edit snapshot | Omitted | Omitted |
| Plan artifact instructions | `planArtifactRef` | Derived planner output | Fallback evidence only | Read-only degraded preview; blocked submit until replaced | Read-only degraded preview; blocked submit |
| Step ledger | workflow step ledger query/projection | Generated execution evidence | Fallback evidence only | Read-only | Read-only |
| Memo/search attributes | memo title/summary, repo/state attrs | Presentation metadata | Execution detail display, not draft authority | Read-only unless specific title edit | Read-only unless specific title edit |

## Frontend Read Rules

1. Read execution detail.
2. If `taskInputSnapshot.reconstructionMode === "authoritative"`, download `artifactRef` and build the draft from the snapshot.
3. If no authoritative snapshot exists and fallback evidence exists, show read-only degraded recovery copy:

   `MoonMind can show generated execution details for reference, but this run does not have the original submitted draft. Replace the task input before rerunning.`

4. Block submit for degraded drafts until the operator supplies replacement authoritative input.
5. Do not call plan-artifact-derived instructions `taskInstructions` without marking their source as derived and read-only.

## Persistence Lifecycle

Create:

1. Build snapshot payload from create-form request before normalization.
2. Create/write immutable artifact.
3. Link artifact to execution with `input.original_snapshot`.
4. Store compact snapshot ref in execution record/memo or dedicated record field if added by implementation.
5. Start workflow with compact refs only.

Edit:

1. Reconstruct from current authoritative snapshot.
2. Submit edited draft.
3. Create replacement snapshot artifact for the update request.
4. Send `UpdateInputs` with compact snapshot ref and parameters patch.
5. Link new snapshot to the same execution.

Rerun:

1. Reconstruct from source run snapshot.
2. Submit rerun draft.
3. Create new snapshot artifact for the rerun request.
4. Send `RequestRerun` or create the fresh rerun execution with the new snapshot ref.
5. Link source and resulting execution through lineage metadata.

Cutover:

- New supported submissions require snapshots.
- Pre-cutover executions without snapshots are not backfilled from derived data.
- Operators may use degraded read-only evidence to author a new replacement draft.
