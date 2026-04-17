# Contract: Task Image Input Preview and Download

## Artifact Metadata Input

Task detail consumes the existing execution artifact list response:

```json
{
  "artifacts": [
    {
      "artifact_id": "art-objective",
      "content_type": "image/png",
      "size_bytes": 1234,
      "status": "complete",
      "metadata": {
        "source": "task-dashboard-objective-attachment",
        "target": "objective",
        "filename": "objective.png"
      }
    },
    {
      "artifact_id": "art-step",
      "content_type": "image/webp",
      "size_bytes": 4567,
      "status": "complete",
      "metadata": {
        "source": "task-dashboard-step-attachment",
        "stepLabel": "Step 2",
        "filename": "step.webp"
      }
    }
  ]
}
```

## UI Rules

- Image inputs are target-grouped only when metadata identifies the dashboard attachment source and target.
- Objective attachments render under `Objective`.
- Step attachments render under the provided step label.
- Preview and download URLs use `/api/artifacts/{artifactId}/download`.
- If image preview fails, metadata and the download action remain visible.
- Artifacts without target metadata remain in the generic Artifacts table.

## Non-Goals

- No new API endpoint.
- No browser-visible object-store URLs for task image input preview/download controls.
- No filename-derived target binding.
