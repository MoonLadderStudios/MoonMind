# Temporal Type Safety

**Implementation tracking:** Rollout and backlog notes live in MoonSpec artifacts (`specs/<feature>/`), gitignored handoffs (for example `artifacts/`), or other local-only files—not as migration checklists in canonical `docs/`.
**Status:** **Desired state / normative target** 
**Last updated:** **2026-04-14** 
**Scope:** Defines how MoonMind represents Temporal workflow, activity, signal, update, query, and Continue-As-New payloads so reliability comes from explicit contracts instead of ad hoc dictionaries.

---

## 1. Purpose

Temporal reliability depends on more than Python type hints. Every workflow/activity/message boundary is also a serialized wire contract and, for workflows, a replayed history contract. MoonMind therefore treats type safety in Temporal as a combination of:

1. static typing at author time
2. validation and serialization safety at runtime
3. compatibility safety across in-flight histories and deployments

This document is normative. Temporary shims may exist for compatibility, but new code must follow the desired-state rules here.

---

## 2. Goals and non-goals

### 2.1 Goals

1. Make every public Temporal boundary explicit, typed, and reviewable.
2. Prevent dict-shaped drift, accidental payload coercion, and provider-specific shapes leaking into workflows.
3. Keep workflow code deterministic and replay-safe as contracts evolve.
4. Standardize message handling for managed sessions and future runtime planes.
5. Make compatibility rules clear for in-flight workflows and worker rollouts.

### 2.2 Non-goals

This document does **not** define:

- Task Queue topology or retry-policy semantics.
- Artifact-storage architecture beyond how Temporal should reference it.
- A license to break activity names, workflow names, or in-flight history compatibility in the name of “cleaner typing.”

---

## 3. Core principles

### 3.1 Boundary types are real contracts

A Temporal activity name, workflow input, update name, signal name, query name, and Continue-As-New payload are public contracts. They must be modeled like API contracts, not convenience dicts.

### 3.2 One structured argument per boundary

The default shape for workflow `run`, updates, signals, queries, and activities is one structured request model and one structured return model. No new Temporal entrypoint may take multiple loosely-related scalar parameters or raw `dict[str, Any]` payloads unless the entrypoint is a documented compatibility shim.

### 3.3 Pydantic v2 is the default boundary modeling system

MoonMind standardizes on **Pydantic v2** models for Temporal boundary I/O. Use dataclasses only for pure in-process helpers when no Temporal serialization boundary is involved.

### 3.4 The data converter is part of the contract

Type safety is incomplete if models only exist inside workflow or activity bodies. Temporal clients and workers must share the same data-converter policy so the wire shape matches the annotated shape.

The desired default is:

- Pydantic-aware Temporal payload conversion for Temporal-facing code.
- Explicit serializers/validators for any field whose wire shape needs to be controlled.
- Immediate validation into the canonical model at any legacy JSON-shaped boundary that still exists for compatibility.

### 3.5 Compatibility outranks tidiness

A cleaner model is not enough reason to break replay or in-flight executions. Evolve contracts additively; when non-additive change is unavoidable, use explicit compatibility shims, Worker Versioning and/or patching, and replay testing.

### 3.6 Determinism remains the workflow rule

Type safety does not move nondeterminism into workflows. It only makes boundaries explicit. Network calls, clocks, subprocesses, filesystem I/O, provider inspection, and mutable external-state reads stay in Activities.

---

## 4. Canonical modeling rules

### 4.1 Request/response models

Every public Temporal boundary must have a named request model and, when it returns data, a named response model. “Raw dict in, raw dict out” is not an approved target-state pattern.

Approved homes:

- activity request/response models: `moonmind/schemas/temporal_activity_models.py` or a domain schema module already used by the activity family
- managed-session workflow/message models: `moonmind/schemas/managed_session_models.py`
- canonical runtime execution models: `moonmind/schemas/agent_runtime_models.py` and related domain modules

### 4.2 Model configuration

Temporal boundary models should, by default:

- set `extra="forbid"`
- use stable alias names for wire compatibility
- normalize nonblank identifiers explicitly
- prefer enums/literals over free-form strings for closed sets
- avoid unconstrained `dict[str, Any]` except for clearly-scoped `metadata` bags or explicitly versioned escape hatches

### 4.3 No anonymous JSON islands

Do not hide business fields inside `parameters`, `options`, or `payload` blobs when those fields are known and stable. If a field matters to workflow logic, idempotency, billing, routing, or operator understanding, it should have a named field in the model.

