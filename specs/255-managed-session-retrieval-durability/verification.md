# MM-507 Verification Notes

## Scope

Story: Preserve durable retrieval truth across session resets.
Issue: `MM-507`

## Red-First Evidence

The following targeted tests were added first and confirmed failing before production changes:

- `pytest -q tests/unit/rag/test_context_injection.py -k durable_authority --tb=short`
  Failure before code change: `KeyError: 'latestContextPackRef'` because retrieval metadata did not publish an explicit latest durable context ref or authority markers.
- `pytest -q tests/unit/services/temporal/runtime/test_managed_session_controller.py -k preserves_retrieval_metadata_in_durable_outputs --tb=short`
  Failure before code change: `CodexManagedSessionRecord` rejected retrieval metadata because the durable managed-session record had no bounded metadata field for retrieval continuity refs.
- `pytest -q tests/integration/workflows/temporal/test_managed_session_retrieval_durability.py --tb=short`
  Failure before code change: the reset-boundary path could not preserve or republish the latest context-pack ref through durable session artifacts.

## Implemented Contract

- Retrieval publication now records explicit bounded durability markers in request metadata:
  - `latestContextPackRef`
  - `retrievalDurabilityAuthority=artifact_ref`
  - `sessionContinuityCacheStatus=advisory_only`
- Managed Codex session launch requests now carry compact retrieval durability metadata extracted from the runtime request.
- Durable managed-session records now persist bounded retrieval metadata across launch, clear/reset, and reconcile flows.
- Reset-boundary artifacts now embed the persisted bounded retrieval metadata so the latest durable context ref remains recoverable after a session epoch change.
- Managed-session summary/publication responses now expose the same bounded retrieval metadata for downstream recovery or verification.

## Passing Evidence

Focused unit verification:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh \
  tests/unit/rag/test_context_pack.py \
  tests/unit/rag/test_context_injection.py \
  tests/unit/services/temporal/runtime/test_managed_session_controller.py \
  tests/unit/services/temporal/runtime/test_launcher.py \
  tests/unit/workflows/temporal/runtime/strategies/test_remaining_strategies.py \
  tests/unit/workflows/temporal/test_agent_runtime_activities.py \
  tests/unit/workflows/adapters/test_codex_session_adapter.py \
  tests/unit/schemas/test_managed_session_models.py
```

Observed result:
- Python suite: `307 passed`
- Frontend suite invoked by the canonical runner: `420 passed`

Focused integration verification:

```bash
pytest tests/integration/workflows/temporal/test_managed_session_retrieval_durability.py -q --tb=short
```

Observed result:
- `1 passed`

## Traceability

`rg -n "MM-507" specs/255-managed-session-retrieval-durability` confirms the issue key remains preserved in the active MoonSpec artifacts.

## Remaining Work

- `tasks.md` still contains unchecked follow-up items for optional or broader coverage expansions.
- `/moonspec-verify` was not run in this implementation turn.
