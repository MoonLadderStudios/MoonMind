# Contract: Edit and Rerun Attachment Reconstruction

## Edit/Rerun Reconstruction Input

The reconstruction source is the authoritative task input snapshot for an existing MoonMind.Run.

Required attachment-bearing shape:

```json
{
  "task": {
    "instructions": "Task objective",
    "inputAttachments": [
      {
        "artifactId": "art_objective_1",
        "filename": "overview.png",
        "contentType": "image/png",
        "sizeBytes": 12345
      }
    ],
    "steps": [
      {
        "id": "step-1",
        "instructions": "Inspect screenshot",
        "inputAttachments": [
          {
            "artifactId": "art_step_1",
            "filename": "detail.png",
            "contentType": "image/png",
            "sizeBytes": 23456
          }
        ]
      }
    ]
  }
}
```

Rules:
- `task.inputAttachments` is the objective target.
- `task.steps[n].inputAttachments` is the step target.
- Absence of attachments is valid.
- Attachment meaning is defined by target, not filename or artifact metadata.
- Compact attachment references without structured target binding are insufficient for reconstruction.

## Reconstructed Draft Behavior

The Create page edit/rerun draft must:
- preserve objective text and step instructions from the snapshot;
- preserve objective-scoped and step-scoped attachment refs on their original targets;
- distinguish persisted refs from new local files;
- preserve runtime, publish, template or preset state, dirty state, and editable dependencies when recoverable;
- warn or mark flat reconstruction when preset binding metadata is not recoverable;
- fail explicitly if attachment target binding cannot be recovered.

## Submission Behavior

Edit and rerun submissions must:
- upload new local images before submitting the execution payload;
- submit structured refs, not binary content;
- preserve unchanged objective refs under `task.inputAttachments`;
- preserve unchanged step refs under `task.steps[n].inputAttachments`;
- reflect explicit remove/add/replace actions only on the authored target.

## Failure Behavior

The system must fail explicitly rather than:
- silently dropping persisted attachments;
- duplicating untouched attachment refs;
- inferring target bindings from filenames, artifact links, or metadata;
- claiming preset-bound state remains recoverable when only flat reconstruction is available.
