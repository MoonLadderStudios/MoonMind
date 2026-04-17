# Research: Materialize Attachment Manifest and Workspace Files

## Input Classification

Decision: Treat the MM-370 Jira preset brief as a single-story runtime feature request.
Rationale: The brief has one actor, one prepare-time behavior, one source document slice, and one independently testable result: declared attachments are downloaded, locally materialized, and represented in a canonical manifest before runtime or step execution.
Alternatives considered: Treating `docs/Tasks/ImageSystem.md` as a broad declarative design was rejected because the brief already selects sections 3.2, 4, and 8 and narrows the work to prepare-time materialization only.

## Materialization Boundary

Decision: Implement materialization in the existing Codex worker `_run_prepare_stage` boundary.
Rationale: That boundary owns per-job workspace creation, `.moonmind` symlink setup, `task_context.json`, prepare logs, and pre-runtime execution state. Materializing attachments there ensures files and manifest exist before execute-stage runtime invocation.
Alternatives considered: Materializing in the API service was rejected because workspace paths are per-worker local state. Materializing in runtime adapters was rejected because target-aware attachment setup should happen before any specific adapter consumes the workspace.

## Attachment Source

Decision: Use the canonical task payload `task.inputAttachments` and `task.steps[n].inputAttachments` as the source of target binding.
Rationale: `docs/Tasks/ImageSystem.md` states target meaning comes from the field containing the ref, and prior stories preserved those refs in the authoritative task snapshot. This avoids filename, artifact metadata, or UI heuristics.
Alternatives considered: Reading artifact links or artifact metadata was rejected because those are observability and access surfaces, not authoritative target-binding state.

## Workspace Paths

Decision: Write objective files under `.moonmind/inputs/objective/<artifactId>-<sanitized-filename>` and step files under `.moonmind/inputs/steps/<stepRef>/<artifactId>-<sanitized-filename>`.
Rationale: This exactly matches the source design contract and keeps target directories isolated. Prefixing by artifact id preserves determinism even when filenames collide.
Alternatives considered: A flat `.moonmind/inputs/` directory was rejected because it loses explicit target grouping. Ordering-derived filenames were rejected because unrelated target ordering must not affect paths.

## Stable Step References

Decision: Use explicit step `id` when provided; otherwise derive a deterministic ordinal-based reference such as `step-1`.
Rationale: The source design requires stable step references when ids are absent. Ordinal-based fallback is deterministic for the canonical payload and matches the step ordinal recorded in the manifest.
Alternatives considered: Hashing step content was rejected because edits to instructions would change paths for the same ordered step. Random ids were rejected because repeated prepare runs would not be stable.

## Failure Handling

Decision: Treat any download, write, sanitization, or manifest failure as a prepare-stage failure.
Rationale: Partial materialization would present incomplete runtime context and violates the source requirement. Failing in prepare prevents runtime execution from consuming missing inputs.
Alternatives considered: Best-effort manifest entries with errors were rejected because the acceptance criteria explicitly require failure, not best-effort success.

## Test Strategy

Decision: Add focused pytest coverage for helper behavior plus a worker prepare boundary test that proves manifest and files are created before execution.
Rationale: Unit tests can cover path determinism, filename sanitization, stable step refs, and failure modes without requiring Docker. Boundary coverage protects the invocation shape used by the worker.
Alternatives considered: Only testing low-level helpers was rejected because the story changes prepare-stage behavior. Only integration tests were rejected because they would be slower and less focused for red-first iteration.
