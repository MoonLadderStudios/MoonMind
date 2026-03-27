## Description

Currently, many Temporal workflow activities in MoonMind (such as `artifact.write_complete`) are invoked with loosely typed dictionaries as payloads. That bypasses static checking and relies on runtime coercion. 

A concrete failure mode: when a `bytes` value is embedded in a **dict** (or other structure) that crosses the default **JSON** payload path, the Temporal Python SDK serializes `bytes` as a JSON array of integers. After deserialization the activity may receive `list[int]` instead of `bytes`. Downstream code that expects a buffer (for example `hashlib.sha256(payload)`) can then raise `TypeError`. Defensive normalization at the activity entry (e.g. accepting `bytes | str | list[int]` and coercing lists) mitigates history-shaped input but does not remove the class of bugs: **any nested binary in JSON-shaped payloads is fragile**.

Goal: make activity boundaries **explicit, typed, and reviewable** so serialization shape and business types stay aligned, and so static analysis can flag mistakes before runtime.

## Design constraints (Temporal + MoonMind)

- **Activity type names are stable contracts** — do not rename activity types to “fix” typing; evolve input shapes in a compatibility-safe way. See `docs/Temporal/ActivityCatalogAndWorkerTopology.md` and `moonmind/workflows/temporal/activity_catalog.py`.
- **Payload codecs:** Default workflow/activity payloads in the Python SDK are JSON-oriented. Treat **`bytes` inside nested dicts / arbitrary JSON** as unsafe unless the team standardizes on an explicit encoding (e.g. **base64 string** fields in a Pydantic model, or a dedicated payload converter). Typed models alone do not fix binary encoding if the wire format is still JSON without a custom serializer for `bytes`.
- **Determinism:** Workflows must remain deterministic; typing applies to **what** is passed to `execute_activity`, not to hiding nondeterminism. Prefer plain data (str, int, enums, small structs) at boundaries.
- **Compatibility (Constitution / project policy):** Avoid “silent” transforms that change billing-relevant or externally visible semantics. For Temporal, treat activity/workflow payload shapes as **compatibility-sensitive**: preserve worker-bound invocation compatibility for in-flight runs, or version with an explicit cutover. Document dual-parse or normalization layers as **explicit compatibility shims**, not permanent business logic.

## Plan

### Phase 1: Standards and binary policy

- [x] 1. **Centralize schemas / Establish Model Standards** for activity inputs (and outputs) under an existing or new module, e.g. `moonmind/schemas/temporal_activity_models.py`, extending existing layout rather than introducing a parallel island.
- [x] 2. **Choose one primary modeling approach**: Default stack: **Pydantic v2** for activity inputs/outputs (validation + explicit serializers where needed). Dataclasses may be used for pure in-process helpers, but activity I/O should converge on one pattern.
- [x] 3. **Binary / blob policy (mandatory):** Document rules for activity payloads, prefer **base64-encoded `str`**, **artifact refs**, or other explicit handles for binary textual payloads at the JSON boundary with documented max sizes; if `bytes` must appear in the model, specify **serialization** (Pydantic custom type / field serializer) so the wire shape is never an “accidental list of ints”. Do not rely on nested raw `bytes` in JSON-encoded dicts.
- [x] 4. **Activity entry pattern:** Prefer activities that take a **single structured argument** (the model) plus optional metadata only if the SDK/worker pattern requires it. Define Pydantic models for the first high-risk artifact activities (e.g., `ArtifactWriteCompleteInput`).
- [x] 5. **Typed execution helper (optional but recommended):** Introduce a small façade or overloads (e.g. generic `execute_activity` wrapper keyed by activity name + input model type) so `mypy` / `pyright` can check call sites.

### Phase 2: Refactor activity definitions and worker wiring

- [ ] 1. Update `moonmind/workflows/temporal/artifacts.py`, `activity_runtime.py`, and related registration so activities accept the typed models at the **public activity boundary**, then map to internal services. Keep **activity type strings** unchanged.
- [ ] 2. **Compatibility:** For in-flight workflows still sending legacy `dict` or legacy shapes, choose and document one cutover approach:
  - Prefer **narrow adapters** / **dual-read** at the activity entry (accept legacy `dict` + new model): `dict | Model` → validate into `Model`, with explicit branches for deprecated keys/shapes; or
  - **Versioned activity names / explicit migration window** when the *workflow* must emit a new shape and old histories must replay safely.
  - Greenfield-only if environments have no in-flight runs.
- [ ] 3. Retain or narrow **defensive coercion** only as long as documented replay/history compatibility requires it. Add **replay / workflow-boundary** coverage when payload shapes change (not only isolated unit tests).

### Phase 3: Refactor workflow call sites

- [ ] 1. Update callers (`moonmind/workflows/temporal/workflows/run.py` and other orchestrators) to construct **typed models** (or typed constructors) when calling `workflow.execute_activity(...)`, so pyright/mypy can check field names and types.
- [ ] 2. Add **thin helpers** / the Phase 1 façade if needed: to centralize kwargs and catalog routing to reduce drift between call sites and so static checkers see concrete input types.

### Phase 4: Expansion, verification, and guardrails

- [ ] 1. **Prioritize by blast radius:** Roll out to other high-risk activities that carry blobs, large strings, or deeply nested dicts (`artifact.read`, `plan.generate`, etc.) before peripheral ones.
- [ ] 2. **Tests (required for this effort):**
  - **Workflow boundary / Round-trip tests:** real `execute_activity` invocation shape (Model → serialized payload → model / activity input), not only isolated unit tests.
  - **Compatibility:** at least one case for the **previous** payload/history shape still seen in replay or in-flight runs.
  - **Degraded input:** unknown/blank/new enum values where applicable.
  - **Replay / in-flight safety:** where payload changes could affect determinism or deserialization.
- [ ] 3. **Static analysis:** Enable or tighten pyright/mypy on workflow and activity packages for these modules; optional CI gate on touched paths.
- [ ] 4. **Documentation / Contract table:** Maintain a lightweight list (activity name → input/output model) for critical paths. When an activity’s input contract changes, update the Temporal activity catalog docs.

## Non-goals (for this document)

- Changing Task Queue routing, retry policies, or activity **names** without a separate migration plan.
- Introducing new compatibility transforms that alter externally visible semantics (billing, published artifact identity, model identifiers) beyond safe deserialization and validation.

## References

- `docs/Temporal/ActivityCatalogAndWorkerTopology.md` — activity taxonomy and stability.
- `docs/Tasks/SkillAndPlanContracts.md` — tools, plans, determinism boundaries.
- Project testing notes: workflow boundary and replay coverage in `AGENTS.md`.
