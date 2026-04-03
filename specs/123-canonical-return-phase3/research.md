# Research: Canonical Return Phase 3

**Branch**: `123-canonical-return-phase3` | **Date**: 2026-04-02

## Findings

### Decision: Return typed models directly from managed runtime activities

**Decision**: Change `agent_runtime_status`, `agent_runtime_fetch_result`, `agent_runtime_cancel`, and `agent_runtime_publish_artifacts` to return typed Pydantic models (`AgentRunStatus`, `AgentRunResult`) instead of `dict[str, Any]` or `None`.

**Rationale**: Phase 2 established this pattern for all external provider activities (Jules, Codex Cloud, OpenClaw). The canonical contracts (`AgentRunStatus`, `AgentRunResult`) already exist in `moonmind/schemas/agent_runtime_models.py` and are validated Pydantic models. Temporal serializes them correctly to/from JSON automatically. All downstream workflow callers already use `_coerce_*` helpers that handle both old dict payloads and typed model payloads.

**Alternatives considered**: (1) Extract managed runtime activities into standalone module like Phase 2 — rejected because the managed runtime activities are deeply integrated with `TemporalAgentRuntimeActivities` state (run_store, run_supervisor, run_launcher) making extraction a Phase 4+ concern. (2) Use dict with TypedDict annotations — rejected per Constitution XIII (no half-measures; typed Pydantic is the target state documented in the canonical plan).

---

### Decision: `agent_runtime.launch` stays as dict return for Phase 3

**Decision**: `agent_runtime.launch` returns `dict[str, Any]` (the serialized `ManagedRunRecord`) and is NOT changed in Phase 3.

**Rationale**: Launch is a support/side-effect activity, not a status or result activity. It returns internal record state used by downstream activities, not a workflow-consumable run contract. The plan document explicitly notes: "Confirm whether `agent_runtime.launch` remains an internal launch/support activity or should also be wrapped more tightly around canonical handle semantics." The answer for Phase 3 is: yes, it remains internal and dict-shaped.

---

### Decision: No `workflow.patched` required for Phase 3

**Decision**: Phase 3 changes do not require `workflow.patched` versioning guards.

**Rationale**: Temporal serializes Pydantic models to dicts when transmitting activity results. Existing workflow code that calls `_coerce_managed_status_payload` / `_coerce_managed_fetch_result` will continue to work on both the old dict format (for history replay) and the new typed-model return (which Temporal serializes identically to the dict). The coercers are passive normalization layers — they tolerate both shapes. Phase 4 will evaluate whether `workflow.patched` is needed when the coercers are actually deleted.

---

### Finding: Gemini enrichment works on typed model

`_maybe_enrich_gemini_failure_result` already accepts `AgentRunResult` and returns `AgentRunResult`. The downstream `result_dict` assembly is the only change: instead of calling `result.model_dump()` and merging as a dict, metadata enrichment (push_info, pr_url) can use `result.model_copy(update={"metadata": {**result.metadata, ...}})`.

---

### Finding: Existing tests use dict key access on return values

The existing `test_agent_runtime_fetch_result.py` tests assert `result["failureClass"]`, `result["summary"]`, etc. These will break when `agent_runtime_fetch_result` returns a typed `AgentRunResult` instead of a dict. Options:

1. Update the tests to use `result.failure_class`, `result.summary` — breaking change for existing tests.
2. Keep the existing tests passing by keeping `agent_runtime_fetch_result` return as a dict (rejects the spec).
3. Update the existing tests to use typed attribute access — this is the correct approach per FR-010 ("only type annotations of the return may change").

**Decision**: Update the existing test assertions from dict-key access to attribute access. This is a test hygiene fix aligned with the new typed contract, not a semantic change. Field values remain identical.
