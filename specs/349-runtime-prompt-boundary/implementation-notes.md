# Implementation Notes: Runtime Prompt Boundary

## Scope

MoonSpec story: `specs/349-runtime-prompt-boundary/spec.md`
Jira issue: `MM-650`
Source design: `DESIGN-REQ-026` from `docs/Tasks/TaskArchitecture.md` section 10.

## Red-First Evidence

### Unit red run

Command:

```bash
./tools/test_unit.sh tests/unit/workflows/tasks/test_prepared_context.py tests/unit/workflows/adapters/test_target_aware_prepared_context.py tests/unit/workflows/adapters/test_base_external_agent_adapter.py tests/unit/workflows/adapters/test_openclaw_agent_adapter.py tests/unit/agents/codex_worker/test_worker.py tests/unit/workflows/temporal/workflows/test_run_target_aware_inputs.py
```

Result: FAIL before production changes.

Expected failures:
- `tests/unit/workflows/temporal/workflows/test_run_target_aware_inputs.py::test_run_request_filters_prepared_context_to_current_step`
- `tests/unit/workflows/temporal/workflows/test_run_target_aware_inputs.py::test_external_request_keeps_generated_context_out_of_adapter_input_refs`

Reason: external runtime requests still included `prepared-context://...` generated context refs in adapter `input_refs` instead of raw artifact refs only.

### Integration red run

Command:

```bash
pytest tests/integration/workflows/temporal/workflows/test_run_target_aware_inputs.py -q
```

Result: FAIL before production changes.

Expected failure:
- `tests/integration/workflows/temporal/workflows/test_run_target_aware_inputs.py::test_external_runtime_receives_raw_refs_without_context_refs`

Reason: external runtime workflow-boundary request included generated `prepared-context://...` refs in adapter `input_refs`.

## Implementation

Changed files:
- `moonmind/workflows/tasks/prepared_context.py`: added `merge_prepared_raw_input_refs()` so adapter-facing refs can merge explicit node refs with selected raw artifact refs only.
- `moonmind/workflows/tasks/__init__.py`: exported `merge_prepared_raw_input_refs()`.
- `moonmind/workflows/temporal/workflows/run.py`: changed external runtime request construction to use raw prepared refs for `input_refs` while preserving full prepared context metadata.
- `tests/unit/workflows/temporal/workflows/test_run_target_aware_inputs.py`: updated unit boundary expectations and added external raw-ref coverage.
- `tests/integration/workflows/temporal/workflows/test_run_target_aware_inputs.py`: added external workflow-boundary raw-ref coverage.
- `tests/unit/workflows/adapters/test_base_external_agent_adapter.py`: added adapter metadata preservation coverage.
- `tests/unit/workflows/adapters/test_openclaw_agent_adapter.py`: added provider translation coverage proving raw refs only appear in adapter user content.

Conditional fallback tasks:
- T018 executed because red tests exposed mixed generated/raw refs for external runtime modes.
- T019 skipped because missing-preparation diagnostics already remained explicit in existing tests.
- T024 skipped because text-first `INPUT ATTACHMENTS` behavior continued to pass.
- T025 skipped because target-kind validation already remained restricted to `objective` and `step`.

## Focused Green Evidence

### Focused unit command

```bash
./tools/test_unit.sh tests/unit/workflows/tasks/test_prepared_context.py tests/unit/workflows/adapters/test_target_aware_prepared_context.py tests/unit/workflows/adapters/test_base_external_agent_adapter.py tests/unit/workflows/adapters/test_openclaw_agent_adapter.py tests/unit/agents/codex_worker/test_worker.py tests/unit/workflows/temporal/workflows/test_run_target_aware_inputs.py
```

Result: PASS.

Evidence:
- Python focused tests: 211 passed.
- Frontend suite invoked by `./tools/test_unit.sh`: 20 files passed, 343 passed, 229 skipped.

### Focused integration command

```bash
pytest tests/integration/workflows/temporal/workflows/test_run_target_aware_inputs.py -q
```

Result: PASS, 5 passed.

## Traceability

- `MM-650` preserved in spec, plan, research, data model, contract, quickstart, tasks, and these implementation notes.
- `DESIGN-REQ-026` covered by tests for text-first context, multimodal/external raw refs, target-binding preservation, and adapter guardrails.

## Full Validation Evidence

### Full unit suite

Command:

```bash
./tools/test_unit.sh
```

Result: PASS.

Evidence:
- Python unit suite: 4973 passed, 1 xpassed, 16 subtests passed.
- Frontend unit suite: 20 files passed, 343 passed, 229 skipped.

### Required hermetic integration suite

Command:

```bash
./tools/test_integration.sh
```

Result: BLOCKED in this managed runtime.

Blocker:
- Docker daemon returned `403 Forbidden` under administrative rules while building/running the compose-backed pytest image.

Fallback evidence:
- Focused hermetic-marked workflow-boundary test command passed: `pytest tests/integration/workflows/temporal/workflows/test_run_target_aware_inputs.py -q` with 5 passed.

### Traceability check

Command:

```bash
rg -n "MM-650|DESIGN-REQ-026|original Jira preset brief" specs/349-runtime-prompt-boundary
```

Result: PASS. Traceability is preserved across feature artifacts.

## Final Verification Status

`/moonspec-verify` was not run in this implementation step because the active skill snapshot for this run contains only `moonspec-implement`; Skills On Demand is disabled. Run the dedicated `moonspec-verify` step next.
