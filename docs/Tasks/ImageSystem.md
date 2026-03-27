# Task Image Input System

**Implementation tracking:** [`docs/tmp/remaining-work/Tasks-ImageSystem.md`](../tmp/remaining-work/Tasks-ImageSystem.md)

## Summary

This design defines how image attachments are processed for MoonMind tasks using
the Temporal execution model and the artifact system.

Goals:

1. allow users to upload images from Mission Control
2. store uploaded bytes in the artifact store
3. pass lightweight `ArtifactRef` values into `MoonMind.Run`
4. run deterministic vision-processing activities when needed
5. let sandbox activities materialize raw image bytes only when required

---

## Architecture Overview

In an artifact-centric, Temporal-orchestrated model, large binaries are never
embedded directly in execution payloads or workflow history.

### High-Level Flow

```text
Dashboard UI
  │
  ├─ 1) POST /artifacts
  │     └─ returns ArtifactRef(s)
  │
  ├─ 2) Upload bytes to the artifact store
  │
  └─ 3) POST /api/executions
        │
        ▼
MoonMind.Run Workflow
  │
  ├─ initialize with input ArtifactRefs
  ├─ run vision-processing activity when needed
  ├─ pass generated text/image context into planning or execution
  └─ optionally materialize raw bytes into the sandbox workspace
```

---

## Data Model

Images are represented through the unified artifact index.

- **Content-Type**: `image/png`, `image/jpeg`, or `image/webp`
- **Linkage**: linked to the owning execution via artifact link records
- **Retention**: follows normal artifact retention policy
- **Integrity**: enforced via checksums at upload completion

---

## Authorization and Security

- end users access images through authorized artifact endpoints
- workers fetch blobs with service credentials, not transient claim tokens
- `image/svg+xml` remains forbidden
- size and chunk limits are enforced by the artifact API

---

## Behavior by workload

### Text-centric planning and coding

- raw image bytes do not need to enter the sandbox by default
- the workflow can invoke a vision activity to produce text context
- the resulting text artifact is injected into later prompts or planning

### Multimodal runtimes

- the workflow passes `ArtifactRef` values into the multimodal activity path
- provider-specific payload construction happens inside activities, not in
  workflow state

### Sandbox materialization

If a task explicitly requires raw image manipulation:

- schedule an artifact-download activity
- materialize files into a workspace input directory
- keep those files out of version control

---

## API Contract Summary

Standard flow:

1. `POST /artifacts`
2. upload bytes using a presigned URL
3. `POST /artifacts/{artifact_id}/complete`
4. `POST /api/executions`

UI rendering consumes:

- `GET /api/executions/{namespace}/{workflow_id}/{run_id}/artifacts?link_type=input.image`
- `POST /api/artifacts/{artifact_id}/presign-download` or the equivalent
  artifact download flow used by the control plane

---

## Vision Pipeline

Images ingest through the artifact APIs and enter workflows as `ArtifactRef`
values.

Vision-processing activities such as `vision.generate_context` can produce text
artifacts that feed later planning or execution stages. Older attachment-specific
create routes are retired in favor of the standard artifact + execution flow.
