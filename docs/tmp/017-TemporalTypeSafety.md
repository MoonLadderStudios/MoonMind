## Description

Currently, many Temporal workflow activities in MoonMind (such as `artifact.write_complete`) are invoked with loosely typed dictionaries as payloads. That bypasses static checking and relies on runtime coercion.

A concrete failure mode: when a `bytes` value is embedded in a **dict** (or other structure) that crosses the default **JSON** payload path, the Temporal Python SDK serializes `bytes` as a JSON array of integers. After deserialization the activity may receive `list[int]` instead of `bytes`. Downstream code that expects a buffer (for example `hashlib.sha256(payload)`) can then raise `TypeError`. Defensive normalization at the activity entry (e.g. accepting `bytes | str | list[int]` and coercing lists) mitigates history-shaped input but does not remove the class of bugs: **any nested binary in JSON-shaped payloads is fragile**.

Goal: make activity boundaries **explicit, typed, and reviewable** so serialization shape and business types stay aligned, and so static analysis can flag mistakes before runtime.

## Design constraints (Temporal + MoonMind)

- **Activity type names are stable contracts** — do not rename activity types to “fix” typing; evolve input shapes in a compatibility-safe way (see below). See `docs/Temporal/ActivityCatalogAndWorkerTopology.md` and `moonmind/workflows/temporal/activity_catalog.py`.
- **Payload codecs:** Default workflow/activity payloads in the Python SDK are JSON-oriented. Treat **`bytes` inside nested dicts / arbitrary JSON** as unsafe unless the team standardizes on an explicit encoding (e.g. **base64 string** fields in a Pydantic model, or a dedicated payload converter). Typed models alone do not fix binary encoding if the wire format is still JSON without a custom serializer for `bytes`.
- **Determinism:** Workflows must remain deterministic; typing applies to **what** is passed to `execute_activity`, not to hiding nondeterminism. Prefer plain data (str, int, enums, small structs) at boundaries.
- **Compatibility (Constitution / project policy):** Avoid “silent” transforms that change billing-relevant or externally visible semantics. For Temporal, treat activity/workflow payload shapes as **compatibility-sensitive**: preserve worker-bound invocation compatibility for in-flight runs, or version with an explicit cutover. Document dual-parse or normalization layers as **explicit compatibility shims**, not permanent business logic.

## Plan

### Phase 1: Standards and binary policy

1. **Centralize schemas** for activity inputs (and, where helpful, outputs) under an existing or new module, e.g. `moonmind/schemas/temporal_activity_models.py`, aligned with other `moonmind.schemas.*` packages and with shared envelopes where they already exist (`ActivityInvocationEnvelope`, catalog types in `activity_catalog.py`).
2. **Choose one primary modeling approach** for activity args (recommend **Pydantic v2** if the repo already standardizes on it for validation + clear errors; otherwise **dataclasses** + explicit validation functions). Avoid mixing ad hoc dicts and multiple partial conventions per activity family.
3. **Binary fields:** Prefer **`str` (base64)** or **UTF-8 `str`** for textual payloads at the boundary, with documented max sizes; if `bytes` must appear in the model, specify **serialization** (Pydantic custom type / field serializer) so the wire shape is never “accidental list of ints”. Do not rely on nested raw `bytes` in JSON-encoded dicts.
4. **Activity entry pattern:** Prefer activities that take a **single structured argument** (the model) plus optional metadata only if the SDK/worker pattern requires it — keeps stubs and `execute_activity` call sites symmetrical and easy to type-check.

### Phase 2: Refactor activity definitions and worker wiring

1. Update `moonmind/workflows/temporal/artifacts.py`, `activity_runtime.py`, and related registration so activities accept the typed models at the **public activity boundary**, then map to internal services. Keep **activity type strings** unchanged.
2. **Compatibility:** For in-flight workflows still sending legacy `dict` or legacy shapes:
   - Prefer **narrow adapters** at the activity entry: `dict | Model` → validate into `Model`, with explicit branches for deprecated keys/shapes; or
   - **Workflow versioning** (`patched`, or separate workflow types) when the *workflow* must emit a new shape and old histories must replay safely — don’t rely only on activity-side guessing if workflow replay could diverge.
3. Retain or narrow **defensive coercion** (e.g. `list[int]` → `bytes`) only as long as documented replay/history compatibility requires it; prefer removing foot-guns via typed callers + binary policy above.

### Phase 3: Refactor workflow call sites

1. Update callers (`moonmind/workflows/temporal/workflows/run.py` and other orchestrators) to construct **typed models** (or typed constructors) when calling `workflow.execute_activity(...)`, so pyright/mypy can check field names and types.
2. Add **thin helpers** if needed: e.g. `execute_artifact_write_complete(workflow, route, input: ArtifactWriteCompleteInput)` to centralize kwargs and catalog routing — reduces drift between call sites.

### Phase 4: Expansion, verification, and guardrails

1. Roll out to other high-risk activities (`artifact.read`, plan/skill execution envelopes, integration activities) prioritized by payload complexity and past incidents.
2. **Tests (required for this effort):**
   - **Workflow boundary tests:** real `execute_activity` invocation shape (or worker binding path), not only isolated unit tests of models.
   - **Compatibility:** at least one case for the **previous** payload/history shape still seen in replay or in-flight runs (e.g. legacy dict or `list[int]` payload) if those remain supported.
   - **Degraded input:** unknown/blank/new enum values where applicable — fail-fast behavior should match product policy.
   - **Replay / in-flight safety:** where payload changes could affect determinism or deserialization, add replay-style or explicit compatibility tests per project guidance.
3. **Static analysis:** Enable or tighten pyright/mypy on workflow and activity packages for these modules; optional CI gate on touched paths.
4. **Documentation:** When an activity’s input contract changes in a user-visible way, update the Temporal activity catalog / contract docs or the relevant `docs/tmp/remaining-work/` tracker so canonical docs stay accurate.

## Non-goals (for this document)

- Changing Task Queue routing, retry policies, or activity **names** without a separate migration plan.
- Introducing new compatibility transforms that alter externally visible semantics (billing, published artifact identity, model identifiers) beyond safe deserialization and validation.

## References

- `docs/Temporal/ActivityCatalogAndWorkerTopology.md` — activity taxonomy and stability.
- `docs/Tasks/SkillAndPlanContracts.md` — tools, plans, determinism boundaries.
- Project testing notes: workflow boundary and replay coverage in `AGENTS.md`.
