## Description
Currently, many Temporal workflow activities in MoonMind (such as `artifact.write_complete`) are called by passing loosely-typed dictionaries as payloads. This circumvents Python static type checkers and relies heavily on runtime type inference.

Recently, this caused a critical workflow failure where a `bytes` object within a dictionary payload was serialized by the Temporal Python SDK as a massive JSON list of integers (e.g., `[123, 34, 115...]`). When deserialized by the activity, the resulting `list[int]` was passed to `hashlib.sha256()`, causing a `TypeError: object supporting the buffer API required` and crashing the Temporal worker.

**Root cause (not only “missing types”):** default JSON-style payload encoding does not round-trip arbitrary nested `bytes` inside ad hoc dicts. Typed models help, but we also need an explicit **wire-format policy** for binary and large blobs so serialization stays predictable.

To prevent such serialization anomalies and type mismatch bugs in the future, enforce structured models and validation at Temporal activity boundaries. **Default stack:** Pydantic v2 models for activity inputs/outputs (validation + explicit serializers where needed). Dataclasses may be used for pure in-process helpers, but activity I/O should converge on one pattern.

## Plan

### Phase 1: Establish Model Standards
1. **Inventory and consolidate:** Scan `moonmind/schemas/` and related Temporal modules; extend existing layout rather than introducing a parallel island. A dedicated module (e.g. `moonmind/schemas/temporal_activity_models.py`) is fine if it re-exports or groups by domain.
2. **Binary / blob policy (mandatory):** Document rules for activity payloads, for example:
   - Prefer **base64-encoded `str`**, **artifact refs**, or other explicit handles for binary at the JSON boundary; or
   - Use Pydantic field serializers/custom JSON encoders that guarantee safe round-trip **if** raw bytes must appear on the model.
   - **Avoid** unstructured `dict` payloads carrying nested `bytes` without an explicit encoding story.
3. **Define Pydantic models** for the first high-risk artifact activities (start with blob-heavy paths).
   - Example shape (illustrative): `ArtifactWriteCompleteInput(principal: str, artifact_id: str, payload_b64: str, content_type: str | None = None)` — adjust names to match real APIs; do **not** use `str | bytes` on the wire without a documented serializer.
4. **Typed execution helper (optional but recommended):** Introduce a small façade or overloads (e.g. generic `execute_activity` wrapper keyed by activity name + input model type) so `mypy` / `pyright` can check call sites; raw `workflow.execute_activity(..., dict)` alone rarely gets full inference.

### Phase 2: Refactor Activity Definitions
1. Update `moonmind/workflows/temporal/artifacts.py` and `moonmind/workflows/temporal/activity_runtime.py` to accept these models at activity entry points and convert to internal APIs as needed.
2. **Compatibility (align with Temporal payload sensitivity):** Choose and document one cutover approach, test it:
   - Short-term **dual-read** in activities (accept legacy `dict` + new model) for one release, or
   - **Versioned activity names** / explicit migration window, or
   - Greenfield-only if environments have no in-flight runs.
3. Add **replay / workflow-boundary** coverage when payload shapes change (not only isolated unit tests).

### Phase 3: Refactor Workflow Call Sites
1. Update callers (`moonmind/workflows/temporal/workflows/run.py` and other orchestrators) to construct typed models when invoking activities.
2. Prefer the Phase 1 façade so static checkers see concrete input types; where that is not yet in place, document gaps.

### Phase 4: Expansion & Verification
1. **Prioritize by blast radius:** Expand to activities that carry blobs, large strings, or deeply nested dicts (`artifact.read`, `plan.generate`, etc.) before peripheral ones.
2. **Round-trip tests:** Model → serialized payload (as the worker/SDK would) → model / activity input, for critical activities.
3. **Contract table (lightweight):** Maintain a small operator- or dev-facing list: activity name → input/output model (or link) for critical paths.
4. Run standard unit and integration tests; keep `mypy` / `pyright` strict on new modules and typed call sites where the façade exists.
