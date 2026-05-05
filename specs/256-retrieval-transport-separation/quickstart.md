# Quickstart: Retrieval Transport and Configuration Separation

## Goal

Verify MM-508 as a runtime Workflow RAG story: retrieval configuration stays separate from managed-runtime provider profiles, gateway retrieval is preferred when runtime embedding credentials should not be required, direct retrieval remains available when policy permits, local fallback stays explicitly degraded, and overlay or budget knobs remain coherent across supported transports.

Gateway-mode follow-up retrieval requires `MOONMIND_RETRIEVAL_URL` plus scoped `MOONMIND_RETRIEVAL_TOKEN` configuration. Optional `MOONMIND_RETRIEVAL_ALLOWED_REPOSITORIES` values constrain scoped-token requests at the gateway.

## Preconditions

- Work from the active feature directory `specs/256-retrieval-transport-separation`.
- Set `MOONMIND_FORCE_LOCAL_TESTS=1` for local unit verification in this managed runtime.
- Start with verification tests before changing production code because the repo already contains substantial retrieval settings and transport logic.
- Use existing retrieval and runtime boundary suites instead of inventing an ad hoc harness.

## Unit Test Strategy

Run the focused retrieval settings, service, gateway, and runtime-boundary suites first:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh \
  tests/unit/rag/test_settings.py \
  tests/unit/rag/test_service.py \
  tests/unit/rag/test_context_injection.py \
  tests/unit/api/routers/test_retrieval_gateway.py \
  tests/unit/services/temporal/runtime/test_launcher.py \
  tests/unit/workflows/temporal/test_agent_runtime_activities.py \
  tests/unit/api_service/api/routers/test_provider_profiles.py
```

Expected use:
- Add failing tests first for retrieval-setting separation, gateway preference, direct-path support, explicit degraded fallback, and cross-transport knob propagation.
- Preserve already-correct transport logic unless the new tests expose a real MM-508 gap.

## Integration Test Strategy

Preferred hermetic path when compatible with the final implementation:

```bash
./tools/test_integration.sh
```

If MM-508 needs focused runtime-boundary proof outside `integration_ci`, run targeted Temporal integration tests such as:

```bash
pytest tests/integration/workflows/temporal/test_managed_session_retrieval_context.py -q --tb=short
```

Expected use:
- Prove retrieval transport and profile ownership stay separate at the managed-runtime boundary.
- Prove gateway preference, direct availability, and degraded local fallback remain observable.
- Prove overlay, filter, top-k, and budget knobs stay coherent across supported transports.

## End-to-End Verification Flow

1. Run the focused unit suite.
2. Add failing verification tests for any MM-508 requirement that remains partial or unverified.
3. Implement only the production changes required to satisfy those failing tests.
4. Re-run the focused unit suite.
5. Run the integration path required by the implemented scope.
6. Confirm `spec.md`, `plan.md`, `research.md`, `data-model.md`, this quickstart, and the contract artifact preserve `MM-508`.

## Requirement-to-Test Guidance

- FR-001 / DESIGN-REQ-004 / DESIGN-REQ-019: verify retrieval settings remain distinct from provider-profile launch configuration.
- FR-002 / DESIGN-REQ-009 / DESIGN-REQ-024: verify gateway preference under missing runtime embedding credentials or MoonMind-owned outbound retrieval.
- FR-003 / DESIGN-REQ-010: verify direct retrieval remains available when policy and environment permit it.
- FR-004 / DESIGN-REQ-014: verify local fallback is gated, explicit, and degraded.
- FR-005 / DESIGN-REQ-016 / DESIGN-REQ-025: verify overlay, filter, and budget knobs remain coherent across transport paths.
- FR-006: verify retrieval ownership remains in the shared Workflow RAG contract rather than provider-profile semantics.
- FR-007: verify `MM-508` traceability remains present in planning and final verification artifacts.
