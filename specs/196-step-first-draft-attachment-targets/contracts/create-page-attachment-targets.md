# Contract: Create Page Attachment Targets

Source story: `MM-377: Step-First Draft and Attachment Targets`

## Browser Draft Contract

When attachment policy is enabled, the Create page exposes:

- Objective-scoped attachment input associated with `Feature Request / Initial Instructions`.
- Step-scoped attachment input inside each step card.
- Per-attachment display including filename and size at minimum, with target ownership visible from the containing field.
- Remove actions for selected attachments before submit.

The draft MUST use stable target identity:

- Objective files are keyed to the objective target.
- Step files are keyed to `step.localId`.
- Reordering steps MUST NOT mutate the target key for any selected step file.

## Artifact Upload Contract

Before execution creation, the browser uploads each selected file through the configured MoonMind artifact API.

Artifact create metadata MUST include target context:

- Objective attachment metadata source: `task-dashboard-objective-attachment`
- Step attachment metadata source: `task-dashboard-step-attachment`
- Step attachment metadata includes a human step label for diagnostics.

Failed validation or upload completion MUST prevent execution creation.

## Execution Create Payload Contract

Submitted task payload MUST use structured refs only:

```json
{
  "payload": {
    "task": {
      "instructions": "Feature request text",
      "inputAttachments": [
        {
          "artifactId": "art-objective",
          "filename": "objective.png",
          "contentType": "image/png",
          "sizeBytes": 100
        }
      ],
      "steps": [
        {
          "instructions": "Step instructions",
          "inputAttachments": [
            {
              "artifactId": "art-step",
              "filename": "step.png",
              "contentType": "image/png",
              "sizeBytes": 100
            }
          ]
        }
      ]
    }
  }
}
```

Rules:
- Objective-scoped images appear only in `task.inputAttachments`.
- Step-scoped images appear only in the owning `task.steps[n].inputAttachments`.
- Generated attachment markdown MUST NOT be appended to task or step instruction text.
- The same file name may appear under different targets without changing ownership.
