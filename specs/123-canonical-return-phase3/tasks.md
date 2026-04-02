# Tasks: Canonical Return — Phase 3 (Managed Runtime Activities)

**Branch**: `123-canonical-return-phase3` | **Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

## Summary

TDD implementation of Phase 3: canonicalize managed runtime activities to return typed Pydantic contracts directly.

---

## Task 1: Write failing TDD tests for `agent_runtime_status`

**File**: `tests/unit/workflows/temporal/test_agent_runtime_activities.py` (NEW)

- [ ] T1.1 — Test: `agent_runtime_status` with running record returns `AgentRunStatus` (isinstance check), `status == "running"`, `agentKind == "managed"`
- [ ] T1.2 — Test: `agent_runtime_status` with completed record returns `AgentRunStatus` with `status == "completed"`
- [ ] T1.3 — Test: `agent_runtime_status` with failed record returns `AgentRunStatus` with `status == "failed"`, `metadata` contains `runtimeId`
- [ ] T1.4 — Test: `agent_runtime_status` with no record returns `AgentRunStatus` with `status == "running"` (optimistic stub)
- [ ] T1.5 — Test: `agent_runtime_status` with missing/empty `run_id` raises `TemporalActivityRuntimeError`

**Validation**: Run `./tools/test_unit.sh` and confirm T1.1–T1.5 FAIL (typed return not yet in place).

---

## Task 2: Write failing TDD tests for `agent_runtime_cancel`

**File**: `tests/unit/workflows/temporal/test_agent_runtime_activities.py`

- [ ] T2.1 — Test: `agent_runtime_cancel` with managed supervisor configured returns `AgentRunStatus` with `status == "canceled"`, `agentKind == "managed"`
- [ ] T2.2 — Test: `agent_runtime_cancel` without supervisor (store-only path) returns `AgentRunStatus` with `status == "canceled"`
- [ ] T2.3 — Test: `agent_runtime_cancel` for unknown run (no supervisor, no store match) returns `AgentRunStatus` with `status == "canceled"` (best-effort)

**Validation**: Run `./tools/test_unit.sh` and confirm T2.1–T2.3 FAIL.

---

## Task 3: Write failing TDD tests for `agent_runtime_publish_artifacts`

**File**: `tests/unit/workflows/temporal/test_agent_runtime_activities.py`

- [ ] T3.1 — Test: `agent_runtime_publish_artifacts` without artifact service configured returns the input `AgentRunResult` unchanged (passthrough)
- [ ] T3.2 — Test: `agent_runtime_publish_artifacts` with a mock artifact service and typed `AgentRunResult` returns typed `AgentRunResult` with `diagnostics_ref` populated (isinstance + attribute check)
- [ ] T3.3 — Test: `agent_runtime_publish_artifacts` with `None` input returns `None` (no-op path)
- [ ] T3.4 — Test: `agent_runtime_publish_artifacts` artifact write failure falls back gracefully and returns the original result unchanged

**Validation**: Run `./tools/test_unit.sh` and confirm T3.1–T3.3 FAIL.

---

## Task 4: Update existing `test_agent_runtime_fetch_result.py` for typed return

**File**: `tests/unit/workflows/temporal/test_agent_runtime_fetch_result.py` (MODIFY)

- [ ] T4.1 — Update all `result["failureClass"]` → `result.failure_class` (attribute access)
- [ ] T4.2 — Update all `result["providerErrorCode"]` → `result.provider_error_code`
- [ ] T4.3 — Update all `result["summary"]` → `result.summary`
- [ ] T4.4 — Add `isinstance(result, AgentRunResult)` assertions to each test case

**Validation**: Run `./tools/test_unit.sh` — tests should still FAIL (production code not yet changed).

---

## Task 5: Write new TDD tests for `agent_runtime_fetch_result` typed return

**File**: `tests/unit/workflows/temporal/test_agent_runtime_activities.py`

- [ ] T5.1 — Test: successful completed run returns `AgentRunResult` with `failure_class is None`
- [ ] T5.2 — Test: failed run returns `AgentRunResult` with `failure_class == "execution_error"`
- [ ] T5.3 — Test: canceled run returns `AgentRunResult` with `failure_class` or status info
- [ ] T5.4 — Test: no record in store returns empty `AgentRunResult` (isinstance still holds)
- [ ] T5.5 — Test: missing `run_id` raises `TemporalActivityRuntimeError`

---

## Task 6: Implement — update `agent_runtime_status` return type

**File**: `moonmind/workflows/temporal/activity_runtime.py`