### 4.4 No generic type variables at Temporal boundaries

Use concrete models and concrete collection element types. Do not rely on unresolved generics in workflow/activity signatures.

---

## 5. Activities

### 5.1 Public activity signatures

New Activities should accept a single typed request model as the public argument. Activities that return structured data should return a named typed model, not a dict assembled ad hoc.

### 5.2 Activity type names stay stable

Type-safety work must not rename activity types to “clean things up.” The activity type string is a long-lived Temporal contract. Change payload shape compatibly or introduce a separately-versioned activity with an explicit migration plan.

### 5.3 Workflow call sites must be typed

Workflow code should construct typed request models at the call site, not inline dicts. Use `moonmind/workflows/temporal/typed_execution.py` overloads or equivalent thin facades so pyright/mypy can see the real contract.

### 5.4 Provider-specific data stops at the activity boundary

Activities may interact with provider-shaped payloads internally, but their workflow-facing return shape must be MoonMind canonical contracts such as `AgentRunHandle`, `AgentRunStatus`, `AgentRunResult`, `CodexManagedSessionHandle`, `CodexManagedSessionTurnResponse`, and `CodexManagedSessionSummary`.

### 5.5 Compatibility shims are narrow and temporary

If an Activity must continue to accept legacy dict payloads for replay or in-flight compatibility, the shim must:

- exist only at the public entry boundary
- validate/coerce immediately into the canonical model
- be documented as compatibility behavior, not permanent business logic
- be removed only after replay/in-flight safety is addressed

---

## 6. Workflow run / Continue-As-New inputs

### 6.1 Typed workflow input is mandatory

Every workflow `run` method must have a named typed input model. For Python workflows that handle messages, the constructor must be annotated as a workflow initializer and take the same input model as the `run` method so state exists before messages are processed.

### 6.2 Continue-As-New payloads are first-class contracts

Continue-As-New input is not an internal scratch dict. It must be the same workflow input model or a dedicated typed continuation model. Fields carried across runs must be intentional, minimal, and compatibility-reviewed.

### 6.3 No opaque continuation bags

Do not stash arbitrary JSON in Continue-As-New payloads. If state is important enough to survive Continue-As-New, it is important enough to be explicitly modeled.

---

## 7. Signals, Updates, and Queries

### 7.1 Use the right Temporal primitive

- **Update**: synchronous mutation that must be accepted/rejected and may return a result.
- **Signal**: fire-and-forget notification or asynchronous instruction where the caller does not wait for workflow-side completion.
- **Query**: read-only state projection.

Do not use Signals to emulate request/response RPC when Update semantics are the real need.

### 7.2 Typed message contracts

Every Signal, Update, and Query must have a named request/response model. New public handlers must not accept `dict[str, Any] | None` as their canonical interface.

### 7.3 Validators are mandatory for mutating Updates

Every public mutating Update must have a validator that:

- validates the typed request shape
- rejects stale epochs, illegal states, or duplicate misuse before history acceptance
- avoids side effects and blocking work

### 7.4 Queries return typed snapshots

Queries should return a named snapshot/projection model. Converting that model to a JSON-friendly dict at the outermost edge is acceptable if transport requires it, but the canonical workflow-side projection must still be typed.

### 7.5 Catch-all control messages are compatibility shims only

Generic messages like `{"action": ...}` envelopes are not an approved target-state public API. If legacy catch-all messages remain for compatibility or internal choreography, they must be clearly marked as shims. New client-facing control surfaces must expose one explicit Update or Signal per operation.

### 7.6 Internal signals still need typed envelopes

Internal-only or child-workflow Signals are allowed when Signal semantics are the right fit, but they should still use dedicated typed envelopes. “Internal” is not a license to use untyped dicts.

---

## 8. Managed-session-specific rules

The managed session plane is the highest-value Temporal message surface in MoonMind and therefore sets the standard for future session and runtime flows.

### 8.1 One operation, one request model

Send follow-up, interrupt, steer, clear, cancel, terminate, attach-runtime-handles, and any future managed-session control must each have an explicit named request model. Reusing a generic control bag across unrelated operations is not allowed as the steady state.

### 8.2 Epoch-aware control

Mutating session controls that target runtime state must carry and validate `sessionEpoch` when stale control would be unsafe. Epoch changes must be explicit in typed session-state transitions.

### 8.3 Idempotent update tracking

Mutating Updates should dedupe by Temporal Update ID and/or caller-supplied request ID. Tracking state that survives Continue-As-New must be typed and bounded.

### 8.4 Serialized mutation handling

