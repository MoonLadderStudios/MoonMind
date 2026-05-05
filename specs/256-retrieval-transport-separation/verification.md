# MM-508 Verification Notes

## Scope

Story: Separate retrieval configuration from provider profiles and support direct, gateway, and fallback retrieval modes.
Issue: `MM-508`

This verification note records the red-first and green verification evidence for MM-508 retrieval transport separation, including the earlier degraded local-fallback slice and the completed gateway/direct/profile-separation slice implemented for MM-487.

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
- `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/rag/test_settings.py tests/unit/rag/test_service.py tests/unit/rag/test_context_injection.py tests/unit/api/routers/test_retrieval_gateway.py tests/unit/services/temporal/runtime/test_launcher.py tests/unit/workflows/temporal/test_agent_runtime_activities.py tests/unit/api_service/api/routers/test_provider_profiles.py`
  - PASS: Python `221 passed`; frontend `307 passed | 223 skipped`.
- `pytest -q tests/integration/workflows/temporal/test_managed_session_retrieval_context.py -q --tb=short`
  - PASS: `4 passed`.

## Traceability

- `MM-508` remains preserved in `spec.md`, `plan.md`, `tasks.md`, and this verification note.
- The completed implementation covers FR-001 through FR-006, SC-001 through SC-006, and DESIGN-REQ-004, DESIGN-REQ-009, DESIGN-REQ-010, DESIGN-REQ-014, DESIGN-REQ-016, DESIGN-REQ-019, DESIGN-REQ-024, and DESIGN-REQ-025.

## Implemented Gateway / Transport Contract

- `moonmind/rag/settings.py` now requires a scoped `MOONMIND_RETRIEVAL_TOKEN` for gateway execution and reports `retrieval_gateway_auth_missing` when a managed runtime has a gateway URL without auth.
- `moonmind/rag/service.py` sends `X-MoonMind-Retrieval-Token` to the gateway, preserving query, filters, top-k, overlay policy, and token/latency budgets.
- `api_service/api/routers/retrieval_gateway.py` accepts OIDC or scoped retrieval-token auth, rejects removed worker-token auth with `410 Gone`, and optionally enforces `MOONMIND_RETRIEVAL_ALLOWED_REPOSITORIES`.
- Codex and Claude runtime strategies now pass run-scoped materialized environment into context injection, so retrieval configuration resolves from MoonMind retrieval settings instead of runtime provider-profile semantics.
- `docs/Rag/WorkflowRag.md` is updated from Draft to Implemented and documents the scoped gateway auth settings.

## Remaining Work

- Full `/moonspec-verify` command was not available as a CLI in this managed runtime; verification evidence above covers the targeted MM-508 unit and workflow-boundary suites.