- [ ] T6.1 — Change return annotation: `async def agent_runtime_status(...) -> AgentRunStatus:`
- [ ] T6.2 — Replace `return status.model_dump(mode="json", by_alias=True)` with `return status` (return model directly)
- [ ] T6.3 — Run `./tools/test_unit.sh` — T1.1–T1.4 tests should now PASS; T1.5 may already pass

---

## Task 7: Implement — update `agent_runtime_cancel` return type

**File**: `moonmind/workflows/temporal/activity_runtime.py`

- [ ] T7.1 — Change return annotation: `async def agent_runtime_cancel(...) -> AgentRunStatus:`
- [ ] T7.2 — After supervisor cancellation (success path): build and return `AgentRunStatus(runId=run_id, agentKind="managed", agentId=agent_kind, status="canceled")`
- [ ] T7.3 — After supervisor cancellation (exception path): return `AgentRunStatus(runId=run_id, agentKind="managed", agentId=agent_kind, status="canceled")` (best-effort)
- [ ] T7.4 — Store-based cancel path: return `AgentRunStatus(runId=run_id, agentKind="managed", agentId="managed", status="canceled")`
- [ ] T7.5 — External/unknown kind path: return `AgentRunStatus(runId=str(run_id), agentKind=agent_kind, agentId=agent_kind, status="canceled")` (best-effort)
- [ ] T7.6 — Run `./tools/test_unit.sh` — T2.1–T2.3 tests should now PASS

---

## Task 8: Implement — update `agent_runtime_fetch_result` return type

**File**: `moonmind/workflows/temporal/activity_runtime.py`

- [ ] T8.1 — Change return annotation: `async def agent_runtime_fetch_result(...) -> AgentRunResult:`
- [ ] T8.2 — Refactor metadata enrichment: instead of `result.model_dump()` → dict mutation → return dict, use `result.model_copy(update={"metadata": merged_meta})` → return model
- [ ] T8.3 — Ensure `push_info` enrichment works via `model_copy` on the metadata dict
- [ ] T8.4 — Ensure `pr_url` enrichment works via `model_copy`
- [ ] T8.5 — Remove final `return result_dict` → replace with `return enriched_result`
- [ ] T8.6 — Run `./tools/test_unit.sh` — all T4 and T5 tests should PASS

---

## Task 9: Implement — update `agent_runtime_publish_artifacts` return type

**File**: `moonmind/workflows/temporal/activity_runtime.py`

- [ ] T9.1 — Change return annotation: `async def agent_runtime_publish_artifacts(self, result: AgentRunResult | None = None, /) -> AgentRunResult | None:`
- [ ] T9.2 — Remove `isinstance(result, Mapping)` / `hasattr(result, "model_dump")` branching; accept typed `AgentRunResult | None` directly
- [ ] T9.3 — Use `result.model_copy(update={"diagnostics_ref": summary_ref.artifact_id})` to enrich with diagnostics ref
- [ ] T9.4 — Preserve: if `result is None`, return `None`; if artifact service is `None`, return `result` unchanged
- [ ] T9.5 — Run `./tools/test_unit.sh` — T3.1–T3.3 tests should now PASS

---

## Task 10: Final full test run and regression check

- [ ] T10.1 — Run `./tools/test_unit.sh` — full suite must pass with zero failures
- [ ] T10.2 — Verify no `dict[str, Any]` return annotation remains on the 4 target methods: `grep -n "-> dict\[str, Any\]\|-> None" moonmind/workflows/temporal/activity_runtime.py | grep "agent_runtime"`
- [ ] T10.3 — Verify no provider-specific top-level fields leak from managed runtime activities: `grep -n "external_id\|tracking_ref\|provider_status" moonmind/workflows/temporal/activity_runtime.py`
- [ ] T10.4 — Update `docs/tmp/010-CanonicalReturnPlan.md` Phase 3 task checkboxes to `[x]`

---

## Task 11: Create PR

- [ ] T11.1 — `git add -A && git commit -m "feat: canonicalize managed runtime activities to return typed contracts (Phase 3)"`
- [ ] T11.2 — `git push -u origin 123-canonical-return-phase3`
- [ ] T11.3 — Create PR targeting `main` with summary of changes and test coverage

---

## Completion Criteria

All tasks marked `[X]`. `./tools/test_unit.sh` green. Return annotations on `agent_runtime_status`, `agent_runtime_fetch_result`, `agent_runtime_cancel`, and `agent_runtime_publish_artifacts` are typed Pydantic models.