If Update or Signal handlers can block on Activities or wait conditions, the workflow must serialize conflicting mutations with a workflow-safe concurrency primitive or by queueing work through the main `run` loop. Managed-session controls must never rely on “single-threaded means race-free” as a substitute for deliberate serialization.

### 8.5 Typed session snapshots

Workflow-owned session status must be represented by an explicit snapshot model. Operator-visible fields, search-attribute-relevant fields, and continuation state must come from typed workflow state, not reconstructed ad hoc at query time from loose dicts.

---

## 9. Binary, large payload, and serialization policy

### 9.1 Raw nested bytes are not an approved wire shape

MoonMind must not rely on raw `bytes` embedded inside arbitrary JSON/dict-shaped Temporal payloads. Approved options are:

- explicit base64-serialized fields on typed models
- top-level `bytes` payloads when the boundary is truly a bytes contract
- artifact references / claim-check style references for larger data

### 9.2 Artifacts over history

Large text, large structured data, transcripts, summaries, checkpoints, diagnostics, and binary outputs belong in the artifact system or external storage, with Temporal carrying compact refs and metadata.

### 9.3 Serializer behavior must be intentional

If a field needs special JSON behavior, it must use an explicit serializer/validator or a project-standard payload converter. MoonMind must never depend on accidental coercions from a generic JSON encoder.

---

## 10. Compatibility and evolution rules

### 10.1 Additive-first evolution

Safe changes are generally:

- adding optional fields with defaults
- widening enums only when callers/handlers can tolerate unknown future values
- dual-reading legacy aliases or old shapes at the boundary

Unsafe changes requiring explicit migration planning:

- renaming or removing fields
- changing the meaning of an existing field
- changing a workflow’s message ordering or branching in a replay-visible way
- changing activity/workflow names in place

### 10.2 Workflow code changes need deployment safety

When workflow code changes affect replay-visible behavior, protect rollout with Worker Versioning or patching. Type-safety refactors are not exempt from determinism rules.

### 10.3 Compatibility logic stays at the edge

If old and new shapes must coexist, the dual-read/normalize step happens at the public Temporal boundary and then hands off the canonical typed model to the rest of the codebase.

---

## 11. Testing and tooling requirements

Type safety is not considered complete without verification at four layers.

### 11.1 Schema tests

Boundary models need focused validation tests for:

- required fields
- aliases
- normalization
- enum handling
- binary serialization
- explicit rejection of unknown fields where applicable

### 11.2 Workflow boundary round-trip tests

Use real Temporal test execution to verify the full path:

`typed model -> payload conversion -> workflow/activity boundary -> typed model/result`

### 11.3 Replay and in-flight compatibility tests

Any change to workflow code, message shape, or Continue-As-New state must have replay coverage against representative histories when compatibility is a concern.

### 11.4 Static analysis

Temporal workflow and activity modules should be in strict pyright/mypy coverage. Regressions such as raw dict execute-activity payloads, untyped `Any` leaks, or provider-shaped return dicts should fail review and, where practical, CI.

---

## 12. Approved escape hatches

The following are allowed only with explicit comments and compatibility justification:

- boundary methods that temporarily accept `Mapping[str, Any] | Model`
- legacy Signal/Update envelopes retained solely for replay or live in-flight executions
- bounded `metadata: dict[str, Any]` bags for provider or operator annotations that are not workflow-control fields

These escape hatches are transitional mechanisms, not the default architecture.

---

## 13. Anti-patterns

The following are explicitly discouraged:

- `workflow.execute_activity("some.activity", {"foo": bar, "baz": qux})`
- public workflow handlers that accept raw dicts as their canonical interface
- generic `{"action": ...}` control envelopes for new public APIs
- nested raw `bytes` inside JSON-shaped payloads
- returning provider-specific top-level dicts from Activities to workflows
- using `Any` where a closed model or enum exists
- storing large conversational state directly in workflow history when an artifact ref would do

---

## 14. Canonical implementation anchors

The following modules are the expected homes for this policy:

- `moonmind/schemas/temporal_activity_models.py`
- `moonmind/schemas/managed_session_models.py`
- `moonmind/workflows/temporal/typed_execution.py`
- `moonmind/workflows/temporal/activity_catalog.py`
- `moonmind/workflows/temporal/workflows/agent_session.py`
- `docs/Temporal/ActivityCatalogAndWorkerTopology.md`

Implementation tracking for work that still lags this target state belongs in `docs/Temporal/TemporalTypeSafety.md`, not in this document.
