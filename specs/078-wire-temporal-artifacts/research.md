# Phase 0: Research

## Unknowns Addressed

1. **How are artifacts currently stored?**
   - *Research Findings*: MoonMind already has an `artifacts.py` module in `moonmind/workflows/temporal` providing a service to write/read artifacts to/from storage. Artifacts return reference IDs (e.g. `artifact_ref`).
   - *Decision*: We will use the existing artifact service to write blob data inside Temporal activity executions and return only the generated string ID.

2. **What is the current state of `MoonMind.Run` workflow regarding artifacts?**
   - *Research Findings*: `MoonMind.Run` has partial support. It expects `plan_ref`, `logs_ref`, and `summary_ref` to be returned by activities (`plan.generate`, `sandbox.run_command`, etc.).
   - *Decision*: We must ensure that the implementations of these activities (`plan.generate`, `sandbox.run_command`, etc. in `activity_runtime.py`) actually write the large string payloads to the artifact store and return the `ref` string, rather than returning the raw string.

3. **What is the current state of `MoonMind.ManifestIngest` workflow?**
   - *Research Findings*: The workflow class `MoonMind.ManifestIngest` is missing. Current code operates directly on the database via `service.py` (`manifest_ingest.py`).
   - *Decision*: Create the formal `MoonMind.ManifestIngest` workflow class. It will delegate parsing and processing to activities. The activity handling the manifest processing will return artifact references for the summary and node list instead of the full JSON/YAML payloads.

## Best Practices Evaluated

- **Temporal Large Payloads**:
  - *Decision*: The Temporal SDK limits event payloads to 2MB (and warns above 1MB). Passing large plans or logs as activity return values directly violates this. We will upload logs and plans within the activity and only pass the `uuid` / `url` back to the workflow.
- **Activity Idempotency**:
  - *Decision*: Storing artifacts should generate deterministic paths based on workflow ID, run ID, and activity ID so retries overwrite or safely skip. The existing `execution_ref` helps construct deterministic keys.

## Integration Patterns

- **Artifact Read/Write Pattern**: Activities will utilize the `ArtifactService` via standard DI or singleton in the worker context. The worker must be configured with access to the artifact store client.
