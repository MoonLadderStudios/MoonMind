# MM-508 Verification Notes

## Scope

Story: Separate retrieval configuration from provider profiles and support direct, gateway, and fallback retrieval modes.
Issue: `MM-508`

This verification note records the red-first and green verification evidence for the implemented FR-004 / DESIGN-REQ-014 slice only. Broader MM-508 transport-separation work remains open in `tasks.md`.

## Red-First Evidence

The degraded local-fallback slice was verified red before production changes:

- `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/rag/test_context_injection.py`
  - Failed before code changes with:
    - `AssertionError` because the injected instruction did not include `Retrieved context mode: degraded local fallback`
    - `KeyError: 'retrievalMode'` because compact retrieval metadata did not publish the degraded or semantic mode marker
    - `TypeError` because `_record_context_metadata()` did not accept a `degraded_reason`
- `pytest -q tests/integration/workflows/temporal/test_managed_session_retrieval_context.py -k local_fallback`
  - Failed before code changes with `KeyError: 'retrievalMode'` because the managed-runtime boundary did not expose degraded fallback metadata after local fallback activation

## Implemented Contract

- `moonmind/rag/context_injection.py` now records `retrievalMode` for compact retrieval metadata.
- Local fallback now records `retrievalMode=degraded_local_fallback`.
- Local fallback now records `retrievalDegradedReason` when the fallback was triggered by an explicit retrieval skip reason or retrieval error.
- Non-fallback retrieval paths now record `retrievalMode=semantic`.
- Injected instructions now include `Retrieved context mode: degraded local fallback` whenever the resolved transport is `local_fallback`.
- `moonmind/schemas/agent_runtime_models.py` now preserves `retrievalMode` and `retrievalDegradedReason` in bounded durable retrieval metadata extraction.

## Passing Evidence

- `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/rag/test_context_injection.py`
  - PASS: `11 passed`
  - The canonical runner also executed frontend verification: `420 passed`
- `pytest -q tests/integration/workflows/temporal/test_managed_session_retrieval_context.py -k local_fallback`
  - PASS: `1 passed, 3 deselected`

## Traceability

- `MM-508` remains preserved in `spec.md`, `plan.md`, `tasks.md`, and this verification note.
- This implemented slice covers FR-004, SC-004, and DESIGN-REQ-014.

## Remaining Work

- FR-001, FR-002, FR-003, FR-005, and FR-006 implementation tasks remain open.
- Final story validation, broader unit/integration coverage, and `/moonspec-verify` remain open.
